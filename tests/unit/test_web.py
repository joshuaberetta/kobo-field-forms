import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kff.config import Settings
from kff.store import FormVersion, Store
from kff.web.app import create_app

FIXTURES = Path(__file__).parents[1] / "fixtures"
ASSET, VERSION = "aAllTypes000000000000", "vAllTypes000000000000"
BASE = f"/api/forms/{ASSET}/{VERSION}"


@pytest.fixture
def client(tmp_path):
    with Store(tmp_path) as store:
        store.save_form(
            FormVersion(
                server="kf.kobotoolbox.org",
                asset_uid=ASSET,
                version_uid=VERSION,
                name="All question types",
                version_number="3",
                date_deployed="2026-10-01T00:00:00Z",
                deployment_uuid="uuid",
                content_hash="h",
                content=json.loads((FIXTURES / "forms" / "all_types.json").read_text()),
            )
        )
    settings = Settings(base_url="https://kf.kobotoolbox.org", token="", home=tmp_path)
    return TestClient(create_app(settings))


def test_pages(client):
    index = client.get("/")
    assert index.status_code == 200 and "All question types" in index.text
    assert "KOBO_TOKEN" in index.text  # no token: fetching is explained, not offered
    designer = client.get(f"/forms/{ASSET}/{VERSION}")
    assert designer.status_code == 200 and "Arabic (ar)" in designer.text
    assert client.get("/scans").status_code == 200
    assert client.get(f"/forms/{ASSET}/vNope").status_code == 404


def test_foreign_host_refused(client):
    assert client.get("/", headers={"Host": "evil.example.com"}).status_code == 400


def test_preview(client):
    resp = client.post(
        f"{BASE}/preview",
        json={
            "options": {
                "paper": "us-letter",
                "languages": ["English (en)", "Arabic (ar)"],
                "columns": "1",
                "font_size": "10",
                "show_hints": False,
            }
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    [layout] = data["layouts"]
    assert layout["languages"] == ["English (en)", "Arabic (ar)"]
    assert len(layout["pages"]) == layout["pages_per_sheet"]
    assert layout["pages"][0].startswith("data:image/png;base64,")
    assert layout["marks"] and layout["paper"]["w"] == 215.9
    assert data["warnings"]


def test_preview_per_sheet_gives_a_layout_per_language(client):
    options = {"languages": ["en", "ar"], "language_layout": "per-sheet"}
    data = client.post(f"{BASE}/preview", json={"options": options}).json()
    assert [lay["languages"] for lay in data["layouts"]] == [["English (en)"], ["Arabic (ar)"]]


def test_preview_refuses_impossible_options(client):
    resp = client.post(f"{BASE}/preview", json={"options": {"paper": "a5", "columns": 2}})
    assert resp.status_code == 400
    assert "question columns" in resp.json()["error"]


def test_render_download_and_scan_back(client):
    made = client.post(f"{BASE}/render", json={"options": {"sides": "double"}, "copies": 2})
    assert made.status_code == 200
    batch = made.json()
    assert batch["sheets"] == 2 and batch["pages"] % 2 == 0
    pdf = client.get(batch["url"])
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert batch["batch"] in client.get("/").text

    scanned = client.post(
        "/api/scans",
        files=[
            ("files", ("sheets.pdf", pdf.content, "application/pdf")),
            ("files", ("notes.txt", b"hello", "text/plain")),
        ],
    )
    assert scanned.status_code == 200
    data = scanned.json()
    ok = [r for r in data["results"] if "scan_id" in r]
    assert len(ok) == batch["pages"]
    assert data["results"][-1]["error"].startswith("notes.txt")
    assert all(s["missing"] == [] for s in data["sheets"])
    overlay = client.get(f"/scans/{ok[0]['scan_id']}/overlay.png")
    assert overlay.status_code == 200 and overlay.content.startswith(b"\x89PNG")
    assert "overlay" in client.get("/scans").text


def test_batch_path_cannot_escape(client):
    assert client.get("/batches/..%2F..%2Fkff.db.pdf").status_code == 404


def test_review_flow(client):
    made = client.post(f"{BASE}/render", json={"options": {}, "copies": 1}).json()
    pdf = client.get(made["url"]).content
    scanned = client.post("/api/scans", files=[("files", ("s.pdf", pdf, "application/pdf"))])
    sheet = next(r for r in scanned.json()["results"] if "scan_id" in r)
    sheets = client.get("/sheets")
    assert sheets.status_code == 200 and sheet["sheet"] in sheets.text
    sheet_id = sheet["sheet"]  # a code; find the id from the list page link
    import re

    sheet_id = re.search(r'href="/sheets/([0-9a-f]{32})"', sheets.text).group(1)
    page = client.get(f"/sheets/{sheet_id}")
    assert "Read answers" in page.text

    read = client.post(f"/sheets/{sheet_id}/read", follow_redirects=True)
    assert "Read " in read.text and "fields" in read.text
    # A blank sheet: the required questions are problems, so submitting is disabled.
    assert "required, but no answer was read" in read.text
    assert "disabled" in read.text

    crop = client.get(f"/sheets/{sheet_id}/crop.png", params={"f": "consent"})
    assert crop.status_code == 200 and crop.content.startswith(b"\x89PNG")
    assert client.get(f"/sheets/{sheet_id}/crop.png", params={"f": "nope"}).status_code == 404

    saved = client.post(
        f"/sheets/{sheet_id}/review",
        data={"value:age": "34", "value:consent": "yes", "ok:consent": "on", "value:full_name": ""},
        follow_redirects=True,
    )
    assert "Saved 2 answer(s)" in saved.text
    assert 'value="34"' in saved.text

    # No token in this app's environment: submitting explains why it can't.
    result = client.post(f"/sheets/{sheet_id}/submit", follow_redirects=True)
    assert "Not submitted" in result.text
