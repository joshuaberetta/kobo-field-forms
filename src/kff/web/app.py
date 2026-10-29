"""A local web interface: fetch forms, design and preview sheets, download PDFs, upload
scans and see how they registered.

Runs on localhost only (`kff web`). The Kobo token stays in this process's environment;
the browser never sees it. Requests must name a local Host, so a page on another site
can't reach the app through DNS rebinding.
"""

import base64
from dataclasses import fields
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from kff.config import Settings
from kff.errors import ConfigError, KffError
from kff.fetch import fetch_form, parse_asset_uid
from kff.form.model import translations
from kff.kobo.client import KoboClient
from kff.read.sheet import read_sheet
from kff.read.vlm import VlmConfig, VlmReader
from kff.render.options import CHOICE_LAYOUTS, LANGUAGE_LAYOUTS, PAPERS, TEXT_ENTRIES, RenderOptions
from kff.render.pdf import Sheet, merge_pdfs, render_png, render_set
from kff.scan.extract import crop_image, draw_overlay
from kff.scan.pipeline import scan_file, sheet_status
from kff.store import FormVersion, Store, new_batch_id
from kff.submit import assemble, submit_sheet

HERE = Path(__file__).parent
MAX_UPLOAD_BYTES = 50_000_000
PREVIEW_PPI = 72
LOCAL_HOSTS = ["127.0.0.1", "localhost", "::1", "testserver"]


def _png_data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def options_from(data: dict[str, Any]) -> RenderOptions:
    """RenderOptions from the designer's JSON (checkboxes arrive as booleans)."""
    known = {f.name: f.type for f in fields(RenderOptions)}
    clean: dict[str, Any] = {}
    for key, value in data.items():
        if key not in known or value is None or value == "":
            continue
        if key in ("columns", "repeat_rows"):
            value = int(value)
        elif key == "font_size":
            value = float(value)
        elif key == "languages":
            value = tuple(v for v in value if v)
        clean[key] = value
    return RenderOptions(**clean)


