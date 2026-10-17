"""SQLAlchemy models for the knowledge tables.

Migrations in database/postgres are the DDL. These classes mirror that schema.
Engines set search_path=kbs, so the models are unqualified. Connector sync
state has a second mapping in connector_core.state so a connector image does
not install this package. A test checks that the column names still match.
"""

from datetime import datetime
from typing import ClassVar

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Computed, DateTime, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIMENSIONS = 1024


class Base(DeclarativeBase):
    pass


class Chunk(Base):
    """Authoritative prose, vector, full-text, and ACL columns. Search filters to one embedding_model."""

    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    object_id: Mapped[str] = mapped_column(Text, nullable=False)
    object_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    chunk_index: Mapped[int] = mapped_column(nullable=False, server_default="0")
    version: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    section_path: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    content: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    chunker_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    acl_groups: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    acl_departments: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    min_clearance: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    acl_rules: Mapped[list] = mapped_column(JSONB, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    fts: Mapped[str | None] = mapped_column(
        TSVECTOR(),
        Computed(
            "to_tsvector('english', coalesce(section_path, '') || ' ' || coalesce(content, ''))",
            persisted=True,
        ),
    )


class Tombstone(Base):
    """Hard-delete marker. A matching create is ignored so a replay cannot resurrect the object."""

    __tablename__ = "tombstones"

    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    object_id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    content_hash: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditLog(Base):
    """gateway_ro may insert a row and may not select it, so no RETURNING."""

    __tablename__ = "audit_log"
    __table_args__: ClassVar[dict[str, bool]] = {"implicit_returning": False}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    principal: Mapped[str | None] = mapped_column(Text)
    chunk_id: Mapped[int | None] = mapped_column(BigInteger)
    attributes: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    request_id: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[str | None] = mapped_column(Text)


class SyncState(Base):
    """Connector watermark. The same table is mapped again in connector_core.state."""

    __tablename__ = "sync_state"

    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    watermark: Mapped[str | None] = mapped_column(Text)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceObject(Base):
    """Objects the connector has snapshotted. Index reconciliation compares chunks to this."""

    __tablename__ = "source_objects"

    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    object_id: Mapped[str] = mapped_column(Text, primary_key=True)
    object_type: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    native_acl: Mapped[str] = mapped_column(Text, nullable=False)
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    s3_version_id: Mapped[str | None] = mapped_column(Text)
    source_updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
