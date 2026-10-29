"""`kff`: the command line."""

import argparse
import json
import logging
import sys
from pathlib import Path

import cv2

from kff.config import Settings
from kff.errors import KffError
from kff.fetch import fetch_form, parse_asset_uid
from kff.kobo.client import KoboClient
from kff.render.options import (
    CHOICE_LAYOUTS,
    LANGUAGE_LAYOUTS,
    PAPERS,
    TEXT_ENTRIES,
    RenderOptions,
)
from kff.render.pdf import merge_pdfs, render_png, render_set
from kff.scan.extract import crops, overlay
from kff.scan.pipeline import scan_file, sheet_status
from kff.store import FormVersion, Store, new_batch_id


def _summary(form: FormVersion) -> str:
    langs = ", ".join(lang or "(default)" for lang in form.languages)
    rows = len(form.content.get("survey") or [])
    return (
        f"{form.asset_uid}  {form.version_uid}  v{form.version_number or '?'}  "
        f"{(form.date_deployed or '')[:10]}  {rows} rows  [{langs}]  {form.name}"
    )


def cmd_fetch(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    uid = parse_asset_uid(args.project)
    with KoboClient(settings) as client, Store(settings.home) as store:
        form = store.save_form(fetch_form(client, settings.server, uid, args.version))
    print(_summary(form))


def cmd_forms(args: argparse.Namespace) -> None:
    settings = Settings.from_env(need_token=False)
    with Store(settings.home) as store:
        forms = list(store.list_forms(None if args.all_servers else settings.server))
    if args.json:
        print(json.dumps([{**vars(f), "content": None} for f in forms], indent=2))
        return
    for form in forms:
        print(_summary(form))


def cmd_show(args: argparse.Namespace) -> None:
    settings = Settings.from_env(need_token=False)
    uid = parse_asset_uid(args.project)
    with Store(settings.home) as store:
        form = store.get_form(settings.server, uid, args.version)
    if form is None:
        raise KffError(f"{uid} isn't in the store for {settings.server}; run `kff fetch` first.")
    print(json.dumps(form.content, indent=2, ensure_ascii=False))


def options_from_args(args: argparse.Namespace) -> RenderOptions:
    languages = tuple(
        lang.strip() for value in args.language or [] for lang in value.split(",") if lang.strip()
    )
    return RenderOptions(
        paper=args.paper,
        orientation=args.orientation,
        sides=args.sides,
        languages=languages,
        language_layout=args.language_layout,
        columns=args.columns,
        font_size=args.font_size,
        choice_layout=args.choice_layout,
        text_entry=args.text_entry,
        repeat_rows=args.repeat_rows,
        show_hints=not args.no_hints,
        show_conditions=not args.no_conditions,
        show_instructions=not args.no_instructions,
        show_names=args.show_names,
    )


def cmd_render(args: argparse.Namespace) -> None:
    settings = Settings.from_env(need_token=False)
    uid = parse_asset_uid(args.project)
    options = options_from_args(args)
    with Store(settings.home) as store:
        form = store.get_form(settings.server, uid, args.version)
        if form is None:
            raise KffError(
                f"{uid} isn't in the store for {settings.server}; run `kff fetch {uid}` first."
            )
        rendered = render_set(form, options, copies=args.copies)
        batch = new_batch_id()
        out = Path(args.out or f"out/{uid}-{batch}.pdf")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(merge_pdfs([r.pdf for r in rendered]))
        # Only record the sheets once the PDF exists: a sheet in the store is one that
        # may be printed and scanned.
        for r in rendered:
            store.save_layout(r.layout)
            store.save_sheets(r.layout_hash, batch, [s.id for s in r.sheets])
    if args.layout_json:
        layouts = [r.layout for r in rendered]
        Path(args.layout_json).write_text(
            json.dumps(layouts[0] if len(layouts) == 1 else layouts, indent=1)
        )
    if args.preview:
        preview = Path(args.preview)
        preview.mkdir(parents=True, exist_ok=True)
        for r in rendered:
            suffix = f"-{r.layout_hash}" if len(rendered) > 1 else ""
            for i, png in enumerate(render_png(r), 1):
                (preview / f"{uid}{suffix}-page{i}.png").write_bytes(png)
    for r in rendered:
        langs = " + ".join(lang.name or "default language" for lang in r.model.languages)
        print(
            f"{out}  {args.copies} sheet(s) × {r.pages_per_sheet} page(s)  "
            f"layout {r.layout_hash}  batch {batch}  [{langs}]"
        )
    for warning in dict.fromkeys(w for r in rendered for w in r.model.warnings):
        print(f"warning: {warning}", file=sys.stderr)


def cmd_batches(args: argparse.Namespace) -> None:
    settings = Settings.from_env(need_token=False)
    with Store(settings.home) as store:
        for b in store.batches():
            print(
                f"{b['batch']}  {b['sheets']} sheet(s)  layout {b['layout_hash']}  "
                f"{b['asset_uid']}  {b['version_uid']}  {b['name']}"
            )


def cmd_scan(args: argparse.Namespace) -> None:
    settings = Settings.from_env(need_token=False)
    debug = Path(args.debug) if args.debug else None
    failed = 0
    sheets: list[str] = []
    with Store(settings.home) as store:
        for name in args.files:
            for result in scan_file(store, Path(name), dpi=args.dpi):
                reg = result.registered
                if reg is None:
                    failed += 1
                    print(f"{result.source}: FAILED: {result.error}")
                    continue
                sheets.append(reg.ref.sheet_id)
                code = f"{reg.ref.sheet_id[:4]}-{reg.ref.sheet_id[4:8]}".upper()
                print(
                    f"{result.source}: sheet {code} page {reg.ref.page}/{reg.ref.pages}  "
                    f"markers {len(reg.markers)}/4  error {reg.residual_mm:.2f} mm"
                )
                for note in result.notes:
                    print(f"  note: {note}")
                if debug:
                    stem = f"{code}-p{reg.ref.page}"
                    debug.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(debug / f"{stem}-overlay.png"), overlay(reg))
                    for fid, items in crops(reg).items():
                        for i, c in enumerate(items):
                            safe = fid.replace("/", "_").replace("[", "_").replace("]", "")
                            cv2.imwrite(str(debug / f"{stem}-{safe}-{i}.png"), c.image)
        statuses = sheet_status(store, sheets)
    for st in statuses:
        missing = f"  missing pages {st.missing}" if st.missing else "  complete"
        print(f"sheet {st.code}: {len(st.found)}/{st.pages} pages{missing}")
    if failed:
        raise KffError(f"{failed} page(s) couldn't be read.")


