"""SQLite store for engineering epics. Deletes are hard deletes."""

import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from harness_common.db import (
    Conflict,
    Database,
    Invalid,
    NotFound,
    check_acl,
    fetch_keyset,
    format_ts,
    split_acl,
    utc_now,
)

Clock = Callable[[], datetime]
NATIVE_ACLS = frozenset({"project_member", "eng_leadership"})
STATUSES = frozenset({"planned", "in_progress", "released"})
TEAMS = frozenset(
    {"people", "platform", "payments", "identity", "data", "product", "sales-emea", "sales-amer"}
)
PRODUCT_KEYS = frozenset(
    {"checkout", "billing", "identity", "mobile", "analytics", "notifications"}
)
_EPIC_ID = re.compile(r"^epic-[a-z0-9-]{3,60}$")
_COLUMNS = (
    "epic_id",
    "title",
    "status",
    "product_key",
    "team",
    "native_acl",
    "version",
    "updated_at",
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS epics (
    epic_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    product_key TEXT NOT NULL,
    team TEXT NOT NULL,
    native_acl TEXT NOT NULL,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS epics_updated ON epics (updated_at, epic_id);
"""


def record(row: object) -> dict[str, object]:
    return {
        "epic_id": row["epic_id"],
        "title": row["title"],
        "status": row["status"],
        "product_key": row["product_key"],
        "team": row["team"],
        "native_acl": split_acl(row["native_acl"]),
        "version": row["version"],
        "updated_at": row["updated_at"],
    }


class Store:
    def __init__(self, db_path: str | Path, clock: Clock = utc_now) -> None:
        self.db = Database(db_path)
        self.clock = clock
        with self.db.session() as connection:
            connection.executescript(SCHEMA)

    def now(self) -> str:
        return format_ts(self.clock())

    def counts(self) -> dict[str, int]:
        with self.db.session() as connection:
            row = connection.execute("SELECT count(*) AS n FROM epics").fetchone()
        return {"epic": int(row["n"])}

    def is_empty(self) -> bool:
        return self.counts()["epic"] == 0

    def get(self, epic_id: str) -> dict[str, object]:
        with self.db.session() as connection:
            row = connection.execute("SELECT * FROM epics WHERE epic_id = ?", (epic_id,)).fetchone()
            if row is None:
                raise NotFound(epic_id)
            return record(row)

    def ids(self) -> dict[str, list[dict[str, object]]]:
        with self.db.session() as connection:
            rows = connection.execute(
                "SELECT epic_id, version FROM epics ORDER BY epic_id"
            ).fetchall()
        return {"epic": [{"id": row["epic_id"], "version": row["version"]} for row in rows]}

    def changes(
        self, updated_since: str | None, page_token: str | None, limit: int
    ) -> tuple[list[dict[str, object]], str | None]:
        with self.db.session() as connection:
            page, token = fetch_keyset(
                connection,
                table="epics",
                columns=_COLUMNS,
                id_column="epic_id",
                updated_since=updated_since,
                page_token=page_token,
                limit=limit,
            )
        items = [
            {
                "object_type": "epic",
                "id": row["epic_id"],
                "version": row["version"],
                "updated_at": row["updated_at"],
                "record": record(row),
            }
            for row in page
        ]
        return items, token

    def create(
        self,
        epic_id: str,
        title: str,
        status: str,
        product_key: str,
        team: str,
        native_acl: list[str],
        at: str | None = None,
    ) -> dict[str, object]:
        if not _EPIC_ID.match(epic_id):
            raise Invalid(f"epic_id must match {_EPIC_ID.pattern}")
        self._check(title, status, product_key, team)
        acl = check_acl(list(native_acl), NATIVE_ACLS)
        stamp = at or self.now()
        with self.db.session() as connection:
            found = connection.execute(
                "SELECT 1 FROM epics WHERE epic_id = ?", (epic_id,)
            ).fetchone()
            if found is not None:
                raise Conflict(epic_id)
            connection.execute(
                """
                INSERT INTO epics (
                    epic_id, title, status, product_key, team, native_acl, version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (epic_id, title.strip(), status, product_key, team, acl, stamp),
            )
            row = connection.execute("SELECT * FROM epics WHERE epic_id = ?", (epic_id,)).fetchone()
        return record(row)

    def update(self, epic_id: str, fields: dict[str, object]) -> dict[str, object]:
        unknown = set(fields) - {"title", "status", "product_key", "team"}
        if unknown:
            raise Invalid(f"unknown fields: {sorted(unknown)}")
        with self.db.session() as connection:
            row = connection.execute("SELECT * FROM epics WHERE epic_id = ?", (epic_id,)).fetchone()
            if row is None:
                raise NotFound(epic_id)
            title = str(fields.get("title", row["title"]))
            status = str(fields.get("status", row["status"]))
            product_key = str(fields.get("product_key", row["product_key"]))
            team = str(fields.get("team", row["team"]))
            self._check(title, status, product_key, team)
            title = title.strip()
            if (
                title == row["title"]
                and status == row["status"]
                and product_key == row["product_key"]
                and team == row["team"]
            ):
                return record(row)
            connection.execute(
                """
                UPDATE epics
                SET title = ?, status = ?, product_key = ?, team = ?,
                    version = ?, updated_at = ?
                WHERE epic_id = ?
                """,
                (title, status, product_key, team, row["version"] + 1, self.now(), epic_id),
            )
            saved = connection.execute(
                "SELECT * FROM epics WHERE epic_id = ?", (epic_id,)
            ).fetchone()
        return record(saved)

    def set_acl(self, epic_id: str, native_acl: list[str]) -> dict[str, object]:
        acl = check_acl(list(native_acl), NATIVE_ACLS)
        with self.db.session() as connection:
            row = connection.execute("SELECT * FROM epics WHERE epic_id = ?", (epic_id,)).fetchone()
            if row is None:
                raise NotFound(epic_id)
            if row["native_acl"] == acl:
                return record(row)
            connection.execute(
                "UPDATE epics SET native_acl = ?, updated_at = ? WHERE epic_id = ?",
                (acl, self.now(), epic_id),
            )
            saved = connection.execute(
                "SELECT * FROM epics WHERE epic_id = ?", (epic_id,)
            ).fetchone()
        return record(saved)

    def delete(self, epic_id: str) -> None:
        with self.db.session() as connection:
            row = connection.execute("SELECT 1 FROM epics WHERE epic_id = ?", (epic_id,)).fetchone()
            if row is None:
                raise NotFound(epic_id)
            connection.execute("DELETE FROM epics WHERE epic_id = ?", (epic_id,))

    def reset(self) -> None:
        with self.db.session() as connection:
            connection.execute("DELETE FROM epics")

    @staticmethod
    def _check(title: str, status: str, product_key: str, team: str) -> None:
        if not title.strip():
            raise Invalid("title is required")
        if status not in STATUSES:
            raise Invalid(f"status must be one of {sorted(STATUSES)}")
        if product_key not in PRODUCT_KEYS:
            raise Invalid(f"unknown product_key: {product_key}")
        if team not in TEAMS:
            raise Invalid(f"unknown team: {team}")
