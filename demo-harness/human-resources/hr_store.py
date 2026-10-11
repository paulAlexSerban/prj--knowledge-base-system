"""SQLite store and file storage for the HR harness.

SQLAlchemy owns every query. The source holds more than the gateway may ingest:
people rows carry `salary` and `national_id`. No public method returns them.
`PUBLIC_PERSON_FIELDS` is the allowlist. Policy bodies live on disk, one file per
policy, and the row keeps the file name and hash.
"""

import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from hr_pdf import render_pdf
from sqlalchemy import Index, and_, create_engine, delete, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

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
KINDS = frozenset({"person", "policy"})
BATCH = 1000


class Base(DeclarativeBase):
    pass


class Person(Base):
    __tablename__ = "people"
    __table_args__ = (Index("people_updated", "updated_at", "person_id"),)

    person_id: Mapped[str] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(unique=True)
    full_name: Mapped[str]
    team: Mapped[str]
    role: Mapped[str]
    region: Mapped[str]
    manager_id: Mapped[str | None]
    native_acl: Mapped[str]
    salary: Mapped[int | None]
    national_id: Mapped[str | None]
    version: Mapped[int]
    updated_at: Mapped[str]


class Policy(Base):
    __tablename__ = "policies"
    __table_args__ = (Index("policies_updated", "updated_at", "policy_id"),)

    policy_id: Mapped[str] = mapped_column(primary_key=True)
    title: Mapped[str]
    category: Mapped[str]
    region: Mapped[str]
    format: Mapped[str]
    file_name: Mapped[str]
    native_acl: Mapped[str]
    version: Mapped[int]
    content_hash: Mapped[str]
    updated_at: Mapped[str]


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


def _max_suffix(ids: list[str], prefix: str) -> int:
    highest = 0
    for object_id in ids:
        suffix = object_id.removeprefix(prefix)
        if object_id.startswith(prefix) and suffix.isdigit():
            highest = max(highest, int(suffix))
    return highest


