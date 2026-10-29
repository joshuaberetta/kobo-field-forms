"""The local store: one SQLite file under KFF_HOME. Works offline.

Forms are keyed by (server, asset uid, version uid), the same triple the QR code on each
printed page carries, so a scan can be matched to its form without the network.
Layouts, sheets and scans get their own tables in later milestones.
"""

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kff.errors import KffError

SCHEMA_VERSION = 1

_MIGRATIONS = {
    1: """
    CREATE TABLE forms (
        server          TEXT NOT NULL,   -- host[:port], e.g. kf.kobotoolbox.org
        asset_uid       TEXT NOT NULL,
        version_uid     TEXT NOT NULL,
        name            TEXT NOT NULL,
        version_number  TEXT,            -- Kobo's deployed count for this version
        date_deployed   TEXT,
        deployment_uuid TEXT NOT NULL,   -- formhub/uuid in submissions
        content_hash    TEXT,
        content         TEXT NOT NULL,   -- the version's form JSON
        fetched_at      TEXT NOT NULL,
        PRIMARY KEY (server, asset_uid, version_uid)
    );
    """,
}


@dataclass(frozen=True)
class FormVersion:
    """One deployed version of a form, with what's needed to render it and submit to it."""

    server: str
    asset_uid: str
    version_uid: str
    name: str
    version_number: str | None
    date_deployed: str | None
    deployment_uuid: str
    content_hash: str | None
    content: dict[str, Any]
    fetched_at: str = ""

    @property
    def languages(self) -> list[str]:
        """The form's translations, e.g. ["English (en)", "Arabic (ar)"]. A form without
        translations has one unnamed language, shown as ""."""
        return [t or "" for t in self.content.get("translations") or [None]]


class Store:
    def __init__(self, home: Path) -> None:
        home.mkdir(parents=True, exist_ok=True)
        self.path = home / "kff.db"
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _migrate(self) -> None:
        current = self._db.execute("PRAGMA user_version").fetchone()[0]
        if current > SCHEMA_VERSION:
            raise KffError(f"{self.path} is from a newer kff (schema {current}); upgrade kff.")
        with self._db:
            for version in range(current + 1, SCHEMA_VERSION + 1):
                self._db.executescript(_MIGRATIONS[version])
                self._db.execute(f"PRAGMA user_version = {version}")

    # -- forms --------------------------------------------------------------

    def save_form(self, form: FormVersion) -> FormVersion:
        """Insert or refresh a form version. A deployed version's content doesn't change,
        so a refresh only updates the name and fetch time."""
        fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
        with self._db:
            self._db.execute(
                """
                INSERT INTO forms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (server, asset_uid, version_uid)
                DO UPDATE SET name = excluded.name, fetched_at = excluded.fetched_at
                """,
                (
                    form.server,
                    form.asset_uid,
                    form.version_uid,
                    form.name,
                    form.version_number,
                    form.date_deployed,
                    form.deployment_uuid,
                    form.content_hash,
                    json.dumps(form.content, ensure_ascii=False),
                    fetched_at,
                ),
            )
        found = self.get_form(form.server, form.asset_uid, form.version_uid)
        assert found is not None
        return found

    def get_form(
        self, server: str, asset_uid: str, version_uid: str | None = None
    ) -> FormVersion | None:
        """A stored version, or the newest one fetched for the asset when `version_uid` is
        None."""
        if version_uid:
            row = self._db.execute(
                "SELECT * FROM forms WHERE server = ? AND asset_uid = ? AND version_uid = ?",
                (server, asset_uid, version_uid),
            ).fetchone()
        else:
            row = self._db.execute(
                """SELECT * FROM forms WHERE server = ? AND asset_uid = ?
                ORDER BY date_deployed DESC, fetched_at DESC LIMIT 1""",
                (server, asset_uid),
            ).fetchone()
        return _form(row) if row else None

    def list_forms(self, server: str | None = None) -> Iterator[FormVersion]:
        sql = "SELECT * FROM forms"
        args: tuple[str, ...] = ()
        if server:
            sql += " WHERE server = ?"
            args = (server,)
        sql += " ORDER BY server, name, date_deployed DESC"
        for row in self._db.execute(sql, args):
            yield _form(row)


def _form(row: sqlite3.Row) -> FormVersion:
    return FormVersion(**{**dict(row), "content": json.loads(row["content"])})
