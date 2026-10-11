"""SQLite CRM. The change feed is a cursor log, including deletes.

`commission_rate` is stored and never returned. Account fields live on the deal.
"""

import json
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
    format_ts,
    split_acl,
    utc_now,
)

Clock = Callable[[], datetime]
NATIVE_ACLS = frozenset({"account_team", "sales_leadership"})
STAGES = frozenset({"prospect", "qualified", "proposal", "negotiation", "won", "lost"})
PRODUCT_KEYS = frozenset(
    {"checkout", "billing", "identity", "mobile", "analytics", "notifications"}
)
_DEAL_ID = re.compile(r"^deal-[a-z0-9-]{3,60}$")
_ACCOUNT_ID = re.compile(r"^acc-[a-z0-9-]{3,40}$")
_EPIC_ID = re.compile(r"^epic-[a-z0-9-]{3,60}$")
_QUARTER = re.compile(r"^\d{4}-Q[1-4]$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS deals (
    deal_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    account_name TEXT NOT NULL,
    arr INTEGER NOT NULL,
    quarter TEXT NOT NULL,
    stage TEXT NOT NULL,
    owner_email TEXT NOT NULL,
    product_key TEXT NOT NULL,
    epic_id TEXT,
    native_acl TEXT NOT NULL,
    commission_rate REAL NOT NULL,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS change_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    op TEXT NOT NULL,
    deal_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    record_json TEXT NOT NULL
);
"""


def public(row: object) -> dict[str, object]:
    """The record a connector may snapshot. commission_rate is not here."""
    return {
        "deal_id": row["deal_id"],
        "account_id": row["account_id"],
        "account_name": row["account_name"],
        "arr": row["arr"],
        "quarter": row["quarter"],
        "stage": row["stage"],
        "owner_email": row["owner_email"],
        "product_key": row["product_key"],
        "epic_id": row["epic_id"],
        "native_acl": split_acl(row["native_acl"]),
        "version": row["version"],
        "updated_at": row["updated_at"],
    }


def _cursor(value: str | None) -> int:
    if not value:
        return 0
    if not str(value).isdigit():
        raise Invalid("cursor must be a non-negative integer")
    return int(value)


def _epic(value: object) -> str | None:
    if value is None or value == "":
        return None
    text = str(value)
    if not _EPIC_ID.match(text):
        raise Invalid("epic_id must look like epic-name")
    return text


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
            row = connection.execute("SELECT count(*) AS n FROM deals").fetchone()
        return {"deal": int(row["n"])}

    def is_empty(self) -> bool:
        return self.counts()["deal"] == 0

    def get(self, deal_id: str) -> dict[str, object]:
        with self.db.session() as connection:
            row = self._row(connection, deal_id)
            return public(row)

    def list_deals(self) -> list[dict[str, object]]:
        with self.db.session() as connection:
            rows = connection.execute("SELECT * FROM deals ORDER BY deal_id").fetchall()
        return [public(row) for row in rows]

    def ids(self) -> dict[str, list[dict[str, object]]]:
        with self.db.session() as connection:
            rows = connection.execute(
                "SELECT deal_id, version FROM deals ORDER BY deal_id"
            ).fetchall()
        return {"deal": [{"id": row["deal_id"], "version": row["version"]} for row in rows]}

    def changes(self, cursor: str | None, limit: int) -> tuple[list[dict[str, object]], str, bool]:
        if limit < 1:
            raise Invalid("limit must be at least 1")
        start = _cursor(cursor)
        with self.db.session() as connection:
            rows = connection.execute(
                """
                SELECT seq, op, deal_id, version, updated_at, record_json
                FROM change_log WHERE seq > ? ORDER BY seq LIMIT ?
                """,
                (start, limit + 1),
            ).fetchall()
        more = len(rows) > limit
        page = rows[:limit]
        next_cursor = str(page[-1]["seq"]) if page else str(start)
        return [_change(row) for row in page], next_cursor, more

    def create(
        self,
        deal_id: str,
        account_id: str,
        account_name: str,
        arr: int,
        quarter: str,
        stage: str,
        owner_email: str,
        product_key: str,
        epic_id: str | None,
        native_acl: list[str],
        commission_rate: float = 0.08,
        at: str | None = None,
    ) -> dict[str, object]:
        self._check_id(deal_id, account_id)
        self._check(account_name, arr, quarter, stage, owner_email, product_key)
        epic = _epic(epic_id)
        acl = check_acl(list(native_acl), NATIVE_ACLS)
        if commission_rate < 0:
            raise Invalid("commission_rate cannot be negative")
        stamp = at or self.now()
        with self.db.session() as connection:
            found = connection.execute(
                "SELECT 1 FROM deals WHERE deal_id = ?", (deal_id,)
            ).fetchone()
            if found is not None:
                raise Conflict(deal_id)
            connection.execute(
                """
                INSERT INTO deals (
                    deal_id, account_id, account_name, arr, quarter, stage, owner_email,
                    product_key, epic_id, native_acl, commission_rate, version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    deal_id,
                    account_id,
                    account_name.strip(),
                    int(arr),
                    quarter,
                    stage,
                    owner_email,
                    product_key,
                    epic,
                    acl,
                    float(commission_rate),
                    stamp,
                ),
            )
            saved = self._row(connection, deal_id)
            self._log(connection, "upsert", saved)
        return public(saved)

    def update(self, deal_id: str, fields: dict[str, object]) -> dict[str, object]:
        unknown = set(fields) - {
            "account_name",
            "arr",
            "quarter",
            "stage",
            "owner_email",
            "product_key",
            "epic_id",
        }
        if unknown:
            raise Invalid(f"unknown fields: {sorted(unknown)}")
        with self.db.session() as connection:
            row = self._row(connection, deal_id)
            account_name = str(fields.get("account_name", row["account_name"]))
            arr = int(fields["arr"]) if "arr" in fields else int(row["arr"])
            quarter = str(fields.get("quarter", row["quarter"]))
            stage = str(fields.get("stage", row["stage"]))
            owner_email = str(fields.get("owner_email", row["owner_email"]))
            product_key = str(fields.get("product_key", row["product_key"]))
            epic = _epic(fields["epic_id"]) if "epic_id" in fields else row["epic_id"]
            self._check(account_name, arr, quarter, stage, owner_email, product_key)
            account_name = account_name.strip()
            if (
                account_name == row["account_name"]
                and arr == row["arr"]
                and quarter == row["quarter"]
                and stage == row["stage"]
                and owner_email == row["owner_email"]
                and product_key == row["product_key"]
                and epic == row["epic_id"]
            ):
                return public(row)
            stamp = self.now()
            connection.execute(
                """
                UPDATE deals
                SET account_name = ?, arr = ?, quarter = ?, stage = ?, owner_email = ?,
                    product_key = ?, epic_id = ?, version = ?, updated_at = ?
                WHERE deal_id = ?
                """,
                (
                    account_name,
                    arr,
                    quarter,
                    stage,
                    owner_email,
                    product_key,
                    epic,
                    row["version"] + 1,
                    stamp,
                    deal_id,
                ),
            )
            saved = self._row(connection, deal_id)
            self._log(connection, "upsert", saved)
        return public(saved)

    def set_acl(self, deal_id: str, native_acl: list[str]) -> dict[str, object]:
        acl = check_acl(list(native_acl), NATIVE_ACLS)
        with self.db.session() as connection:
            row = self._row(connection, deal_id)
            if row["native_acl"] == acl:
                return public(row)
            connection.execute(
                "UPDATE deals SET native_acl = ?, updated_at = ? WHERE deal_id = ?",
                (acl, self.now(), deal_id),
            )
            saved = self._row(connection, deal_id)
            self._log(connection, "upsert", saved)
        return public(saved)

    def delete(self, deal_id: str) -> None:
        with self.db.session() as connection:
            row = self._row(connection, deal_id)
            stamp = self.now()
            connection.execute("DELETE FROM deals WHERE deal_id = ?", (deal_id,))
            self._log_delete(connection, deal_id, int(row["version"]), stamp)

    def reset(self) -> None:
        with self.db.session() as connection:
            connection.execute("DELETE FROM deals")
            connection.execute("DELETE FROM change_log")

    def _row(self, connection: object, deal_id: str):
        row = connection.execute("SELECT * FROM deals WHERE deal_id = ?", (deal_id,)).fetchone()
        if row is None:
            raise NotFound(deal_id)
        return row

    @staticmethod
    def _log(connection: object, op: str, row: object) -> None:
        body = public(row)
        connection.execute(
            """
            INSERT INTO change_log (op, deal_id, version, updated_at, record_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                op,
                row["deal_id"],
                row["version"],
                row["updated_at"],
                json.dumps(body, separators=(",", ":"), sort_keys=True),
            ),
        )

    @staticmethod
    def _log_delete(connection: object, deal_id: str, version: int, stamp: str) -> None:
        connection.execute(
            """
            INSERT INTO change_log (op, deal_id, version, updated_at, record_json)
            VALUES ('delete', ?, ?, ?, '{}')
            """,
            (deal_id, version, stamp),
        )

    @staticmethod
    def _check_id(deal_id: str, account_id: str) -> None:
        if not _DEAL_ID.match(deal_id):
            raise Invalid("deal_id must look like deal-name")
        if not _ACCOUNT_ID.match(account_id):
            raise Invalid("account_id must look like acc-name")

    @staticmethod
    def _check(
        account_name: str,
        arr: int,
        quarter: str,
        stage: str,
        owner_email: str,
        product_key: str,
    ) -> None:
        if not account_name.strip():
            raise Invalid("account_name is required")
        if arr < 0:
            raise Invalid("arr cannot be negative")
        if not _QUARTER.match(quarter):
            raise Invalid("quarter must look like 2026-Q3")
        if stage not in STAGES:
            raise Invalid(f"stage must be one of {sorted(STAGES)}")
        if "@" not in owner_email:
            raise Invalid("owner_email must be an email address")
        if product_key not in PRODUCT_KEYS:
            raise Invalid(f"unknown product_key: {product_key}")


def _change(row: object) -> dict[str, object]:
    item: dict[str, object] = {
        "op": row["op"],
        "object_type": "deal",
        "id": row["deal_id"],
        "version": row["version"],
        "updated_at": row["updated_at"],
    }
    if row["op"] == "upsert":
        item["record"] = json.loads(row["record_json"])
    return item
