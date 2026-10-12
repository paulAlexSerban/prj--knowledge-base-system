"""Engineering epics in SQLite. SQLAlchemy owns every query. Deletes are hard deletes."""

import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy import Index, delete, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

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
NATIVE_ACLS = frozenset({"project_member", "eng_leadership", "roadmap_readers"})
STATUSES = frozenset({"planned", "in_progress", "released"})
TEAMS = frozenset(
    {"people", "platform", "payments", "identity", "data", "product", "sales-emea", "sales-amer"}
)
PRODUCT_KEYS = frozenset(
    {"checkout", "billing", "identity", "mobile", "analytics", "notifications"}
)
_EPIC_ID = re.compile(r"^epic-[a-z0-9-]{3,60}$")


class Base(DeclarativeBase):
    pass


class Epic(Base):
    __tablename__ = "epics"
    __table_args__ = (Index("epics_updated", "updated_at", "epic_id"),)

    epic_id: Mapped[str] = mapped_column(primary_key=True)
    title: Mapped[str]
    status: Mapped[str]
    product_key: Mapped[str]
    team: Mapped[str]
    native_acl: Mapped[str]
    version: Mapped[int]
    updated_at: Mapped[str]


def record(row: Epic) -> dict[str, object]:
    return {
        "epic_id": row.epic_id,
        "title": row.title,
        "status": row.status,
        "product_key": row.product_key,
        "team": row.team,
        "native_acl": split_acl(row.native_acl),
        "version": row.version,
        "updated_at": row.updated_at,
    }


class Store:
    def __init__(self, db_path: str | Path, clock: Clock = utc_now) -> None:
        self.db = Database(db_path)
        self.clock = clock
        Base.metadata.create_all(self.db.engine)

    def now(self) -> str:
        return format_ts(self.clock())

    def counts(self) -> dict[str, int]:
        with self.db.session() as db:
            total = db.scalar(select(func.count()).select_from(Epic)) or 0
        return {"epic": int(total)}

    def is_empty(self) -> bool:
        return self.counts()["epic"] == 0

    def get(self, epic_id: str) -> dict[str, object]:
        with self.db.session() as db:
            row = db.get(Epic, epic_id)
            if row is None:
                raise NotFound(epic_id)
            return record(row)

    def ids(self) -> dict[str, list[dict[str, object]]]:
        with self.db.session() as db:
            rows = db.execute(select(Epic.epic_id, Epic.version).order_by(Epic.epic_id)).all()
            return {"epic": [{"id": row.epic_id, "version": row.version} for row in rows]}

    def changes(
        self, updated_since: str | None, page_token: str | None, limit: int
    ) -> tuple[list[dict[str, object]], str | None]:
        with self.db.session() as db:
            page, token = fetch_keyset(
                db,
                Epic,
                id_column=Epic.epic_id,
                updated_column=Epic.updated_at,
                updated_since=updated_since,
                page_token=page_token,
                limit=limit,
            )
            items = [
                {
                    "object_type": "epic",
                    "id": row.epic_id,
                    "version": row.version,
                    "updated_at": row.updated_at,
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
        with self.db.session() as db:
            if db.get(Epic, epic_id) is not None:
                raise Conflict(epic_id)
            saved = Epic(
                epic_id=epic_id,
                title=title.strip(),
                status=status,
                product_key=product_key,
                team=team,
                native_acl=acl,
                version=1,
                updated_at=stamp,
            )
            db.add(saved)
            return record(saved)

    def update(self, epic_id: str, fields: dict[str, object]) -> dict[str, object]:
        unknown = set(fields) - {"title", "status", "product_key", "team"}
        if unknown:
            raise Invalid(f"unknown fields: {sorted(unknown)}")
        with self.db.session() as db:
            row = db.get(Epic, epic_id)
            if row is None:
                raise NotFound(epic_id)
            title = str(fields.get("title", row.title))
            status = str(fields.get("status", row.status))
            product_key = str(fields.get("product_key", row.product_key))
            team = str(fields.get("team", row.team))
            self._check(title, status, product_key, team)
            title = title.strip()
            if (
                title == row.title
                and status == row.status
                and product_key == row.product_key
                and team == row.team
            ):
                return record(row)
            row.title = title
            row.status = status
            row.product_key = product_key
            row.team = team
            row.version += 1
            row.updated_at = self.now()
            return record(row)

    def set_acl(self, epic_id: str, native_acl: list[str]) -> dict[str, object]:
        acl = check_acl(list(native_acl), NATIVE_ACLS)
        with self.db.session() as db:
            row = db.get(Epic, epic_id)
            if row is None:
                raise NotFound(epic_id)
            if row.native_acl == acl:
                return record(row)
            row.native_acl = acl
            row.updated_at = self.now()
            return record(row)

    def delete(self, epic_id: str) -> None:
        with self.db.session() as db:
            row = db.get(Epic, epic_id)
            if row is None:
                raise NotFound(epic_id)
            db.delete(row)

    def reset(self) -> None:
        with self.db.session() as db:
            db.execute(delete(Epic))

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
