import sqlite3

import pytest

from kff.errors import KffError
from kff.store import _MIGRATIONS, SCHEMA_VERSION, FormVersion, Store


def make_form(version_uid="v1", date="2026-10-01T00:00:00Z", name="Form", **kw) -> FormVersion:
    return FormVersion(
        server="kf.kobotoolbox.org",
        asset_uid="aAsset000000",
        version_uid=version_uid,
        name=name,
        version_number="1",
        date_deployed=date,
        deployment_uuid="uuid",
        content_hash="h",
        content={"survey": [], "translations": [None]},
        **kw,
    )


def test_save_and_get(tmp_path):
    with Store(tmp_path) as store:
        saved = store.save_form(make_form())
        assert saved.fetched_at
        assert store.get_form("kf.kobotoolbox.org", "aAsset000000", "v1") == saved
        assert store.get_form("eu.kobotoolbox.org", "aAsset000000") is None


def test_refetch_updates_in_place(tmp_path):
    with Store(tmp_path) as store:
        store.save_form(make_form(name="Old"))
        store.save_form(make_form(name="New"))
        forms = list(store.list_forms())
    assert [f.name for f in forms] == ["New"]


def test_newest_version_by_default(tmp_path):
    with Store(tmp_path) as store:
        store.save_form(make_form("v2", "2026-10-02T00:00:00Z"))
        store.save_form(make_form("v1", "2026-10-01T00:00:00Z"))
        assert store.get_form("kf.kobotoolbox.org", "aAsset000000").version_uid == "v2"


def test_untranslated_form_has_one_default_language():
    assert make_form().languages == [""]


def test_refuses_newer_schema(tmp_path):
    Store(tmp_path).close()
    db = sqlite3.connect(tmp_path / "kff.db")
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    db.close()
    with pytest.raises(KffError, match="newer"):
        Store(tmp_path)


def test_layouts_and_sheets(tmp_path):
    layout = {
        "layout_hash": "abcd1234",
        "pages_per_sheet": 2,
        "form": {"server": "kf.kobotoolbox.org", "asset_uid": "aAsset000000", "version_uid": "v1"},
    }
    with Store(tmp_path) as store:
        store.save_form(make_form())
        store.save_layout(layout)
        store.save_layout(layout)  # same hash: kept once
        store.save_sheets("abcd1234", "b1", ["s1", "s2"])
        assert store.get_layout("abcd1234") == layout
        assert store.get_sheet("s2")["copy"] == 2
        assert store.get_sheet("nope") is None
        [batch] = store.batches()
    assert (batch["batch"], batch["sheets"], batch["name"]) == ("b1", 2, "Form")


def test_upgrades_an_older_store(tmp_path):
    db = sqlite3.connect(tmp_path / "kff.db")
    db.executescript(_MIGRATIONS[1])
    db.execute("PRAGMA user_version = 1")
    db.close()
    with Store(tmp_path) as store:
        assert store.batches() == []
