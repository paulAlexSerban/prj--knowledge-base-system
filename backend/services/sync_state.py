"""Sync watermark and the objects the connector has snapshotted.

SQLAlchemy Core, no schema qualifier. Postgres sets search_path=kbs so the
names match the migration. Tests create the same tables on SQLite.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, MetaData, String, Table, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

metadata = MetaData()

sync_state = Table(
    "sync_state",
    metadata,
    Column("source_id", String, primary_key=True),
    Column("watermark", String),
    Column("last_success_at", DateTime(timezone=True)),
    Column("last_error", String),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

source_objects = Table(
    "source_objects",
    metadata,
    Column("source_id", String, primary_key=True),
    Column("object_id", String, primary_key=True),
    Column("object_type", String, nullable=False),
    Column("version", String, nullable=False),
    Column("content_hash", String, nullable=False),
    Column("native_acl", String, nullable=False),
    Column("s3_key", String, nullable=False),
    Column("s3_version_id", String),
    Column("source_updated_at", String, nullable=False),
    Column("synced_at", DateTime(timezone=True), nullable=False),
)


@dataclass(frozen=True)
class ObjectRow:
    object_id: str
    object_type: str
    version: str
    content_hash: str
    native_acl: str
    s3_key: str


def _now() -> datetime:
    return datetime.now(UTC)


class State:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_tables(self) -> None:
        """Test helper. Production applies database/postgres/migrations."""
        metadata.create_all(self.engine)

    def ping(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(select(sync_state.c.source_id).limit(1))
        except SQLAlchemyError:
            return False
        return True

    def _insert(self):
        if self.engine.dialect.name == "postgresql":
            return pg_insert
        return sqlite_insert

    def get_watermark(self, source_id: str) -> str | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(sync_state.c.watermark).where(sync_state.c.source_id == source_id)
            ).first()
        if row is None:
            return None
        return row.watermark

    def mark_success(self, source_id: str, watermark: str) -> None:
        now = _now()
        insert = self._insert()
        stmt = insert(sync_state).values(
            source_id=source_id,
            watermark=watermark,
            last_success_at=now,
            last_error=None,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_id"],
            set_={
                "watermark": watermark,
                "last_success_at": now,
                "last_error": None,
                "updated_at": now,
            },
        )
        with self.engine.begin() as connection:
            connection.execute(stmt)

    def mark_error(self, source_id: str, error: str) -> None:
        now = _now()
        current = self.get_watermark(source_id)
        insert = self._insert()
        stmt = insert(sync_state).values(
            source_id=source_id,
            watermark=current,
            last_success_at=None,
            last_error=error[:500],
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_id"],
            set_={"last_error": error[:500], "updated_at": now},
        )
        with self.engine.begin() as connection:
            connection.execute(stmt)

    def get_object(self, source_id: str, object_id: str) -> ObjectRow | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(source_objects).where(
                        source_objects.c.source_id == source_id,
                        source_objects.c.object_id == object_id,
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return _row(row)

    def list_objects(self, source_id: str) -> dict[str, ObjectRow]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(source_objects).where(source_objects.c.source_id == source_id)
            ).mappings()
            return {row["object_id"]: _row(row) for row in rows}

    def upsert_object(
        self,
        source_id: str,
        object_id: str,
        object_type: str,
        version: str,
        content_hash: str,
        native_acl: str,
        s3_key: str,
        s3_version_id: str | None,
        source_updated_at: str,
    ) -> None:
        now = _now()
        insert = self._insert()
        values = {
            "source_id": source_id,
            "object_id": object_id,
            "object_type": object_type,
            "version": version,
            "content_hash": content_hash,
            "native_acl": native_acl,
            "s3_key": s3_key,
            "s3_version_id": s3_version_id,
            "source_updated_at": source_updated_at,
            "synced_at": now,
        }
        stmt = insert(source_objects).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_id", "object_id"],
            set_={key: values[key] for key in values if key not in {"source_id", "object_id"}},
        )
        with self.engine.begin() as connection:
            connection.execute(stmt)

    def delete_object(self, source_id: str, object_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                delete(source_objects).where(
                    source_objects.c.source_id == source_id,
                    source_objects.c.object_id == object_id,
                )
            )

    def count_objects(self, source_id: str) -> int:
        return len(self.list_objects(source_id))


def _row(row: object) -> ObjectRow:
    return ObjectRow(
        object_id=row["object_id"],
        object_type=row["object_type"],
        version=row["version"],
        content_hash=row["content_hash"],
        native_acl=row["native_acl"],
        s3_key=row["s3_key"],
    )
