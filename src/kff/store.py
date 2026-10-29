"""The local store: one SQLite file under KFF_HOME. Works offline.

Forms are keyed by (server, asset uid, version uid), the same triple the QR code on each
printed page carries, so a scan can be matched to its form without the network. The QR
code's layout hash finds the layout map, and its sheet id the printed sheet.
"""

import json
import secrets
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kff.errors import KffError

SCHEMA_VERSION = 4

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
    2: """
    CREATE TABLE layouts (
        layout_hash     TEXT PRIMARY KEY,  -- in every page's QR code
        server          TEXT NOT NULL,
        asset_uid       TEXT NOT NULL,
        version_uid     TEXT NOT NULL,
        pages_per_sheet INTEGER NOT NULL,
        layout          TEXT NOT NULL,     -- the layout map JSON (render/pdf.py)
        created_at      TEXT NOT NULL,
        FOREIGN KEY (server, asset_uid, version_uid) REFERENCES forms
    );
    CREATE TABLE sheets (
        sheet_id    TEXT PRIMARY KEY,      -- uuid4 hex, in the QR code; the instanceID
        layout_hash TEXT NOT NULL REFERENCES layouts,
        batch       TEXT NOT NULL,         -- one per `kff render`
        copy        INTEGER NOT NULL,
        created_at  TEXT NOT NULL
    );
    CREATE INDEX sheets_batch ON sheets (batch);
    """,
    3: """
    CREATE TABLE scans (
        scan_id     TEXT PRIMARY KEY,
        sheet_id    TEXT NOT NULL,         -- from the QR code; may be unknown to sheets
        page        INTEGER NOT NULL,
        layout_hash TEXT NOT NULL REFERENCES layouts,
        source      TEXT NOT NULL,         -- file name, `#n` for a page in a PDF
        image       TEXT NOT NULL,         -- registered page PNG, relative to KFF_HOME
        residual_mm REAL NOT NULL,
        markers     INTEGER NOT NULL,      -- corner markers found (3 or 4)
        created_at  TEXT NOT NULL,
        current     INTEGER NOT NULL DEFAULT 1  -- 0 once a rescan of the page replaces it
    );
    CREATE INDEX scans_sheet ON scans (sheet_id, page);
    """,
    4: """
    CREATE TABLE readings (
        sheet_id    TEXT NOT NULL,
        field_id    TEXT NOT NULL,         -- xpath, repeat instances in brackets
        value       TEXT,                  -- Kobo submission format; NULL: blank
        confidence  REAL NOT NULL,
        method      TEXT NOT NULL,         -- omr | digits | vlm | ink | none | person
        reason      TEXT,                  -- why a person should check it
        needs_review INTEGER NOT NULL,
        detail      TEXT NOT NULL,         -- JSON: fills, digits as written, …
        reviewed    INTEGER NOT NULL DEFAULT 0,  -- 1: a person confirmed or corrected it
        updated_at  TEXT NOT NULL,
        PRIMARY KEY (sheet_id, field_id)
    );
    CREATE TABLE submissions (
        sheet_id    TEXT NOT NULL,
        instance_id TEXT NOT NULL,         -- uuid:<sheet id>
        status      INTEGER NOT NULL,      -- HTTP status: 201 created, 202 duplicate
        message     TEXT,
        submitted_at TEXT NOT NULL
    );
    CREATE INDEX submissions_sheet ON submissions (sheet_id);
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
        fetched_at = _now()
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

    # -- layouts and sheets -------------------------------------------------

    def save_layout(self, layout: dict[str, Any]) -> None:
        """Keep a layout map. The hash covers everything that shapes the layout, so an
        existing row is the same layout and is left alone."""
        form = layout["form"]
        with self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO layouts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    layout["layout_hash"],
                    form["server"],
                    form["asset_uid"],
                    form["version_uid"],
                    layout["pages_per_sheet"],
                    json.dumps(layout, ensure_ascii=False),
                    _now(),
                ),
            )

    def get_layout(self, layout_hash: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT layout FROM layouts WHERE layout_hash = ?", (layout_hash,)
        ).fetchone()
        return json.loads(row["layout"]) if row else None

    def save_sheets(self, layout_hash: str, batch: str, sheet_ids: list[str]) -> None:
        now = _now()
        with self._db:
            self._db.executemany(
                "INSERT INTO sheets VALUES (?, ?, ?, ?, ?)",
                [(sid, layout_hash, batch, i + 1, now) for i, sid in enumerate(sheet_ids)],
            )

    def get_sheet(self, sheet_id: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM sheets WHERE sheet_id = ?", (sheet_id,)).fetchone()
        return dict(row) if row else None

    def batches(self) -> list[dict[str, Any]]:
        """Printed batches, newest first, with their form and sheet count."""
        rows = self._db.execute(
            """
            SELECT s.batch, s.layout_hash, l.asset_uid, l.version_uid, f.name,
                   COUNT(*) AS sheets, MIN(s.created_at) AS created_at
            FROM sheets s
            JOIN layouts l USING (layout_hash)
            JOIN forms f USING (server, asset_uid, version_uid)
            GROUP BY s.batch ORDER BY created_at DESC, s.batch
            """
        )
        return [dict(r) for r in rows]

    # -- scans --------------------------------------------------------------

    def save_scan(self, scan: dict[str, Any]) -> bool:
        """Record a registered page. A rescan of the same sheet page replaces the earlier
        one (which is kept, not current). Returns True if it replaced one."""
        with self._db:
            replaced = self._db.execute(
                "UPDATE scans SET current = 0 WHERE sheet_id = ? AND page = ? AND current = 1",
                (scan["sheet_id"], scan["page"]),
            ).rowcount
            self._db.execute(
                """INSERT INTO scans (scan_id, sheet_id, page, layout_hash, source, image,
                residual_mm, markers, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scan["scan_id"],
                    scan["sheet_id"],
                    scan["page"],
                    scan["layout_hash"],
                    scan["source"],
                    scan["image"],
                    scan["residual_mm"],
                    scan["markers"],
                    _now(),
                ),
            )
        return bool(replaced)

    def scans(self, sheet_id: str | None = None) -> list[dict[str, Any]]:
        """Current scans, by sheet then page."""
        sql = "SELECT * FROM scans WHERE current = 1"
        args: tuple[str, ...] = ()
        if sheet_id:
            sql += " AND sheet_id = ?"
            args = (sheet_id,)
        return [dict(r) for r in self._db.execute(sql + " ORDER BY sheet_id, page", args)]

    def get_scan(self, scan_id: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM scans WHERE scan_id = ?", (scan_id,)).fetchone()
        return dict(row) if row else None

    # -- readings and submissions --------------------------------------------

    def save_readings(self, sheet_id: str, readings: list[dict[str, Any]]) -> None:
        """Store machine readings; a reading a person reviewed is kept as it is."""
        now = _now()
        with self._db:
            for r in readings:
                self._db.execute(
                    """
                    INSERT INTO readings (sheet_id, field_id, value, confidence, method, reason,
                        needs_review, detail, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (sheet_id, field_id) DO UPDATE SET
                        value = excluded.value, confidence = excluded.confidence,
                        method = excluded.method, reason = excluded.reason,
                        needs_review = excluded.needs_review, detail = excluded.detail,
                        updated_at = excluded.updated_at
                    WHERE readings.reviewed = 0
                    """,
                    (
                        sheet_id,
                        r["field_id"],
                        r["value"],
                        r["confidence"],
                        r["method"],
                        r["reason"],
                        int(r["needs_review"]),
                        json.dumps(r.get("detail") or {}, ensure_ascii=False),
                        now,
                    ),
                )

    def readings(self, sheet_id: str) -> dict[str, dict[str, Any]]:
        rows = self._db.execute("SELECT * FROM readings WHERE sheet_id = ?", (sheet_id,))
        return {r["field_id"]: {**dict(r), "detail": json.loads(r["detail"])} for r in rows}

    def review(self, sheet_id: str, field_id: str, value: str | None) -> None:
        """A person's answer for a field: confirmed or corrected."""
        with self._db:
            self._db.execute(
                """
                INSERT INTO readings (sheet_id, field_id, value, confidence, method, reason,
                    needs_review, detail, reviewed, updated_at)
                VALUES (?, ?, ?, 1.0, 'person', NULL, 0, '{}', 1, ?)
                ON CONFLICT (sheet_id, field_id) DO UPDATE SET
                    value = excluded.value, confidence = 1.0, method = 'person', reason = NULL,
                    needs_review = 0, reviewed = 1, updated_at = excluded.updated_at
                """,
                (sheet_id, field_id, value, _now()),
            )

    def record_submission(self, sheet_id: str, instance_id: str, status: int, message: str):
        with self._db:
            self._db.execute(
                "INSERT INTO submissions VALUES (?, ?, ?, ?, ?)",
                (sheet_id, instance_id, status, message, _now()),
            )

    def submission(self, sheet_id: str) -> dict[str, Any] | None:
        """The latest successful (201/202) submission of a sheet."""
        row = self._db.execute(
            """SELECT * FROM submissions WHERE sheet_id = ? AND status IN (201, 202)
            ORDER BY submitted_at DESC LIMIT 1""",
            (sheet_id,),
        ).fetchone()
        return dict(row) if row else None

    def scanned_sheets(self) -> list[dict[str, Any]]:
        """Every sheet with a current scan: its layout, form and pages found."""
        rows = self._db.execute(
            """
            SELECT s.sheet_id, s.layout_hash, l.asset_uid, l.version_uid, l.pages_per_sheet,
                   f.name, COUNT(*) AS pages_found, MAX(s.created_at) AS scanned_at
            FROM scans s JOIN layouts l USING (layout_hash)
            JOIN forms f USING (server, asset_uid, version_uid)
            WHERE s.current = 1
            GROUP BY s.sheet_id ORDER BY scanned_at DESC
            """
        )
        return [dict(r) for r in rows]


def new_batch_id() -> str:
    """One per render: when, plus a little randomness (`20261029-115054-c1cc`)."""
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _form(row: sqlite3.Row) -> FormVersion:
    return FormVersion(**{**dict(row), "content": json.loads(row["content"])})
