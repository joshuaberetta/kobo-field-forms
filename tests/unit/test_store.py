import sqlite3

import pytest

from kff.errors import KffError
from kff.store import SCHEMA_VERSION, FormVersion, Store


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
