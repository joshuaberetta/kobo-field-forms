"""Make the homepage screenshots (src/kff/web/static/home/*.jpg) from a demo store.

  uv run python scripts/screenshots.py

Everything shown is the real app on synthetic data: the demo form is the e2e form,
the filled-in sheets come from synth/ and are scanned and read like real ones. No text
reader is configured, so written text shows as flagged for a person, as it would.
"""

import json
import random
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import uvicorn
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kff.bench import run  # noqa: E402
from kff.config import Settings  # noqa: E402
from kff.form.rules import rules  # noqa: E402
from kff.render.options import RenderOptions  # noqa: E402
from kff.render.pdf import render, render_png  # noqa: E402
from kff.scan.extract import draw_overlay  # noqa: E402
from kff.store import FormVersion, Store  # noqa: E402
from kff.synth.answers import random_answers  # noqa: E402
from kff.synth.degrade import Degrade, degrade  # noqa: E402
from kff.synth.fill import filled_pages  # noqa: E402
from kff.web.app import create_app  # noqa: E402

OUT = ROOT / "src" / "kff" / "web" / "static" / "home"
QUALITY = 82


def save_jpg(name: str, img: np.ndarray, width: int | None = None) -> None:
    if width and img.shape[1] > width:
        img = cv2.resize(
            img, (width, round(img.shape[0] * width / img.shape[1])), interpolation=cv2.INTER_AREA
        )
    cv2.imwrite(str(OUT / name), img, [cv2.IMWRITE_JPEG_QUALITY, QUALITY])
    print("wrote", name, img.shape[1], "×", img.shape[0])


def png(data: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def top(img: np.ndarray, share: float) -> np.ndarray:
    return img[: round(img.shape[0] * share)]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="kff-shots-"))
    content = json.loads((ROOT / "src" / "kff" / "e2e_form.json").read_text())
    form = FormVersion(
        "kf.kobotoolbox.org",
        "aHouseholdDemo0000000",
        "vHouseholdDemo0000000",
        "Household survey",
        "3",
        "2026-10-01T09:00:00Z",
        "0" * 32,
        "h",
        content,
    )

    # Printed sheets, as images.
    a4 = render(form, RenderOptions())
    save_jpg("sheet-en.jpg", top(png(render_png(a4, 130)[0]), 0.62), 1280)
    both = render(
        form,
        RenderOptions(
            orientation="landscape", languages=("en", "ar"), language_layout="columns", columns=2
        ),
    )
    save_jpg("sheet-bilingual.jpg", png(render_png(both, 110)[0]), 1280)
    ar = render(form, RenderOptions(paper="a5", languages=("ar",), font_size=12))
    save_jpg("sheet-arabic.jpg", png(render_png(ar, 150)[0]), 760)

    # A filled-in sheet, photographed.
    rng = random.Random(8)
    answers = random_answers(a4.model, rules(content), rng)
    filled = png(filled_pages(a4, answers, rng, ppi=150)[0])
    look = Degrade(
        rotate_deg=3, perspective=0.03, blur=0.6, noise=4, jpeg_quality=85, pad_px=90, seed=4
    )
    photo, _ = degrade(cv2.cvtColor(filled, cv2.COLOR_BGR2GRAY), look)
    # Where the page isn't: the same warp of an all-black page, without noise.
    outside, _ = degrade(
        np.zeros(filled.shape[:2], np.uint8),
        Degrade(rotate_deg=3, perspective=0.03, pad_px=90, seed=4),
    )
    canvas = cv2.cvtColor(photo, cv2.COLOR_GRAY2BGR)
    canvas[outside > 128] = (214, 222, 228)  # the table under the page
    save_jpg("filled-photo.jpg", top(canvas, 0.6), 1000)

    # A demo store with scanned and read sheets, for the app's own pages.
    with Store(home) as store:
        store.save_form(form)
        # The photographed sheet's batch, so its upload reads as one of ours.
        store.save_layout(a4.layout)
        store.save_sheets(a4.layout_hash, "20261029-090000-demo", [s.id for s in a4.sheets])
        run(store, form, RenderOptions(), sheets=3, seed=5, profile="demo")
        run(store, form, RenderOptions(languages=("ar", "en")), sheets=1, seed=9, profile="ar")
        sheet = store.scanned_sheets()[-1]
        scan = store.scans(sheet["sheet_id"])[0]
        layout = store.get_layout(scan["layout_hash"])
        image = cv2.imread(str(home / scan["image"]), cv2.IMREAD_GRAYSCALE)
        save_jpg("registered.jpg", top(draw_overlay(image, layout, scan["page"]), 0.55), 1100)
        # A couple of answers a person has typed in, as after review.
        readings = store.readings(sheet["sheet_id"])
        if "full_name" in readings:
            store.review(sheet["sheet_id"], "full_name", "GRACE MENSAH")
        review_id = sheet["sheet_id"]

    settings = Settings(base_url="https://kf.kobotoolbox.org", token="", home=home)
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)

        def shot(name: str, full: bool = False) -> None:
            page.screenshot(path=str(OUT / name), type="jpeg", quality=QUALITY, full_page=full)
            print("wrote", name)

        page.goto(f"{base}/forms")
        shot("forms.jpg")

        page.goto(f"{base}/forms/{form.asset_uid}/{form.version_uid}")
        page.wait_for_selector(".page img")
        page.check('input[name="also"][value="Arabic (ar)"]')
        page.select_option('select[name="orientation"]', "landscape")
        page.select_option('select[name="language_layout"]', "columns")
        page.check("#show_marks")
        page.wait_for_function("document.querySelector('#status').textContent.includes('layout')")
        page.wait_for_timeout(800)
        page.evaluate("document.querySelector('.options').scrollTop = 0; window.scrollTo(0, 0)")
        shot("designer.jpg")

        page.goto(f"{base}/sheets")
        shot("sheets.jpg")

        page.goto(f"{base}/sheets/{review_id}")
        page.wait_for_timeout(800)
        page.set_viewport_size({"width": 1440, "height": 1100})
        shot("review.jpg")

        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(f"{base}/scans")
        filled_pdf = home / "upload.png"
        cv2.imwrite(str(filled_pdf), photo)
        page.set_input_files("input[type=file]", str(filled_pdf))
        page.click("#upload button")
        page.wait_for_selector(".scan img")
        page.wait_for_timeout(1200)
        shot("scans.jpg")

        page.goto(f"{base}/")
        page.wait_for_timeout(500)
        page.screenshot(path=str(home / "home-check.png"), full_page=True)
        print("homepage check:", home / "home-check.png")
        browser.close()
    server.should_exit = True


if __name__ == "__main__":
    main()
