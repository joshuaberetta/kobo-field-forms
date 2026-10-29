"""Write test on a real server: creates a `kff-e2e-*` project, submits to it, reads the
submission back and deletes the project. Run with `uv run pytest -m live`."""

import json
import os
import random
import time
import uuid
from pathlib import Path

import cv2
import numpy as np
import pytest

from kff.bench import same
from kff.config import Settings
from kff.fetch import fetch_form
from kff.form.rules import rules
from kff.kobo.client import KoboClient
from kff.kobo.deploy import create_and_deploy, delete_test_project
from kff.render.pdf import Sheet, render
from kff.submit import build_xml, submission_values
from kff.synth.answers import random_answers

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("KOBO_TOKEN"), reason="needs KOBO_TOKEN"),
]
CONTENT = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "forms" / "all_types.json").read_text()
)


@pytest.fixture(scope="module")
def project():
    settings = Settings.from_env()
    with KoboClient(settings) as client:
        uid = create_and_deploy(client, f"kff-e2e-{time.strftime('%Y%m%d-%H%M%S')}", CONTENT)
        try:
            yield settings, client, uid
        finally:
            delete_test_project(client, uid)


def test_submit_read_back_and_duplicate(project):
    settings, client, uid = project
    form = fetch_form(client, settings.server, uid)
    rendered = render(form)
    answers = random_answers(rendered.model, rules(form.content), random.Random(5))
    values = answers.values()
    sheet_id = uuid.uuid4().hex
    png = cv2.imencode(".png", np.full((40, 120), 255, np.uint8))[1].tobytes()
    files = {}
    if "signature" in values:
        values["signature"] = "signature.png"
        files["signature.png"] = (png, "image/png")

    xml = build_xml(form, values, sheet_id)
    first = client.submit(xml, files)
    assert first.status_code == 201, first.text
    again = client.submit(xml, files)
    assert again.status_code == 202  # same sheet id: Kobo's duplicate, not a second record

    instance = Sheet(sheet_id, 0).instance_id
    data = client.get(
        f"/api/v2/assets/{uid}/data/", params={"query": {"meta/instanceID": instance}}
    )
    assert data["count"] == 1
    record = data["results"][0]
    stored = submission_values(record)
    types = {fid: info["type"] for fid, info in rendered.layout["fields"].items()}
    wrong = {
        fid: (v, stored.get(fid))
        for fid, v in values.items()
        if not same(types[fid], v, stored.get(fid))
    }
    assert not wrong
    assert record["__version__"] == form.version_uid
    if files:
        assert [a["media_file_basename"] for a in record["_attachments"]] == ["signature.png"]
