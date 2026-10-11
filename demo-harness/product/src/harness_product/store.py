"""Spec metadata in SQLite and the markdown body on disk.

Version moves when the title or the body changes. The only native ACL is
product_org, so an ACL call that repeats it does not move the version.
A lost content webhook is recovered because /ids shows the new version.
"""

import hashlib
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
NATIVE_ACLS = frozenset({"product_org"})
PRODUCT_KEYS = frozenset(
    {"checkout", "billing", "identity", "mobile", "analytics", "notifications"}
)
_SPEC_ID = re.compile(r"^spec-[a-z0-9-]{3,60}$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS specs (
    spec_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    product_key TEXT NOT NULL,
    file_name TEXT NOT NULL,
    native_acl TEXT NOT NULL,
    version INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def record(row: object) -> dict[str, object]:
    return {
        "spec_id": row["spec_id"],
        "title": row["title"],
        "product_key": row["product_key"],
        "native_acl": split_acl(row["native_acl"]),
        "version": row["version"],
        "content_hash": row["content_hash"],
        "updated_at": row["updated_at"],
    }


class Store:
    def __init__(self, db_path: str | Path, files_dir: str | Path, clock: Clock = utc_now) -> None:
        self.db = Database(db_path)
        self.files_dir = Path(files_dir)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        with self.db.session() as connection:
            connection.executescript(SCHEMA)

    def now(self) -> str:
        return format_ts(self.clock())

    def counts(self) -> dict[str, int]:
        with self.db.session() as connection:
            row = connection.execute("SELECT count(*) AS n FROM specs").fetchone()
        return {"spec": int(row["n"])}

    def is_empty(self) -> bool:
        return self.counts()["spec"] == 0

    def get(self, spec_id: str) -> dict[str, object]:
        with self.db.session() as connection:
            return record(self._row(connection, spec_id))

    def list_specs(self) -> list[dict[str, object]]:
        with self.db.session() as connection:
            rows = connection.execute("SELECT * FROM specs ORDER BY spec_id").fetchall()
        return [record(row) for row in rows]

    def content(self, spec_id: str) -> tuple[bytes, str]:
        with self.db.session() as connection:
            row = self._row(connection, spec_id)
            file_name = row["file_name"]
        path = self.files_dir / file_name
        if not path.is_file():
            raise NotFound(spec_id)
        return path.read_bytes(), "text/markdown; charset=utf-8"

    def ids(self) -> dict[str, list[dict[str, object]]]:
        with self.db.session() as connection:
            rows = connection.execute(
                "SELECT spec_id, version FROM specs ORDER BY spec_id"
            ).fetchall()
        return {"spec": [{"id": row["spec_id"], "version": row["version"]} for row in rows]}

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
        with self.db.session() as connection:
            found = connection.execute(
                "SELECT 1 FROM specs WHERE spec_id = ?", (spec_id,)
            ).fetchone()
            if found is not None:
                raise Conflict(spec_id)
            self._write(file_name, body)
            connection.execute(
                """
                INSERT INTO specs (
                    spec_id, title, product_key, file_name, native_acl,
                    version, content_hash, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (spec_id, title.strip(), product_key, file_name, acl, _hash(body), stamp),
            )
            saved = self._row(connection, spec_id)
        return record(saved)

    def edit(
        self, spec_id: str, body: str | None = None, title: str | None = None
    ) -> dict[str, object]:
        with self.db.session() as connection:
            row = self._row(connection, spec_id)
            new_title = row["title"] if title is None else title
            new_body = self._read(row["file_name"]) if body is None else body
            self._check(new_title, row["product_key"], new_body)
            new_title = new_title.strip()
            if new_title == row["title"] and new_body == self._read(row["file_name"]):
                return record(row)
            self._write(row["file_name"], new_body)
            connection.execute(
                """
                UPDATE specs
                SET title = ?, version = ?, content_hash = ?, updated_at = ?
                WHERE spec_id = ?
                """,
                (new_title, row["version"] + 1, _hash(new_body), self.now(), spec_id),
            )
            saved = self._row(connection, spec_id)
        return record(saved)

    def set_acl(self, spec_id: str, native_acl: list[str]) -> dict[str, object]:
        acl = check_acl(list(native_acl), NATIVE_ACLS)
        with self.db.session() as connection:
            row = self._row(connection, spec_id)
            if row["native_acl"] == acl:
                return record(row)
            connection.execute(
                """
                UPDATE specs SET native_acl = ?, version = ?, updated_at = ? WHERE spec_id = ?
                """,
                (acl, row["version"] + 1, self.now(), spec_id),
            )
            saved = self._row(connection, spec_id)
        return record(saved)

    def delete(self, spec_id: str) -> None:
        with self.db.session() as connection:
            row = self._row(connection, spec_id)
            connection.execute("DELETE FROM specs WHERE spec_id = ?", (spec_id,))
            path = self.files_dir / row["file_name"]
        if path.is_file():
            path.unlink()

    def reset(self) -> None:
        with self.db.session() as connection:
            rows = connection.execute("SELECT file_name FROM specs").fetchall()
            connection.execute("DELETE FROM specs")
        for row in rows:
            path = self.files_dir / row["file_name"]
            if path.is_file():
                path.unlink()

    def _read(self, file_name: str) -> str:
        return (self.files_dir / file_name).read_text(encoding="utf-8")

    def _write(self, file_name: str, body: str) -> None:
        (self.files_dir / file_name).write_text(body, encoding="utf-8")

    def _row(self, connection: object, spec_id: str):
        row = connection.execute("SELECT * FROM specs WHERE spec_id = ?", (spec_id,)).fetchone()
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
