"""SQLite store and file storage for the HR harness.

The source holds more than the gateway may ingest: people rows carry `salary` and
`national_id`. No public method returns them. `PUBLIC_PERSON_FIELDS` is the allowlist.
Policy bodies live on disk, one file per policy, and the row keeps the file name and hash.
"""

import hashlib
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from hr_pdf import render_pdf

Clock = Callable[[], datetime]

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
PUBLIC_PERSON_FIELDS = (
    "person_id",
    "email",
    "full_name",
    "team",
    "role",
    "region",
    "manager_id",
    "native_acl",
    "version",
    "updated_at",
)
PUBLIC_POLICY_FIELDS = (
    "policy_id",
    "title",
    "category",
    "region",
    "format",
    "native_acl",
    "version",
    "content_hash",
    "updated_at",
)
NATIVE_ACLS = frozenset({"hr_employee", "hr_policy_admin"})
FORMATS = frozenset({"markdown", "pdf"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    person_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    team TEXT NOT NULL,
    role TEXT NOT NULL,
    region TEXT NOT NULL,
    manager_id TEXT,
    native_acl TEXT NOT NULL,
    salary INTEGER,
    national_id TEXT,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS policies (
    policy_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    region TEXT NOT NULL,
    format TEXT NOT NULL,
    file_name TEXT NOT NULL,
    native_acl TEXT NOT NULL,
    version INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS people_updated ON people (updated_at, person_id);
CREATE INDEX IF NOT EXISTS policies_updated ON policies (updated_at, policy_id);
"""


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class Invalid(Exception):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def format_ts(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(TS_FORMAT)


def parse_ts(value: str) -> str:
    """Normalize an ISO 8601 timestamp to the stored format. Naive input is UTC."""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as error:
        raise Invalid(f"not an ISO 8601 timestamp: {value!r}") from error
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return format_ts(moment)


def split_acl(value: str) -> list[str]:
    return [item for item in value.split(",") if item]


def check_acl(native_acl: list[str]) -> str:
    if not native_acl:
        raise Invalid("native_acl needs at least one entry")
    unknown = set(native_acl) - NATIVE_ACLS
    if unknown:
        raise Invalid(f"unknown native acl: {sorted(unknown)}")
    return ",".join(sorted(set(native_acl)))


def render_body(fmt: str, body: str) -> bytes:
    return render_pdf(body) if fmt == "pdf" else body.encode("utf-8")


class Store:
    def __init__(self, db_path: str | Path, files_dir: str | Path, clock: Clock = utc_now):
        self.db_path = Path(db_path)
        self.files_dir = Path(files_dir)
        self.clock = clock
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def now(self) -> str:
        return format_ts(self.clock())

    # Reads

    def counts(self) -> dict[str, int]:
        with self.connect() as db:
            return {
                "person": db.execute("SELECT COUNT(*) FROM people").fetchone()[0],
                "policy": db.execute("SELECT COUNT(*) FROM policies").fetchone()[0],
            }

    def is_empty(self) -> bool:
        return not any(self.counts().values())

    @staticmethod
    def person_record(row: sqlite3.Row) -> dict[str, object]:
        record = {name: row[name] for name in PUBLIC_PERSON_FIELDS}
        record["native_acl"] = split_acl(row["native_acl"])
        return record

    @staticmethod
    def policy_record(row: sqlite3.Row) -> dict[str, object]:
        record = {name: row[name] for name in PUBLIC_POLICY_FIELDS}
        record["native_acl"] = split_acl(row["native_acl"])
        return record

    def list_people(self) -> list[dict[str, object]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM people ORDER BY person_id").fetchall()
        return [self.person_record(row) for row in rows]

    def list_policies(self) -> list[dict[str, object]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM policies ORDER BY policy_id").fetchall()
        return [self.policy_record(row) for row in rows]

    def get_person(self, person_id: str) -> dict[str, object]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM people WHERE person_id = ?", (person_id,)).fetchone()
        if row is None:
            raise NotFound(person_id)
        return self.person_record(row)

    def get_policy(self, policy_id: str) -> dict[str, object]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM policies WHERE policy_id = ?", (policy_id,)).fetchone()
        if row is None:
            raise NotFound(policy_id)
        return self.policy_record(row)

    def policy_content(self, policy_id: str) -> tuple[bytes, str]:
        with self.connect() as db:
            row = db.execute(
                "SELECT file_name, format FROM policies WHERE policy_id = ?", (policy_id,)
            ).fetchone()
        if row is None:
            raise NotFound(policy_id)
        media = "application/pdf" if row["format"] == "pdf" else "text/markdown; charset=utf-8"
        return (self.files_dir / row["file_name"]).read_bytes(), media

    def changes(
        self, updated_since: str | None, page_token: str | None, limit: int
    ) -> tuple[list[dict[str, object]], str | None]:
        """Return objects ordered by (updated_at, id). Inclusive of `updated_since`.

        The token is the last (updated_at, id) of the previous page, so a page boundary
        inside one timestamp loses nothing. Deletes are not listed; see `ids`.
        """
        since = parse_ts(updated_since) if updated_since else ""
        after: tuple[str, str] | None = None
        if page_token:
            stamp, _, object_id = page_token.partition("|")
            after = (parse_ts(stamp), object_id)
        items: list[dict[str, object]] = []
        with self.connect() as db:
            for table, key, kind, build in (
                ("people", "person_id", "person", self.person_record),
                ("policies", "policy_id", "policy", self.policy_record),
            ):
                if after:
                    rows = db.execute(
                        f"SELECT * FROM {table} WHERE updated_at > ? "
                        f"OR (updated_at = ? AND {key} > ?) "
                        f"ORDER BY updated_at, {key} LIMIT ?",
                        (after[0], after[0], after[1], limit + 1),
                    ).fetchall()
                else:
                    rows = db.execute(
                        f"SELECT * FROM {table} WHERE updated_at >= ? "
                        f"ORDER BY updated_at, {key} LIMIT ?",
                        (since, limit + 1),
                    ).fetchall()
                for row in rows:
                    record = build(row)
                    items.append(
                        {
                            "object_type": kind,
                            "id": row[key],
                            "version": row["version"],
                            "updated_at": row["updated_at"],
                            "record": record,
                        }
                    )
        items.sort(key=lambda item: (item["updated_at"], item["id"]))
        more = len(items) > limit
        items = items[:limit]
        token = f"{items[-1]['updated_at']}|{items[-1]['id']}" if more else None
        return items, token

    def ids(self) -> dict[str, list[dict[str, object]]]:
        """Every live id with its version. The snapshot_diff backstop reads this."""
        with self.connect() as db:
            people = db.execute(
                "SELECT person_id AS id, version FROM people ORDER BY person_id"
            ).fetchall()
            policies = db.execute(
                "SELECT policy_id AS id, version FROM policies ORDER BY policy_id"
            ).fetchall()
        return {
            "person": [dict(row) for row in people],
            "policy": [dict(row) for row in policies],
        }

    # Writes. These stand in for HR staff editing the source system.

    def put_person(self, person: dict[str, object], at: str | None = None) -> dict[str, object]:
        stamp = at or self.now()
        native = check_acl(list(person.get("native_acl", ["hr_employee"])))  # type: ignore[arg-type]
        with self.connect() as db:
            row = db.execute(
                "SELECT version FROM people WHERE person_id = ?", (person["person_id"],)
            ).fetchone()
            version = 1 if row is None else row["version"] + 1
            try:
                db.execute(
                    "INSERT INTO people (person_id, email, full_name, team, role, "
                    "region, manager_id, native_acl, salary, national_id, version, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(person_id) DO UPDATE SET email = excluded.email, "
                    "full_name = excluded.full_name, team = excluded.team, role = excluded.role, "
                    "region = excluded.region, manager_id = excluded.manager_id, "
                    "native_acl = excluded.native_acl, salary = excluded.salary, "
                    "national_id = excluded.national_id, version = excluded.version, "
                    "updated_at = excluded.updated_at",
                    (
                        person["person_id"],
                        person["email"],
                        person["full_name"],
                        person["team"],
                        person["role"],
                        person["region"],
                        person.get("manager_id"),
                        native,
                        person.get("salary"),
                        person.get("national_id"),
                        version,
                        stamp,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise Conflict(str(error)) from error
        return self.get_person(str(person["person_id"]))

    def update_person(self, person_id: str, changes: dict[str, object]) -> dict[str, object]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM people WHERE person_id = ?", (person_id,)).fetchone()
        if row is None:
            raise NotFound(person_id)
        merged = dict(row)
        merged.update({key: value for key, value in changes.items() if value is not None})
        if "native_acl" in changes and changes["native_acl"] is not None:
            merged["native_acl"] = changes["native_acl"]
        else:
            merged["native_acl"] = split_acl(row["native_acl"])
        return self.put_person(merged)

    def delete_person(self, person_id: str) -> None:
        with self.connect() as db:
            if db.execute("DELETE FROM people WHERE person_id = ?", (person_id,)).rowcount == 0:
                raise NotFound(person_id)

    def create_policy(
        self,
        policy_id: str,
        title: str,
        category: str,
        region: str,
        fmt: str,
        native_acl: list[str],
        body: str,
        at: str | None = None,
    ) -> dict[str, object]:
        if fmt not in FORMATS:
            raise Invalid(f"format must be one of {sorted(FORMATS)}")
        acl = check_acl(native_acl)
        with self.connect() as db:
            if db.execute("SELECT 1 FROM policies WHERE policy_id = ?", (policy_id,)).fetchone():
                raise Conflict(policy_id)
        file_name = f"{policy_id}.{'pdf' if fmt == 'pdf' else 'md'}"
        payload = render_body(fmt, body)
        (self.files_dir / file_name).write_bytes(payload)
        with self.connect() as db:
            db.execute(
                "INSERT INTO policies (policy_id, title, category, region, format, file_name, "
                "native_acl, version, content_hash, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    policy_id,
                    title,
                    category,
                    region,
                    fmt,
                    file_name,
                    acl,
                    hashlib.sha256(payload).hexdigest(),
                    at or self.now(),
                ),
            )
        return self.get_policy(policy_id)

    def edit_policy(
        self, policy_id: str, body: str | None = None, title: str | None = None
    ) -> dict[str, object]:
        """Replace the body or title. The version rises only when the stored bytes or title differ."""
        with self.connect() as db:
            row = db.execute("SELECT * FROM policies WHERE policy_id = ?", (policy_id,)).fetchone()
        if row is None:
            raise NotFound(policy_id)
        new_title = title if title is not None else row["title"]
        digest = row["content_hash"]
        if body is not None:
            payload = render_body(row["format"], body)
            digest = hashlib.sha256(payload).hexdigest()
            if digest != row["content_hash"]:
                (self.files_dir / row["file_name"]).write_bytes(payload)
        if digest == row["content_hash"] and new_title == row["title"]:
            return self.policy_record(row)
        with self.connect() as db:
            db.execute(
                "UPDATE policies SET title = ?, content_hash = ?, version = version + 1, "
                "updated_at = ? WHERE policy_id = ?",
                (new_title, digest, self.now(), policy_id),
            )
        return self.get_policy(policy_id)

    def set_policy_acl(self, policy_id: str, native_acl: list[str]) -> dict[str, object]:
        """Change who may read a policy. Content and version stay; updated_at moves."""
        acl = check_acl(native_acl)
        with self.connect() as db:
            changed = db.execute(
                "UPDATE policies SET native_acl = ?, updated_at = ? WHERE policy_id = ?",
                (acl, self.now(), policy_id),
            ).rowcount
        if changed == 0:
            raise NotFound(policy_id)
        return self.get_policy(policy_id)

    def delete_policy(self, policy_id: str) -> None:
        with self.connect() as db:
            row = db.execute(
                "SELECT file_name FROM policies WHERE policy_id = ?", (policy_id,)
            ).fetchone()
            if row is None:
                raise NotFound(policy_id)
            db.execute("DELETE FROM policies WHERE policy_id = ?", (policy_id,))
        (self.files_dir / row["file_name"]).unlink(missing_ok=True)

    def reset(self) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM people")
            db.execute("DELETE FROM policies")
        for path in self.files_dir.iterdir():
            if path.is_file():
                path.unlink()