class Store:
    def __init__(self, db_path: str | Path, files_dir: str | Path, clock: Clock = utc_now):
        self.db_path = Path(db_path)
        self.files_dir = Path(files_dir)
        self.clock = clock
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(
            f"sqlite:///{self.db_path}", connect_args={"timeout": 10}
        )
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine) as db, db.begin():
            yield db

    def now(self) -> str:
        return format_ts(self.clock())

    def counts(self) -> dict[str, int]:
        with self.session() as db:
            people = db.scalar(select(func.count()).select_from(Person)) or 0
            policies = db.scalar(select(func.count()).select_from(Policy)) or 0
        return {"person": people, "policy": policies}

    def is_empty(self) -> bool:
        return not any(self.counts().values())

    @staticmethod
    def person_record(row: Person) -> dict[str, object]:
        record = {name: getattr(row, name) for name in PUBLIC_PERSON_FIELDS}
        record["native_acl"] = split_acl(row.native_acl)
        return record

    @staticmethod
    def policy_record(row: Policy) -> dict[str, object]:
        record = {name: getattr(row, name) for name in PUBLIC_POLICY_FIELDS}
        record["native_acl"] = split_acl(row.native_acl)
        return record

    def list_people(self) -> list[dict[str, object]]:
        with self.session() as db:
            rows = db.scalars(select(Person).order_by(Person.person_id)).all()
            return [self.person_record(row) for row in rows]

    def list_policies(self) -> list[dict[str, object]]:
        with self.session() as db:
            rows = db.scalars(select(Policy).order_by(Policy.policy_id)).all()
            return [self.policy_record(row) for row in rows]

    def get_person(self, person_id: str) -> dict[str, object]:
        with self.session() as db:
            row = db.get(Person, person_id)
            if row is None:
                raise NotFound(person_id)
            return self.person_record(row)

    def get_policy(self, policy_id: str) -> dict[str, object]:
        with self.session() as db:
            row = db.get(Policy, policy_id)
            if row is None:
                raise NotFound(policy_id)
            return self.policy_record(row)

    def policy_content(self, policy_id: str) -> tuple[bytes, str]:
        with self.session() as db:
            row = db.get(Policy, policy_id)
            if row is None:
                raise NotFound(policy_id)
            file_name, fmt = row.file_name, row.format
        media = "application/pdf" if fmt == "pdf" else "text/markdown; charset=utf-8"
        return (self.files_dir / file_name).read_bytes(), media

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
        with self.session() as db:
            for model, key, kind, build in (
                (Person, Person.person_id, "person", self.person_record),
                (Policy, Policy.policy_id, "policy", self.policy_record),
            ):
                if after:
                    window = or_(
                        model.updated_at > after[0],
                        and_(model.updated_at == after[0], key > after[1]),
                    )
                else:
                    window = model.updated_at >= since
                rows = db.scalars(
                    select(model).where(window).order_by(model.updated_at, key).limit(limit + 1)
                ).all()
                for row in rows:
                    record = build(row)
                    items.append(
                        {
                            "object_type": kind,
                            "id": record[key.key],
                            "version": row.version,
                            "updated_at": row.updated_at,
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
        with self.session() as db:
            people = db.execute(
                select(Person.person_id, Person.version).order_by(Person.person_id)
            ).all()
            policies = db.execute(
                select(Policy.policy_id, Policy.version).order_by(Policy.policy_id)
            ).all()
        return {
            "person": [{"id": row.person_id, "version": row.version} for row in people],
            "policy": [{"id": row.policy_id, "version": row.version} for row in policies],
        }

    def next_number(self, kind: str) -> int:
        """One past the highest numeric suffix, so a second bulk load does not collide."""
        if kind not in KINDS:
            raise Invalid(f"kind must be one of {sorted(KINDS)}")
        prefix = "emp-" if kind == "person" else "pol-gen-"
        with self.session() as db:
            column = Person.person_id if kind == "person" else Policy.policy_id
            existing = list(db.scalars(select(column)))
        return _max_suffix(existing, prefix) + 1

    # Writes. These stand in for HR staff editing the source system.

    def put_person(self, person: dict[str, object], at: str | None = None) -> dict[str, object]:
        stamp = at or self.now()
        native = check_acl(list(person.get("native_acl", ["hr_employee"])))  # type: ignore[arg-type]
        with self.session() as db:
            current = db.get(Person, person["person_id"])
            version = 1 if current is None else current.version + 1
            values = {
                "person_id": person["person_id"],
                "email": person["email"],
                "full_name": person["full_name"],
                "team": person["team"],
                "role": person["role"],
                "region": person["region"],
                "manager_id": person.get("manager_id"),
                "native_acl": native,
                "salary": person.get("salary"),
                "national_id": person.get("national_id"),
                "version": version,
                "updated_at": stamp,
            }
            stmt = sqlite_insert(Person).values(**values)
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=[Person.person_id],
                set_={
                    "email": excluded.email,
                    "full_name": excluded.full_name,
                    "team": excluded.team,
                    "role": excluded.role,
                    "region": excluded.region,
                    "manager_id": excluded.manager_id,
                    "native_acl": excluded.native_acl,
                    "salary": excluded.salary,
                    "national_id": excluded.national_id,
                    "version": excluded.version,
                    "updated_at": excluded.updated_at,
                },
            )
            try:
                db.execute(stmt)
            except IntegrityError as error:
                raise Conflict(str(error)) from error
        return self.get_person(str(person["person_id"]))

    def update_person(self, person_id: str, changes: dict[str, object]) -> dict[str, object]:
        with self.session() as db:
            row = db.get(Person, person_id)
            if row is None:
                raise NotFound(person_id)
            merged: dict[str, object] = {
                "person_id": row.person_id,
                "email": row.email,
                "full_name": row.full_name,
                "team": row.team,
                "role": row.role,
                "region": row.region,
                "manager_id": row.manager_id,
                "native_acl": split_acl(row.native_acl),
                "salary": row.salary,
                "national_id": row.national_id,
            }
        merged.update({key: value for key, value in changes.items() if value is not None})
        return self.put_person(merged)

    def delete_person(self, person_id: str) -> None:
        with self.session() as db:
            removed = db.execute(delete(Person).where(Person.person_id == person_id))
            if removed.rowcount == 0:
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
        with self.session() as db:
            if db.get(Policy, policy_id) is not None:
                raise Conflict(policy_id)
        self.bulk_insert(
            "policy",
            [
                {
                    "policy_id": policy_id,
                    "title": title,
                    "category": category,
                    "region": region,
                    "format": fmt,
                    "native_acl": native_acl,
                    "body": body,
                }
            ],
            at=at,
        )
        return self.get_policy(policy_id)

    def edit_policy(
        self, policy_id: str, body: str | None = None, title: str | None = None
    ) -> dict[str, object]:
        """Replace the body or title. The version rises only when the stored bytes or title differ."""
        with self.session() as db:
            row = db.get(Policy, policy_id)
            if row is None:
                raise NotFound(policy_id)
            current_title = row.title
            fmt = row.format
            file_name = row.file_name
        new_title = title if title is not None else current_title
        with self.session() as db:
            stored = db.get(Policy, policy_id)
            assert stored is not None
            digest = stored.content_hash
            if body is not None:
                payload = render_body(fmt, body)
                digest = hashlib.sha256(payload).hexdigest()
                if digest != stored.content_hash:
                    (self.files_dir / file_name).write_bytes(payload)
            if digest == stored.content_hash and new_title == stored.title:
                return self.policy_record(stored)
            db.execute(
                update(Policy)
                .where(Policy.policy_id == policy_id)
                .values(
                    title=new_title,
                    content_hash=digest,
                    version=stored.version + 1,
                    updated_at=self.now(),
                )
            )
        return self.get_policy(policy_id)

    def set_policy_acl(self, policy_id: str, native_acl: list[str]) -> dict[str, object]:
        """Change who may read a policy. Content and version stay; updated_at moves."""
        acl = check_acl(native_acl)
        with self.session() as db:
            changed = db.execute(
                update(Policy)
                .where(Policy.policy_id == policy_id)
                .values(native_acl=acl, updated_at=self.now())
            )
            if changed.rowcount == 0:
                raise NotFound(policy_id)
        return self.get_policy(policy_id)

    def delete_policy(self, policy_id: str) -> None:
        with self.session() as db:
            row = db.get(Policy, policy_id)
            if row is None:
                raise NotFound(policy_id)
            file_name = row.file_name
            db.execute(delete(Policy).where(Policy.policy_id == policy_id))
        (self.files_dir / file_name).unlink(missing_ok=True)

    def bulk_insert(self, kind: str, rows: list[dict[str, object]], at: str | None = None) -> int:
        """Insert new rows in batches of 1,000. One transaction per batch. Ids must be new."""
        if kind not in KINDS:
            raise Invalid(f"kind must be one of {sorted(KINDS)}")
        if not rows:
            return 0
        stamp = at or self.now()
        if kind == "person":
            prepared = [self._person_values(row, stamp) for row in rows]
            self._insert_batches(Person, prepared)
            return len(prepared)
        written: list[Path] = []
        prepared = []
        try:
            for row in rows:
                values, path, payload = self._policy_values(row, stamp)
                path.write_bytes(payload)
                written.append(path)
                prepared.append(values)
            self._insert_batches(Policy, prepared)
        except Exception:
            for path in written:
                path.unlink(missing_ok=True)
            raise
        return len(prepared)

    def _person_values(self, person: dict[str, object], stamp: str) -> dict[str, object]:
        native = check_acl(list(person.get("native_acl", ["hr_employee"])))  # type: ignore[arg-type]
        return {
            "person_id": person["person_id"],
            "email": person["email"],
            "full_name": person["full_name"],
            "team": person["team"],
            "role": person["role"],
            "region": person["region"],
            "manager_id": person.get("manager_id"),
            "native_acl": native,
            "salary": person.get("salary"),
            "national_id": person.get("national_id"),
            "version": 1,
            "updated_at": stamp,
        }

    def _policy_values(
        self, policy: dict[str, object], stamp: str
    ) -> tuple[dict[str, object], Path, bytes]:
        fmt = str(policy["format"])
        if fmt not in FORMATS:
            raise Invalid(f"format must be one of {sorted(FORMATS)}")
        payload = render_body(fmt, str(policy["body"]))
        file_name = f"{policy['policy_id']}.{'pdf' if fmt == 'pdf' else 'md'}"
        values = {
            "policy_id": policy["policy_id"],
            "title": policy["title"],
            "category": policy["category"],
            "region": policy["region"],
            "format": fmt,
            "file_name": file_name,
            "native_acl": check_acl(list(policy["native_acl"])),  # type: ignore[arg-type]
            "version": 1,
            "content_hash": hashlib.sha256(payload).hexdigest(),
            "updated_at": stamp,
        }
        return values, self.files_dir / file_name, payload

    def _insert_batches(
        self, model: type[Person] | type[Policy], rows: list[dict[str, object]]
    ) -> None:
        for start in range(0, len(rows), BATCH):
            chunk = rows[start : start + BATCH]
            with self.session() as db:
                try:
                    db.execute(sqlite_insert(model), chunk)
                except IntegrityError as error:
                    raise Conflict(str(error)) from error

    def clear_kind(self, kind: str) -> None:
        if kind == "person":
            with self.session() as db:
                db.execute(delete(Person))
            return
        if kind == "policy":
            with self.session() as db:
                names = list(db.scalars(select(Policy.file_name)))
                db.execute(delete(Policy))
            for name in names:
                (self.files_dir / name).unlink(missing_ok=True)
            return
        raise Invalid(f"kind must be one of {sorted(KINDS)}")

    def reset(self) -> None:
        self.clear_kind("person")
        self.clear_kind("policy")
