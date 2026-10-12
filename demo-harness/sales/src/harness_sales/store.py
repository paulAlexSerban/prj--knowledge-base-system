"""CRM deals in SQLite. SQLAlchemy owns every query.

The change feed is a cursor log, including deletes. `commission_rate` is stored
and never returned. Account fields live on the deal.
"""

import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from sqlalchemy import delete, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

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
NATIVE_ACLS = frozenset({"account_team", "sales_leadership", "roadmap_readers"})
STAGES = frozenset({"prospect", "qualified", "proposal", "negotiation", "won", "lost"})
PRODUCT_KEYS = frozenset(
    {"checkout", "billing", "identity", "mobile", "analytics", "notifications"}
)
_DEAL_ID = re.compile(r"^deal-[a-z0-9-]{3,60}$")
_ACCOUNT_ID = re.compile(r"^acc-[a-z0-9-]{3,40}$")
_EPIC_ID = re.compile(r"^epic-[a-z0-9-]{3,60}$")
_QUARTER = re.compile(r"^\d{4}-Q[1-4]$")


class Base(DeclarativeBase):
    pass


class Deal(Base):
    __tablename__ = "deals"

    deal_id: Mapped[str] = mapped_column(primary_key=True)
    account_id: Mapped[str]
    account_name: Mapped[str]
    arr: Mapped[int]
    quarter: Mapped[str]
    stage: Mapped[str]
    owner_email: Mapped[str]
    product_key: Mapped[str]
    epic_id: Mapped[str | None]
    native_acl: Mapped[str]
    commission_rate: Mapped[float]
    version: Mapped[int]
    updated_at: Mapped[str]


class ChangeLog(Base):
    __tablename__ = "change_log"
    __table_args__: ClassVar[dict[str, bool]] = {"sqlite_autoincrement": True}

    seq: Mapped[int] = mapped_column(primary_key=True)
    op: Mapped[str]
    deal_id: Mapped[str]
    version: Mapped[int]
    updated_at: Mapped[str]
    record_json: Mapped[str]