def cmd_web(args: argparse.Namespace) -> None:
    # Imported here so the other commands don't pay for loading the web stack.
    import uvicorn

    from kff.web.app import LOCAL_HOSTS, create_app

    settings = Settings.from_env(need_token=False)
    extra = [] if args.host in LOCAL_HOSTS else [args.host]
    if extra:
        print(
            f"warning: serving on {args.host}: anyone who can reach it can fetch forms with "
            "your token and read your scans. Prefer an SSH tunnel to 127.0.0.1.",
            file=sys.stderr,
        )
    print(f"kff web: http://{args.host}:{args.port}/  (store: {settings.home})")
    uvicorn.run(create_app(settings, extra), host=args.host, port=args.port, log_level="warning")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kff", description="Paper forms for KoboToolbox.")
    parser.add_argument("-v", "--verbose", action="store_true", help="log requests")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="fetch a deployed form version into the local store")
    p.add_argument("project", help="project uid or link")
    p.add_argument("--version", help="deployed version uid (default: the current one)")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("forms", help="list the form versions in the local store")
    p.add_argument("--all-servers", action="store_true", help="not just KOBO_BASE_URL's")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_forms)

    p = sub.add_parser("show", help="print a stored form version's content as JSON")
    p.add_argument("project", help="project uid or link")
    p.add_argument("--version", help="version uid (default: the newest stored)")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("render", help="render printable sheets (PDF) from a stored form")
    p.add_argument("project", help="project uid or link")
    p.add_argument("--version", help="version uid (default: the newest stored)")
    p.add_argument("--copies", type=int, default=1, help="sheets, each with its own id")
    p.add_argument("--paper", default="a4", choices=sorted(PAPERS))
    p.add_argument("--orientation", default="portrait", choices=["portrait", "landscape"])
    p.add_argument("--sides", default="single", choices=["single", "double"])
    p.add_argument(
        "--language",
        action="append",
        help="translation name or code (en, 'Arabic (ar)'); repeat or comma-separate for "
        "several, page language first",
    )
    p.add_argument("--language-layout", default="stacked", choices=LANGUAGE_LAYOUTS)
    p.add_argument("--columns", type=int, default=1, choices=[1, 2], help="question columns")
    p.add_argument("--font-size", type=float, default=10.0, help="points (default 10)")
    p.add_argument("--choice-layout", default="auto", choices=CHOICE_LAYOUTS)
    p.add_argument("--text-entry", default="auto", choices=TEXT_ENTRIES)
    p.add_argument("--repeat-rows", type=int, default=3, help="entries for an open repeat")
    p.add_argument("--no-hints", action="store_true")
    p.add_argument("--no-conditions", action="store_true", help="don't print skip logic")
    p.add_argument("--no-instructions", action="store_true")
    p.add_argument("--show-names", action="store_true", help="print field ids (testing)")
    p.add_argument("--out", help="PDF path (default: out/<uid>-<batch>.pdf)")
    p.add_argument("--layout-json", help="also write the layout map here")
    p.add_argument("--preview", help="also write PNGs of the first sheet to this directory")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("batches", help="list rendered batches of sheets")
    p.set_defaults(func=cmd_batches)

    p = sub.add_parser("scan", help="read scanned pages (PDF or images) into the store")
    p.add_argument("files", nargs="+", help="scanned PDFs or images")
    p.add_argument("--dpi", type=int, default=200, help="rasterising resolution for PDFs")
    p.add_argument("--debug", help="write overlays and field crops to this directory")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("web", help="local web interface: design, preview, print, scan")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_web)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s"
    )
    try:
        args.func(args)
    except KffError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
