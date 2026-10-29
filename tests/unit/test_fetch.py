import json
from pathlib import Path

import httpx
import pytest
import respx

from kff.cli import main
from kff.errors import KffError
from kff.fetch import fetch_form, parse_asset_uid
from kff.kobo.client import KoboClient
from kff.store import Store
from tests.conftest import BASE, TOKEN

FIXTURE = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "asset_two_languages.json").read_text()
)
ASSET = FIXTURE["asset"]
UID = ASSET["uid"]
SERVER = "kf.kobotoolbox.org"


def mock_kobo(asset=ASSET):
    respx.get(f"{BASE}/api/v2/assets/{UID}/").mock(return_value=httpx.Response(200, json=asset))
    for version in FIXTURE["versions"]:
        respx.get(f"{BASE}/api/v2/assets/{UID}/versions/{version['uid']}/").mock(
            return_value=httpx.Response(200, json=version)
        )


@pytest.fixture
def client(settings):
    with KoboClient(settings, sleep=lambda _: None) as c:
        yield c


@pytest.mark.parametrize(
    "value",
    [UID, f"  {UID} ", f"https://kf.kobotoolbox.org/#/forms/{UID}/summary"],
)
def test_parse_asset_uid(value):
    assert parse_asset_uid(value) == UID


@pytest.mark.parametrize("value", ["", "vNotAnAsset123456", "https://kf.kobotoolbox.org/#/"])
def test_parse_asset_uid_rejects(value):
    with pytest.raises(KffError):
        parse_asset_uid(value)


@respx.mock
def test_fetch_uses_deployed_version_not_draft(client):
    mock_kobo()
    form = fetch_form(client, SERVER, UID)
    assert form.version_uid == "vDeployed2000000000000"
    assert form.version_number == "2"
    assert form.deployment_uuid == ASSET["deployment__uuid"]
    assert [r["name"] for r in form.content["survey"]] == ["name", "age", "consent"]
    assert form.languages == ["English (en)", "Arabic (ar)"]


@respx.mock
def test_fetch_older_deployed_version(client):
    mock_kobo()
    form = fetch_form(client, SERVER, UID, "vDeployed1000000000000")
    assert form.version_number == "1"
    assert len(form.content["survey"]) == 2


@respx.mock
def test_fetch_refuses_undeployed_version(client):
    mock_kobo()
    with pytest.raises(KffError, match="never deployed"):
        fetch_form(client, SERVER, UID, "vDraftNotDeployed000000")


@respx.mock
def test_fetch_unknown_version(client):
    mock_kobo()
    respx.get(f"{BASE}/api/v2/assets/{UID}/versions/vNope/").mock(return_value=httpx.Response(404))
    with pytest.raises(KffError, match="no version vNope"):
        fetch_form(client, SERVER, UID, "vNope")


@respx.mock
def test_fetch_refuses_never_deployed_asset(client):
    mock_kobo({**ASSET, "has_deployment": False, "deployed_version_id": None})
    with pytest.raises(KffError, match="never been deployed"):
        fetch_form(client, SERVER, UID)


@respx.mock
def test_fetch_refuses_non_survey(client):
    mock_kobo({**ASSET, "asset_type": "block"})
    with pytest.raises(KffError, match="not a survey"):
        fetch_form(client, SERVER, UID)


@respx.mock
def test_cli_fetch_forms_show(tmp_path, monkeypatch, capsys):
    mock_kobo()
    monkeypatch.setenv("KOBO_TOKEN", TOKEN)
    monkeypatch.setenv("KOBO_BASE_URL", "global")
    monkeypatch.setenv("KFF_HOME", str(tmp_path))

    assert main(["fetch", f"{BASE}/#/forms/{UID}/landing"]) == 0
    out = capsys.readouterr().out
    assert "vDeployed2000000000000" in out and "Arabic (ar)" in out

    # Offline from here on: forms and show read the store only.
    monkeypatch.delenv("KOBO_TOKEN")
    assert main(["forms"]) == 0
    assert "Test form" in capsys.readouterr().out
    assert main(["show", UID]) == 0
    assert json.loads(capsys.readouterr().out)["translations"][1] == "Arabic (ar)"

    with Store(tmp_path) as store:
        assert len(list(store.list_forms())) == 1


def test_cli_reports_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("KOBO_TOKEN", raising=False)
    monkeypatch.setenv("KFF_HOME", str(tmp_path))
    assert main(["fetch", UID]) == 1
    assert "KOBO_TOKEN is not set" in capsys.readouterr().err
    assert main(["show", UID]) == 1
    assert "kff fetch" in capsys.readouterr().err


@respx.mock
def test_cli_render(tmp_path, monkeypatch, capsys):
    mock_kobo()
    monkeypatch.setenv("KOBO_TOKEN", TOKEN)
    monkeypatch.setenv("KFF_HOME", str(tmp_path))
    assert main(["fetch", UID]) == 0
    monkeypatch.delenv("KOBO_TOKEN")  # rendering is offline
    pdf = tmp_path / "sheets.pdf"
    args = ["render", UID, "--copies", "2", "--out", str(pdf), "--language", "ar"]
    assert main([*args, "--preview", str(tmp_path / "png")]) == 0
    out = capsys.readouterr().out
    assert "2 sheet(s) × 1 page(s)" in out and "Arabic (ar)" in out
    assert pdf.read_bytes().startswith(b"%PDF")
    assert (tmp_path / "png" / f"{UID}-page1.png").exists()
    assert main(["batches"]) == 0
    assert "2 sheet(s)" in capsys.readouterr().out

    assert main(["render", "aNotFetched0000000000"]) == 1
    assert "kff fetch aNotFetched0000000000" in capsys.readouterr().err
