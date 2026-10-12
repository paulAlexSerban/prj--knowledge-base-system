"""Spec metadata in SQLite and the markdown body on disk. SQLAlchemy owns every query.

Version moves when the title or the body changes. Native ACLs are
product_org and roadmap_readers. Repeating the current ACL does not move
the version.
A lost content webhook is recovered because /ids shows the new version.
"""

import hashlib
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

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
NATIVE_ACLS = frozenset({"product_org", "roadmap_readers"})
PRODUCT_KEYS = frozenset(
    {"checkout", "billing", "identity", "mobile", "analytics", "notifications"}
)
_SPEC_ID = re.compile(r"^spec-[a-z0-9-]{3,60}$")


class Base(DeclarativeBase):
    pass


class Spec(Base):
    __tablename__ = "specs"

    spec_id: Mapped[str] = mapped_column(primary_key=True)
    title: Mapped[str]
    product_key: Mapped[str]
    file_name: Mapped[str]
    native_acl: Mapped[str]
    version: Mapped[int]
    content_hash: Mapped[str]
    updated_at: Mapped[str]


def _hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def record(row: Spec) -> dict[str, object]:
    return {
        "spec_id": row.spec_id,
        "title": row.title,
        "product_key": row.product_key,
        "native_acl": split_acl(row.native_acl),
        "version": row.version,
        "content_hash": row.content_hash,
        "updated_at": row.updated_at,
    }


class Store:
    def __init__(self, db_path: str | Path, files_dir: str | Path, clock: Clock = utc_now) -> None:
        self.db = Database(db_path)
        self.files_dir = Path(files_dir)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        Base.metadata.create_all(self.db.engine)

    def now(self) -> str:
        return format_ts(self.clock())

    def counts(self) -> dict[str, int]:
        with self.db.session() as db:
            total = db.scalar(select(func.count()).select_from(Spec)) or 0
        return {"spec": int(total)}

    def is_empty(self) -> bool:
        return self.counts()["spec"] == 0

    def get(self, spec_id: str) -> dict[str, object]:
        with self.db.session() as db:
            return record(self._row(db, spec_id))

    def list_specs(self) -> list[dict[str, object]]:
        with self.db.session() as db:
            rows = db.scalars(select(Spec).order_by(Spec.spec_id)).all()
            return [record(row) for row in rows]

    def content(self, spec_id: str) -> tuple[bytes, str]:
        with self.db.session() as db:
            file_name = self._row(db, spec_id).file_name
        path = self.files_dir / file_name
        if not path.is_file():
            raise NotFound(spec_id)
        return path.read_bytes(), "text/markdown; charset=utf-8"

    def ids(self) -> dict[str, list[dict[str, object]]]:
        with self.db.session() as db:
            rows = db.execute(select(Spec.spec_id, Spec.version).order_by(Spec.spec_id)).all()
            return {"spec": [{"id": row.spec_id, "version": row.version} for row in rows]}

    def create(
        self,
        spec_id: str,
        title: str,
        product_key: str,
        body: str,
        native_acl: list[str],
        at: str | None = None,
    ) -> dict[str, object]:
        if not _SPEC_ID.match(spec_id):
            raise Invalid("spec_id must look like spec-name")
        self._check(title, product_key, body)
        acl = check_acl(list(native_acl), NATIVE_ACLS)
        stamp = at or self.now()
        file_name = f"{spec_id}.md"
        with self.db.session() as db:
            if db.get(Spec, spec_id) is not None:
                raise Conflict(spec_id)
            self._write(file_name, body)
            saved = Spec(
                spec_id=spec_id,
                title=title.strip(),
                product_key=product_key,
                file_name=file_name,
                native_acl=acl,
                version=1,
                content_hash=_hash(body),
                updated_at=stamp,
            )
            db.add(saved)
            return record(saved)

    def edit(
        self, spec_id: str, body: str | None = None, title: str | None = None
    ) -> dict[str, object]:
        with self.db.session() as db:
            row = self._row(db, spec_id)
            new_title = row.title if title is None else title
            new_body = self._read(row.file_name) if body is None else body
            self._check(new_title, row.product_key, new_body)
            new_title = new_title.strip()
            if new_title == row.title and new_body == self._read(row.file_name):
                return record(row)
            self._write(row.file_name, new_body)
            row.title = new_title
            row.version += 1
            row.content_hash = _hash(new_body)
            row.updated_at = self.now()
            return record(row)

    def set_acl(self, spec_id: str, native_acl: list[str]) -> dict[str, object]:
        acl = check_acl(list(native_acl), NATIVE_ACLS)
        with self.db.session() as db:
            row = self._row(db, spec_id)
            if row.native_acl == acl:
                return record(row)
            row.native_acl = acl
            row.version += 1
            row.updated_at = self.now()
            return record(row)

    def delete(self, spec_id: str) -> None:
        with self.db.session() as db:
            row = self._row(db, spec_id)
            file_name = row.file_name
            db.delete(row)
        path = self.files_dir / file_name
        if path.is_file():
            path.unlink()

    def reset(self) -> None:
        with self.db.session() as db:
            names = list(db.scalars(select(Spec.file_name)))
            db.execute(delete(Spec))
        for name in names:
            path = self.files_dir / name
            if path.is_file():
                path.unlink()

    def _read(self, file_name: str) -> str:
        return (self.files_dir / file_name).read_text(encoding="utf-8")

    def _write(self, file_name: str, body: str) -> None:
        (self.files_dir / file_name).write_text(body, encoding="utf-8")

    @staticmethod
    def _row(db: Session, spec_id: str) -> Spec:
        row = db.get(Spec, spec_id)
        if row is None:
            raise NotFound(spec_id)
        return row

    @staticmethod
    def _check(title: str, product_key: str, body: str) -> None:
        if not str(title).strip():
            raise Invalid("title is required")
        if product_key not in PRODUCT_KEYS:
            raise Invalid(f"unknown product_key: {product_key}")
        if not str(body).strip():
            raise Invalid("body is required")
