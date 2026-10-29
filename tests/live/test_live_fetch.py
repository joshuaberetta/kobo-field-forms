"""Read-only checks against a real server. Run with `uv run pytest -m live`."""

import os

import pytest

from kff.config import Settings
from kff.fetch import fetch_form
from kff.kobo.client import KoboClient
from kff.store import Store

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("KOBO_TOKEN"), reason="needs KOBO_TOKEN"),
]


@pytest.fixture(scope="module")
def live():
    settings = Settings.from_env()
    with KoboClient(settings) as client:
        assets = [
            a
            for a in client.paginate(
                "/api/v2/assets/", params={"q": "asset_type:survey"}, page_size=200
            )
            if a.get("has_deployment") and a.get("deployed_version_id")
        ]
        if not assets:
            pytest.skip("the account has no deployed surveys")
        yield settings, client, assets


def test_fetch_current_deployed_version(live, tmp_path):
    settings, client, assets = live
    asset = assets[0]
    form = fetch_form(client, settings.server, asset["uid"])
    assert form.version_uid == asset["deployed_version_id"]
    assert form.deployment_uuid and form.date_deployed
    assert isinstance(form.content.get("survey"), list) and form.content["survey"]

    with Store(tmp_path) as store:
        saved = store.save_form(form)
        assert store.get_form(settings.server, asset["uid"]) == saved


def test_fetch_ignores_undeployed_changes(live):
    settings, client, assets = live
    drafts = [a for a in assets if a["version_id"] != a["deployed_version_id"]]
    if not drafts:
        pytest.skip("no deployed survey with undeployed changes on the account")
    asset = drafts[0]
    form = fetch_form(client, settings.server, asset["uid"])
    assert form.version_uid == asset["deployed_version_id"] != asset["version_id"]
    draft = client.get(f"/api/v2/assets/{asset['uid']}/")["content"]
    deployed = client.get(f"/api/v2/assets/{asset['uid']}/versions/{form.version_uid}/")
    assert form.content == deployed["content"]
    # Not required by Kobo, but a draft that differs is what makes this test meaningful.
    if draft == deployed["content"]:
        pytest.skip("the undeployed save has the same content as the deployed one")
