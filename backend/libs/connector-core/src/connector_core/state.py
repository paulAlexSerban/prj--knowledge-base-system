"""Sync watermark and the objects the connector has snapshotted.

SQLAlchemy ORM, no schema qualifier. Postgres sets search_path=kbs so the
names match the migration. Tests create the same tables on SQLite. The chunk
store maps these two tables again in knowledge_store.models. Connectors do
not import that package. A test checks the column names still match.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class SyncState(Base):
    __tablename__ = "sync_state"

    source_id: Mapped[str] = mapped_column(String, primary_key=True)
    watermark: Mapped[str | None] = mapped_column(String)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceObject(Base):
    __tablename__ = "source_objects"

    source_id: Mapped[str] = mapped_column(String, primary_key=True)
    object_id: Mapped[str] = mapped_column(String, primary_key=True)
    object_type: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    native_acl: Mapped[str] = mapped_column(String, nullable=False)
    s3_key: Mapped[str] = mapped_column(String, nullable=False)
    s3_version_id: Mapped[str | None] = mapped_column(String)
    source_updated_at: Mapped[str] = mapped_column(String, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True)
class ObjectRow:
    object_id: str
    object_type: str
    version: str
    content_hash: str
    native_acl: str
    s3_key: str


def _now() -> datetime:
    """UTC stamp for synced_at and watermark rows."""
    return datetime.now(UTC)


class State:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_tables(self) -> None:
        """Test helper. Production applies database/postgres/migrations."""
        Base.metadata.create_all(self.engine)

    def ping(self) -> bool:
        """True when sync_state is queryable. Migrations may still be applying."""
        try:
            with self.engine.connect() as connection:
                connection.execute(select(SyncState.source_id).limit(1))
        except SQLAlchemyError:
            return False
        return True

    def _insert(self):
        """Postgres upsert in production, SQLite upsert in tests."""
        if self.engine.dialect.name == "postgresql":
            return pg_insert
        return sqlite_insert

    def get_watermark(self, source_id: str) -> str | None:
        """Cursor or timestamp of the last fully successful cycle. Failures do not advance it."""
        statement = select(SyncState.watermark).where(SyncState.source_id == source_id)
        with Session(self.engine) as session:
            return session.scalar(statement)

    def mark_success(self, source_id: str, watermark: str) -> None:
        """Advance the watermark only after every object in the cycle succeeded."""
        now = _now()
        stmt = self._insert()(SyncState).values(
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
        with Session(self.engine) as session, session.begin():
            session.execute(stmt)

    def mark_error(self, source_id: str, error: str) -> None:
        """Record the failure without moving the watermark, so the next cycle retries."""
        now = _now()
        current = self.get_watermark(source_id)
        stmt = self._insert()(SyncState).values(
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
        with Session(self.engine) as session, session.begin():
            session.execute(stmt)

    def get_object(self, source_id: str, object_id: str) -> ObjectRow | None:
        """Last snapshotted version, hash, ACL, and S3 key for one object."""
        statement = select(SourceObject).where(
            SourceObject.source_id == source_id, SourceObject.object_id == object_id
        )
        with Session(self.engine) as session:
            row = session.scalar(statement)
        if row is None:
            return None
        return _row(row)

    def list_objects(self, source_id: str) -> dict[str, ObjectRow]:
        """All snapshotted objects for the delete sweep."""
        statement = select(SourceObject).where(SourceObject.source_id == source_id)
        with Session(self.engine) as session:
            return {row.object_id: _row(row) for row in session.scalars(statement)}

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
        """Remember what was snapshotted. Called only after the change event is accepted."""
        now = _now()
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
        stmt = self._insert()(SourceObject).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_id", "object_id"],
            set_={key: values[key] for key in values if key not in {"source_id", "object_id"}},
        )
        with Session(self.engine) as session, session.begin():
            session.execute(stmt)

    def delete_object(self, source_id: str, object_id: str) -> None:
        """Drop the row after the delete event is accepted and S3 is gone."""
        statement = delete(SourceObject).where(
            SourceObject.source_id == source_id, SourceObject.object_id == object_id
        )
        with Session(self.engine) as session, session.begin():
            session.execute(statement)

    def count_objects(self, source_id: str) -> int:
        """Snapshotted object count for the drift gauge."""
        statement = (
            select(func.count())
            .select_from(SourceObject)
            .where(SourceObject.source_id == source_id)
        )
        with Session(self.engine) as session:
            return int(session.scalar(statement) or 0)


def _row(row: SourceObject) -> ObjectRow:
    """Detach ORM state into a frozen row the syncer can pass across threads."""
    return ObjectRow(
        object_id=row.object_id,
        object_type=row.object_type,
        version=row.version,
        content_hash=row.content_hash,
        native_acl=row.native_acl,
        s3_key=row.s3_key,
    )