def create_app(settings: Settings | None = None, extra_hosts: list[str] | None = None) -> FastAPI:
    settings = settings or Settings.from_env(need_token=False)
    app = FastAPI(title="kobo-field-forms", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS + (extra_hosts or []))
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    batches_dir = settings.home / "batches"

    def store() -> Store:
        return Store(settings.home)

    def live_settings() -> Settings:
        """The settings `kff web` started with: the token comes from its environment then,
        never from a request."""
        if not settings.token:
            raise ConfigError("KOBO_TOKEN wasn't set when kff web started.")
        return settings

    def get_form(asset_uid: str, version_uid: str) -> FormVersion:
        with store() as s:
            form = s.get_form(settings.server, asset_uid, version_uid)
        if form is None:
            raise HTTPException(404, "That form version isn't in the store.")
        return form

    @app.exception_handler(KffError)
    async def kff_error(request: Request, err: KffError) -> JSONResponse:
        return JSONResponse({"error": str(err)}, status_code=400)

    # -- pages --------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        """The overview for people using it: what it does, how, with screenshots."""
        return templates.TemplateResponse(
            request, "home.html", {"pages": False, "static": "/static", "home": "/"}
        )

    @app.get("/forms", response_class=HTMLResponse)
    def index(request: Request, error: str | None = None) -> HTMLResponse:
        with store() as s:
            forms = list(s.list_forms(settings.server))
            batches = s.batches()
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "forms": forms,
                "batches": batches,
                "server": settings.server,
                "has_token": bool(settings.token),
                "error": error,
            },
        )

    @app.post("/fetch")
    def fetch(project: Annotated[str, Form()]) -> Response:
        try:
            live = live_settings()
            uid = parse_asset_uid(project)
            with KoboClient(live) as client, store() as s:
                form = s.save_form(fetch_form(client, live.server, uid))
        except (KffError, ConfigError) as err:
            return RedirectResponse(f"/forms?error={_q(str(err))}", status_code=303)
        return RedirectResponse(f"/forms/{form.asset_uid}/{form.version_uid}", status_code=303)

    @app.get("/forms/{asset_uid}/{version_uid}", response_class=HTMLResponse)
    def designer(request: Request, asset_uid: str, version_uid: str) -> HTMLResponse:
        form = get_form(asset_uid, version_uid)
        langs = [name or "" for name in translations(form.content)]
        return templates.TemplateResponse(
            request,
            "designer.html",
            {
                "form": form,
                "languages": langs,
                "papers": list(PAPERS),
                "language_layouts": LANGUAGE_LAYOUTS,
                "choice_layouts": CHOICE_LAYOUTS,
                "text_entries": TEXT_ENTRIES,
                "defaults": RenderOptions(),
            },
        )

    @app.get("/scans", response_class=HTMLResponse)
    def scans_page(request: Request) -> HTMLResponse:
        with store() as s:
            scans = s.scans()
            statuses = {st.sheet_id: st for st in sheet_status(s, [x["sheet_id"] for x in scans])}
        return templates.TemplateResponse(
            request,
            "scans.html",
            {"scans": scans, "statuses": statuses, "code": lambda sid: Sheet(sid, 0).code},
        )

    # -- API ----------------------------------------------------------------

    @app.post("/api/forms/{asset_uid}/{version_uid}/preview")
    def preview(asset_uid: str, version_uid: str, body: dict[str, Any]) -> dict[str, Any]:
        form = get_form(asset_uid, version_uid)
        options = options_from(body.get("options") or {})
        rendered = render_set(form, options)
        out = []
        for r in rendered:
            out.append(
                {
                    "layout_hash": r.layout_hash,
                    "languages": [lang.name for lang in r.model.languages],
                    "pages_per_sheet": r.pages_per_sheet,
                    "paper": r.layout["paper"],
                    "pages": [_png_data_url(p) for p in render_png(r, PREVIEW_PPI)],
                    "marks": [
                        {k: m[k] for k in ("page", "x", "y", "w", "h", "k")}
                        for m in r.layout["marks"]
                    ],
                }
            )
        warnings = list(dict.fromkeys(w for r in rendered for w in r.model.warnings))
        return {"layouts": out, "warnings": warnings}

    @app.post("/api/forms/{asset_uid}/{version_uid}/render")
    def render_batch(asset_uid: str, version_uid: str, body: dict[str, Any]) -> dict[str, Any]:
        form = get_form(asset_uid, version_uid)
        options = options_from(body.get("options") or {})
        copies = int(body.get("copies") or 1)
        rendered = render_set(form, options, copies=copies)
        batch = new_batch_id()
        batches_dir.mkdir(parents=True, exist_ok=True)
        (batches_dir / f"{batch}.pdf").write_bytes(merge_pdfs([r.pdf for r in rendered]))
        with store() as s:
            for r in rendered:
                s.save_layout(r.layout)
                s.save_sheets(r.layout_hash, batch, [sh.id for sh in r.sheets])
        return {
            "batch": batch,
            "url": f"/batches/{batch}.pdf",
            "sheets": copies * len(rendered),
            "pages": sum(copies * r.pages_per_sheet for r in rendered),
        }

    @app.get("/batches/{batch}.pdf")
    def batch_pdf(batch: str) -> FileResponse:
        path = (batches_dir / f"{batch}.pdf").resolve()
        if path.parent != batches_dir.resolve() or not path.is_file():
            raise HTTPException(404, "No such batch PDF (batches rendered by the CLI are in out/).")
        return FileResponse(path, media_type="application/pdf", filename=f"sheets-{batch}.pdf")

    @app.post("/api/scans")
    def upload_scans(files: Annotated[list[UploadFile], File()]) -> dict[str, Any]:
        # Sync on purpose: scanning is CPU work, so it runs in the thread pool.
        results = []
        sheet_ids: list[str] = []
        with store() as s:
            for upload in files:
                data = upload.file.read(MAX_UPLOAD_BYTES + 1)
                name = Path(upload.filename or "upload").name
                if len(data) > MAX_UPLOAD_BYTES:
                    results.append({"source": name, "error": "File larger than 50 MB."})
                    continue
                for r in scan_file(s, Path(name), data=data):
                    reg = r.registered
                    if reg is None:
                        results.append({"source": r.source, "error": r.error})
                        continue
                    sheet_ids.append(reg.ref.sheet_id)
                    results.append(
                        {
                            "source": r.source,
                            "scan_id": r.scan_id,
                            "sheet": Sheet(reg.ref.sheet_id, 0).code,
                            "page": reg.ref.page,
                            "pages": reg.ref.pages,
                            "residual_mm": round(reg.residual_mm, 2),
                            "markers": len(reg.markers),
                            "notes": r.notes,
                        }
                    )
            statuses = [
                {"sheet": st.code, "found": st.found, "pages": st.pages, "missing": st.missing}
                for st in sheet_status(s, sheet_ids)
            ]
        return {"results": results, "sheets": statuses}

    @app.get("/scans/{scan_id}/overlay.png")
    def scan_overlay(scan_id: str) -> Response:
        with store() as s:
            scan = s.get_scan(scan_id)
            layout = s.get_layout(scan["layout_hash"]) if scan else None
        if scan is None or layout is None:
            raise HTTPException(404, "No such scan.")
        image = cv2.imread(str(settings.home / scan["image"]), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise HTTPException(404, "The scan's image is missing from the store.")
        img = draw_overlay(image, layout, scan["page"])
        scale = 1000 / img.shape[1]
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        ok, png = cv2.imencode(".png", img)
        assert ok
        return Response(np.asarray(png).tobytes(), media_type="image/png")

    # -- review -------------------------------------------------------------

    def sheet_context(sheet_id: str) -> dict[str, Any]:
        with store() as s:
            scans = s.scans(sheet_id)
            if not scans:
                raise HTTPException(404, "No scans of that sheet.")
            layout = s.get_layout(scans[0]["layout_hash"])
            f = layout["form"]
            form = s.get_form(f["server"], f["asset_uid"], f["version_uid"])
            readings = s.readings(sheet_id)
            submission = s.submission(sheet_id)
        assembly = assemble(form, layout, readings) if readings else None
        problems = dict(assembly.problems) if assembly else {}
        rows = []
        for fid, info in layout["fields"].items():
            r = readings.get(fid)
            rows.append(
                {
                    "id": fid,
                    "info": info,
                    "reading": r,
                    "problem": problems.get(fid),
                    "hidden": bool(assembly and fid in assembly.dropped),
                    "choices": info.get("choices") or [],
                }
            )
        # Problems first, then the rest in form order.
        rows.sort(key=lambda row: row["problem"] is None)
        return {
            "sheet_id": sheet_id,
            "code": Sheet(sheet_id, 0).code,
            "form": form,
            "layout": layout,
            "rows": rows,
            "pages_found": sorted(sc["page"] for sc in scans),
            "submission": submission,
            "problems": len(problems),
            "read": bool(readings),
        }

    @app.get("/sheets", response_class=HTMLResponse)
    def sheets_page(request: Request) -> HTMLResponse:
        with store() as s:
            sheets = []
            for row in s.scanned_sheets():
                readings = s.readings(row["sheet_id"])
                sheets.append(
                    {
                        **row,
                        "code": Sheet(row["sheet_id"], 0).code,
                        "read": len(readings),
                        "review": sum(
                            1 for r in readings.values() if r["needs_review"] and not r["reviewed"]
                        ),
                        "submission": s.submission(row["sheet_id"]),
                    }
                )
        return templates.TemplateResponse(
            request, "sheets.html", {"sheets": sheets, "vlm": VlmConfig.from_env() is not None}
        )

    @app.get("/sheets/{sheet_id}", response_class=HTMLResponse)
    def sheet_page(request: Request, sheet_id: str, message: str | None = None) -> HTMLResponse:
        ctx = sheet_context(sheet_id)
        return templates.TemplateResponse(
            request,
            "sheet.html",
            {
                **ctx,
                "message": message,
                "has_token": bool(settings.token),
            },
        )

    @app.get("/sheets/{sheet_id}/crop.png")
    def field_crop(sheet_id: str, f: str) -> Response:
        """A field as scanned: every mark of it, with a little of the page around."""
        with store() as s:
            scans = {sc["page"]: sc for sc in s.scans(sheet_id)}
            layout = s.get_layout(next(iter(scans.values()))["layout_hash"]) if scans else None
        marks = [m for m in (layout or {}).get("marks", []) if m["f"] == f]
        if not marks or marks[0]["page"] not in scans:
            raise HTTPException(404, "No such field on the scanned pages.")
        scan = scans[marks[0]["page"]]
        image = cv2.imread(str(settings.home / scan["image"]), cv2.IMREAD_GRAYSCALE)
        x0 = min(m["x"] for m in marks) - 2
        y0 = min(m["y"] for m in marks) - 2
        x1 = max(m["x"] + m["w"] for m in marks) + 2
        y1 = max(m["y"] + m["h"] for m in marks) + 2
        if any(m["k"] == "choice" for m in marks):
            x1 += 40  # the choice labels sit to the right of their bubbles
        box = {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "k": "area"}
        ok, png = cv2.imencode(".png", crop_image(image, box, inset=0.0))
        assert ok
        return Response(png.tobytes(), media_type="image/png")

    @app.post("/sheets/{sheet_id}/read")
    def read_again(sheet_id: str) -> Response:
        config = VlmConfig.from_env()
        vlm = VlmReader(config) if config else None
        try:
            with store() as s:
                readings = read_sheet(s, sheet_id, vlm)
        finally:
            if vlm:
                vlm.close()
        flagged = sum(r.needs_review for r in readings)
        note = f"Read {len(readings)} fields; {flagged} to check."
        if not config:
            note += " No text reader configured (KFF_VLM_URL), so written text is left to you."
        return RedirectResponse(f"/sheets/{sheet_id}?message={_q(note)}", status_code=303)

    @app.post("/sheets/{sheet_id}/review")
    async def save_review(request: Request, sheet_id: str) -> Response:
        form = await request.form()
        with store() as s:
            current = s.readings(sheet_id)
            saved = 0
            for key, raw in form.items():
                if not key.startswith("value:"):
                    continue
                fid = key[len("value:") :]
                value = str(raw).strip() or None
                old = current.get(fid)
                confirmed = form.get(f"ok:{fid}") == "on"
                changed = old is None or (old["value"] or None) != value
                if changed or (confirmed and not old["reviewed"]):
                    s.review(sheet_id, fid, value)
                    saved += 1
        return RedirectResponse(
            f"/sheets/{sheet_id}?message={_q(f'Saved {saved} answer(s).')}", status_code=303
        )

    @app.post("/sheets/{sheet_id}/submit")
    def submit(sheet_id: str) -> Response:
        try:
            with KoboClient(live_settings()) as client, store() as s:
                done = submit_sheet(s, client, sheet_id)
            note = (
                "Submitted to Kobo."
                if done.status == 201
                else "Already in Kobo: this sheet was submitted before (duplicate)."
            )
        except (KffError, ConfigError) as err:
            note = f"Not submitted: {err}"
        return RedirectResponse(f"/sheets/{sheet_id}?message={_q(note)}", status_code=303)

    return app


def _q(text: str) -> str:
    return quote(text[:300])
