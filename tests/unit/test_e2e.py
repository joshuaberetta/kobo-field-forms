import argparse
import contextlib
import json

import pytest

from kff.config import Settings
from kff.e2e import main_e2e, run_e2e
from kff.errors import KffError


def test_offline_e2e(tmp_path):
    settings = Settings(base_url="https://kf.kobotoolbox.org", token="", home=tmp_path)
    report = run_e2e(
        settings,
        profiles=["a4-en"],
        sheets=1,
        seed=2,
        offline=True,
        keep_project=False,
        log=lambda *_: None,
    )
    summary = report.profiles["a4-en"]
    assert summary["scan_failures"] == []
    assert summary["by_type"]["select_one"]["accuracy"] == 1.0
    assert report.project is None and report.kobo_mismatches == []


def test_cli_e2e_offline_writes_a_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KFF_HOME", str(tmp_path))
    monkeypatch.delenv("KOBO_TOKEN", raising=False)
    monkeypatch.delenv("KFF_VLM_URL", raising=False)
    args = argparse.Namespace(
        profiles="a5-ar-large",
        sheets=1,
        seed=3,
        offline=True,
        keep_project=False,
        report=str(tmp_path / "r.json"),
    )
    # Thresholds may fail on one sheet; the report is what's checked here.
    with contextlib.suppress(KffError):
        main_e2e(args)
    report = json.loads((tmp_path / "r.json").read_text())
    assert "a5-ar-large" in report["profiles"]
    assert "report:" in capsys.readouterr().out


def test_unknown_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("KFF_HOME", str(tmp_path))
    args = argparse.Namespace(
        profiles="nope",
        sheets=1,
        seed=1,
        offline=True,
        keep_project=False,
        report=str(tmp_path / "r.json"),
    )
    with pytest.raises(KffError, match="Unknown profile"):
        main_e2e(args)