def public(row: Deal) -> dict[str, object]:
    """The record a connector may snapshot. commission_rate is not here."""
    return {
        "deal_id": row.deal_id,
        "account_id": row.account_id,
        "account_name": row.account_name,
        "arr": row.arr,
        "quarter": row.quarter,
        "stage": row.stage,
        "owner_email": row.owner_email,
        "product_key": row.product_key,
        "epic_id": row.epic_id,
        "native_acl": split_acl(row.native_acl),
        "version": row.version,
        "updated_at": row.updated_at,
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


def _change(row: ChangeLog) -> dict[str, object]:
    item: dict[str, object] = {
        "op": row.op,
        "object_type": "deal",
        "id": row.deal_id,
        "version": row.version,
        "updated_at": row.updated_at,
    }
    if row.op == "upsert":
        item["record"] = json.loads(row.record_json)
    return item


class Store:
    def __init__(self, db_path: str | Path, clock: Clock = utc_now) -> None:
        self.db = Database(db_path)
        self.clock = clock
        Base.metadata.create_all(self.db.engine)

    def now(self) -> str:
        return format_ts(self.clock())

    def counts(self) -> dict[str, int]:
        with self.db.session() as db:
            total = db.scalar(select(func.count()).select_from(Deal)) or 0
        return {"deal": int(total)}

    def is_empty(self) -> bool:
        return self.counts()["deal"] == 0

    def get(self, deal_id: str) -> dict[str, object]:
        with self.db.session() as db:
            return public(self._row(db, deal_id))

    def list_deals(self) -> list[dict[str, object]]:
        with self.db.session() as db:
            rows = db.scalars(select(Deal).order_by(Deal.deal_id)).all()
            return [public(row) for row in rows]

    def ids(self) -> dict[str, list[dict[str, object]]]:
        with self.db.session() as db:
            rows = db.execute(select(Deal.deal_id, Deal.version).order_by(Deal.deal_id)).all()
            return {"deal": [{"id": row.deal_id, "version": row.version} for row in rows]}

    def changes(self, cursor: str | None, limit: int) -> tuple[list[dict[str, object]], str, bool]:
        if limit < 1:
            raise Invalid("limit must be at least 1")
        start = _cursor(cursor)
        with self.db.session() as db:
            rows = db.scalars(
                select(ChangeLog)
                .where(ChangeLog.seq > start)
                .order_by(ChangeLog.seq)
                .limit(limit + 1)
            ).all()
            more = len(rows) > limit
            page = rows[:limit]
            next_cursor = str(page[-1].seq) if page else str(start)
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
        with self.db.session() as db:
            if db.get(Deal, deal_id) is not None:
                raise Conflict(deal_id)
            saved = Deal(
                deal_id=deal_id,
                account_id=account_id,
                account_name=account_name.strip(),
                arr=int(arr),
                quarter=quarter,
                stage=stage,
                owner_email=owner_email,
                product_key=product_key,
                epic_id=epic,
                native_acl=acl,
                commission_rate=float(commission_rate),
                version=1,
                updated_at=stamp,
            )
            db.add(saved)
            self._log(db, "upsert", saved)
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
        with self.db.session() as db:
            row = self._row(db, deal_id)
            account_name = str(fields.get("account_name", row.account_name))
            arr = int(fields["arr"]) if "arr" in fields else int(row.arr)
            quarter = str(fields.get("quarter", row.quarter))
            stage = str(fields.get("stage", row.stage))
            owner_email = str(fields.get("owner_email", row.owner_email))
            product_key = str(fields.get("product_key", row.product_key))
            epic = _epic(fields["epic_id"]) if "epic_id" in fields else row.epic_id
            self._check(account_name, arr, quarter, stage, owner_email, product_key)
            account_name = account_name.strip()
            if (
                account_name == row.account_name
                and arr == row.arr
                and quarter == row.quarter
                and stage == row.stage
                and owner_email == row.owner_email
                and product_key == row.product_key
                and epic == row.epic_id
            ):
                return public(row)
            row.account_name = account_name
            row.arr = arr
            row.quarter = quarter
            row.stage = stage
            row.owner_email = owner_email
            row.product_key = product_key
            row.epic_id = epic
            row.version += 1
            row.updated_at = self.now()
            self._log(db, "upsert", row)
            return public(row)

    def set_acl(self, deal_id: str, native_acl: list[str]) -> dict[str, object]:
        acl = check_acl(list(native_acl), NATIVE_ACLS)
        with self.db.session() as db:
            row = self._row(db, deal_id)
            if row.native_acl == acl:
                return public(row)
            row.native_acl = acl
            row.updated_at = self.now()
            self._log(db, "upsert", row)
            return public(row)

    def delete(self, deal_id: str) -> None:
        with self.db.session() as db:
            row = self._row(db, deal_id)
            version = int(row.version)
            stamp = self.now()
            db.delete(row)
            self._log_delete(db, deal_id, version, stamp)

    def reset(self) -> None:
        with self.db.session() as db:
            db.execute(delete(Deal))
            db.execute(delete(ChangeLog))

    @staticmethod
    def _row(db: Session, deal_id: str) -> Deal:
        row = db.get(Deal, deal_id)
        if row is None:
            raise NotFound(deal_id)
        return row

    @staticmethod
    def _log(db: Session, op: str, row: Deal) -> None:
        db.add(
            ChangeLog(
                op=op,
                deal_id=row.deal_id,
                version=row.version,
                updated_at=row.updated_at,
                record_json=json.dumps(public(row), separators=(",", ":"), sort_keys=True),
            )
        )

    @staticmethod
    def _log_delete(db: Session, deal_id: str, version: int, stamp: str) -> None:
        db.add(
            ChangeLog(
                op="delete",
                deal_id=deal_id,
                version=version,
                updated_at=stamp,
                record_json="{}",
            )
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
