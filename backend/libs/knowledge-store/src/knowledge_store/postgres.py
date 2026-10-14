"""Chunk rows, tombstones, and authorized search.

The writer connects as ingestion_rw. Search connects as gateway_ro, so a query
that forgets the predicate still cannot read another principal's rows.
Statements are SQLAlchemy ORM expressions. The principal is applied with
set_config for the transaction, which is what row-level security reads.
gateway_ro can insert an audit row and cannot select one, so that insert does
not use RETURNING.
"""

import os
from dataclasses import dataclass

from sqlalchemy import (
    Text,
    and_,
    any_,
    delete,
    false,
    func,
    insert,
    literal,
    or_,
    select,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from knowledge_store.acl import AclView, Principal, allows, view_from_rules
from knowledge_store.chunkers import ChunkDraft
from knowledge_store.models import AuditLog, Chunk, SourceObject, SyncState, Tombstone
from shared.telemetry import span

RRF_K = 60
# Cosine distance above which a vector-only hit is not an answer (semantic models only).
# e5-large on the seeded chunks: paraphrases about 0.11 to 0.18,
# and "xylophone quantum payroll" about 0.21. The floor sits between them.
DEFAULT_MAX_DISTANCE = float(os.environ.get("VECTOR_MAX_DISTANCE", "0.20"))
OVERFETCH = 3


class VerifyError(Exception):
    def __init__(self, store: str) -> None:
        super().__init__(store)
        self.store = store


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    source_id: str
    object_id: str
    object_type: str
    version: str
    section_path: str
    text: str
    score: float
    synced_at: str


class ChunkTable:
    def __init__(self, writer: Engine | None, reader: Engine) -> None:
        self.writer = writer
        self.reader = reader

    def ping(self) -> bool:
        engine = self.writer if self.writer is not None else self.reader
        try:
            with engine.connect() as connection:
                connection.execute(select(literal(1)))
        except Exception:  # noqa: BLE001 - the driver raises several connection errors
            return False
        return True

    def same_hash(self, source_id: str, object_id: str, content_hash: str, model: str) -> bool:
        statement = (
            select(func.count())
            .select_from(Chunk)
            .where(
                Chunk.source_id == source_id,
                Chunk.object_id == object_id,
                Chunk.content_hash == content_hash,
                Chunk.embedding_model == model,
            )
        )
        with Session(self.writer) as session:
            found = session.scalar(statement)
        return int(found or 0) > 0

    def blocked_by_tombstone(
        self, source_id: str, object_id: str, version: str, content_hash: str
    ) -> bool:
        statement = select(Tombstone).where(
            Tombstone.source_id == source_id, Tombstone.object_id == object_id
        )
        with Session(self.writer) as session:
            row = session.scalar(statement)
        if row is None:
            return False
        # Block only when version and content hash both match. A newer create is allowed.
        return row.version == (version or "") and row.content_hash == (content_hash or "")

    def replace_chunks(
        self,
        source_id: str,
        object_id: str,
        object_type: str,
        version: str,
        content_hash: str,
        model: str,
        chunks: list[ChunkDraft],
        vectors: list[list[float]],
        acl: AclView,
    ) -> None:
        rules = acl.as_json()
        with Session(self.writer) as session, session.begin():
            session.execute(
                delete(Tombstone).where(
                    Tombstone.source_id == source_id, Tombstone.object_id == object_id
                )
            )
            # This model only. Another model's rows survive reindex.
            session.execute(
                delete(Chunk).where(
                    Chunk.source_id == source_id,
                    Chunk.object_id == object_id,
                    Chunk.embedding_model == model,
                )
            )
            session.add_all(
                [
                    Chunk(
                        source_id=source_id,
                        object_id=object_id,
                        object_type=object_type,
                        chunk_index=chunk.index,
                        version=version,
                        section_path=chunk.section_path,
                        content=chunk.text,
                        content_hash=content_hash,
                        chunker_version=chunk.chunker_version,
                        embedding_model=model,
                        acl_groups=list(acl.groups),
                        acl_departments=list(acl.departments),
                        min_clearance=acl.min_clearance,
                        acl_rules=rules,
                        embedding=vector,
                    )
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ]
            )

    def touch(self, source_id: str, object_id: str, version: str) -> None:
        statement = (
            update(Chunk)
            .where(Chunk.source_id == source_id, Chunk.object_id == object_id)
            .values(synced_at=func.now(), version=version)
        )
        with Session(self.writer) as session, session.begin():
            session.execute(statement)

    def update_acl(self, source_id: str, object_id: str, acl: AclView) -> int:
        statement = (
            update(Chunk)
            .where(Chunk.source_id == source_id, Chunk.object_id == object_id)
            .values(
                acl_groups=list(acl.groups),
                acl_departments=list(acl.departments),
                min_clearance=acl.min_clearance,
                acl_rules=acl.as_json(),
                synced_at=func.now(),
            )
        )
        with Session(self.writer) as session, session.begin():
            result = session.execute(statement)
        return result.rowcount or 0

    def tombstone(self, source_id: str, object_id: str, version: str, content_hash: str) -> None:
        values = {
            "source_id": source_id,
            "object_id": object_id,
            "version": version or "",
            "content_hash": content_hash or "",
        }
        upsert = pg_insert(Tombstone).values(**values)
        upsert = upsert.on_conflict_do_update(
            index_elements=[Tombstone.source_id, Tombstone.object_id],
            set_={
                "version": upsert.excluded.version,
                "content_hash": upsert.excluded.content_hash,
                "deleted_at": func.now(),
            },
        )
        with Session(self.writer) as session, session.begin():
            session.execute(upsert)
            session.execute(
                delete(Chunk).where(Chunk.source_id == source_id, Chunk.object_id == object_id)
            )

    def count_chunks(self, source_id: str, object_id: str) -> int:
        statement = (
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.source_id == source_id, Chunk.object_id == object_id)
        )
        with Session(self.writer) as session:
            return int(session.scalar(statement) or 0)

    def count_visible(self, principal: Principal | None) -> int:
        """No application predicate. Row-level security is the only filter."""
        with Session(self.reader) as session, session.begin():
            _set_principal(session, principal)
            found = session.scalar(select(func.count()).select_from(Chunk))
        return int(found or 0)

    def search(
        self,
        query: str,
        principal: Principal,
        model: str,
        query_vector: list[float],
        limit: int,
        request_id: str,
        semantic: bool = False,
        max_distance: float = DEFAULT_MAX_DISTANCE,
        object_ids: list[str] | None = None,
    ) -> list[Hit]:
        fetch = max(limit * OVERFETCH, limit)
        vector_sql = _vector_select(principal, model, query_vector, fetch, object_ids)
        lexical_sql = _lexical_select(principal, model, query, fetch, object_ids)
        with Session(self.reader) as session, session.begin():
            _set_principal(session, principal)
            # The ACL predicate is in each statement. It is not a separate query.
            with span("chunks.vector", acl_prefilter=True, model=model) as current:
                vector_rows = list(session.execute(vector_sql).mappings())
                current.set_attribute("count", len(vector_rows))
            with span("chunks.full_text", acl_prefilter=True, model=model) as current:
                lexical_rows = list(session.execute(lexical_sql).mappings())
                current.set_attribute("count", len(lexical_rows))
            ranked = _fuse(vector_rows, lexical_rows)
            # A vector-only neighbor is an answer only when the model is semantic and the
            # chunk is close enough. hash-v1 never qualifies. Nearest-neighbour search
            # always returns something, so the floor is what lets a question abstain.
            lexical_ids = {int(row["id"]) for row in lexical_rows}
            close_ids = {
                int(row["id"]) for row in vector_rows if float(row["distance"]) <= max_distance
            }
            hits: list[Hit] = []
            denied = 0
            with span("audit.write") as current:
                for row, score in ranked:
                    row_id = int(row["id"])
                    if row_id not in lexical_ids and not (semantic and row_id in close_ids):
                        continue
                    view = view_from_rules(row["acl_rules"])
                    allowed, rule = allows(view, principal)
                    if not allowed:
                        denied += 1
                        _audit(session, principal, row, "deny", None, request_id)
                        continue
                    _audit(session, principal, row, "allow", rule, request_id)
                    hits.append(_hit(row, score))
                    if len(hits) >= limit:
                        break
                if not hits and not denied:
                    denied += 1
                    _audit(session, principal, None, "deny", None, request_id)
                current.set_attribute("count", len(hits))
                current.set_attribute("denied", denied)
            return hits

    def get_chunk(self, chunk_id: int, principal: Principal, request_id: str) -> Hit | None:
        """One chunk the caller may read. Missing and unauthorized both return None."""
        statement = select(
            Chunk.id,
            Chunk.source_id,
            Chunk.object_id,
            Chunk.object_type,
            Chunk.version,
            Chunk.section_path,
            Chunk.content,
            Chunk.acl_rules,
            Chunk.synced_at,
        ).where(Chunk.id == chunk_id)
        with Session(self.reader) as session, session.begin():
            _set_principal(session, principal)
            row = session.execute(statement).mappings().first()
            if row is None:
                return None
            view = view_from_rules(row["acl_rules"])
            allowed, rule = allows(view, principal)
            if not allowed:
                _audit(session, principal, row, "deny", None, request_id)
                return None
            _audit(session, principal, row, "allow", rule, request_id)
            return _hit(row, 1.0)

    def record_decisions(
        self,
        principal: Principal,
        request_id: str,
        decisions: list[tuple[str, str, dict[str, str] | None, str | None]],
    ) -> None:
        """Audit graph allows and denies. An empty list is one deny with no object."""
        with Session(self.reader) as session, session.begin():
            if not decisions:
                _audit(session, principal, None, "deny", None, request_id)
                return
            for decision, source_id, rule, object_id in decisions:
                _audit(
                    session,
                    principal,
                    None,
                    decision,
                    rule,
                    request_id,
                    source_id=source_id,
                    object_id=object_id,
                )

    def last_success(self) -> dict[str, str | None]:
        statement = select(SyncState.source_id, SyncState.last_success_at)
        with Session(self.reader) as session:
            found: dict[str, str | None] = {}
            for source_id, stamp in session.execute(statement):
                found[source_id] = None if stamp is None else stamp.isoformat()
            return found

    def orphans(self) -> list[tuple[str, str, str]]:
        statement = (
            select(Chunk.source_id, Chunk.object_id, Chunk.object_type)
            .distinct()
            .outerjoin(
                SourceObject,
                and_(
                    SourceObject.source_id == Chunk.source_id,
                    SourceObject.object_id == Chunk.object_id,
                ),
            )
            .where(SourceObject.object_id.is_(None))
        )
        with Session(self.writer) as session:
            return [
                (row.source_id, row.object_id, row.object_type)
                for row in session.execute(statement)
            ]

    def delete_other_models(self, model: str) -> int:
        statement = delete(Chunk).where(Chunk.embedding_model != model)
        with Session(self.writer) as session, session.begin():
            return int(session.execute(statement).rowcount or 0)

    def count_by_model(self) -> dict[str, int]:
        statement = select(Chunk.embedding_model, func.count()).group_by(Chunk.embedding_model)
        with Session(self.writer) as session:
            return {model: int(count) for model, count in session.execute(statement)}

    def missing(self, model: str) -> list[dict]:
        indexed = (
            select(Chunk.id)
            .where(
                Chunk.source_id == SourceObject.source_id,
                Chunk.object_id == SourceObject.object_id,
                Chunk.version == SourceObject.version,
                Chunk.embedding_model == model,
            )
            .exists()
        )
        statement = select(
            SourceObject.source_id,
            SourceObject.object_id,
            SourceObject.object_type,
            SourceObject.version,
            SourceObject.content_hash,
            SourceObject.s3_key,
        ).where(~indexed)
        with Session(self.writer) as session:
            return [dict(row) for row in session.execute(statement).mappings()]

    def drift(self) -> dict[str, tuple[int, int]]:
        indexed_ids = select(Chunk.source_id, Chunk.object_id).distinct().subquery()
        indexed = select(
            indexed_ids.c.source_id.label("source_id"),
            func.count().label("n"),
            literal("index").label("kind"),
        ).group_by(indexed_ids.c.source_id)
        sourced = select(
            SourceObject.source_id.label("source_id"),
            func.count().label("n"),
            literal("source").label("kind"),
        ).group_by(SourceObject.source_id)
        found: dict[str, dict[str, int]] = {}
        with Session(self.writer) as session:
            for row in session.execute(union_all(indexed, sourced)).mappings():
                found.setdefault(row["source_id"], {"index": 0, "source": 0})[row["kind"]] = int(
                    row["n"]
                )
        return {key: (value["index"], value["source"]) for key, value in found.items()}


def _columns():
    return (
        Chunk.id,
        Chunk.source_id,
        Chunk.object_id,
        Chunk.object_type,
        Chunk.version,
        Chunk.section_path,
        Chunk.content,
        Chunk.acl_rules,
        Chunk.synced_at,
    )


def _acl(principal: Principal):
    return and_(
        Chunk.acl_groups.overlap(literal(list(principal.groups), type_=ARRAY(Text))),
        or_(
            func.cardinality(Chunk.acl_departments) == 0,
            literal(principal.department) == any_(Chunk.acl_departments),
        ),
        Chunk.min_clearance <= func.kbs.clearance_rank(literal(principal.clearance)),
    )


def _limit_objects(statement, object_ids: list[str] | None):
    if object_ids is None:
        return statement
    if not object_ids:
        return statement.where(false())
    return statement.where(Chunk.object_id.in_(object_ids))


def _vector_select(
    principal: Principal,
    model: str,
    query_vector: list[float],
    fetch: int,
    object_ids: list[str] | None,
):
    distance = Chunk.embedding.cosine_distance(query_vector).label("distance")
    statement = (
        select(*_columns(), distance)
        .where(Chunk.embedding_model == model, _acl(principal))
        .order_by(distance)
        .limit(fetch)
    )
    return _limit_objects(statement, object_ids)


def _lexical_select(
    principal: Principal,
    model: str,
    query: str,
    fetch: int,
    object_ids: list[str] | None,
):
    tsquery = func.plainto_tsquery("english", query)
    statement = (
        select(*_columns())
        .where(Chunk.embedding_model == model, _acl(principal), Chunk.fts.op("@@")(tsquery))
        .order_by(func.ts_rank(Chunk.fts, tsquery).desc())
        .limit(fetch)
    )
    return _limit_objects(statement, object_ids)


def _hit(row, score: float) -> Hit:
    stamp = row["synced_at"]
    return Hit(
        chunk_id=int(row["id"]),
        source_id=row["source_id"],
        object_id=row["object_id"],
        object_type=row["object_type"] or "",
        version=row["version"],
        section_path=row["section_path"],
        text=row["content"],
        score=score,
        synced_at=stamp.isoformat() if hasattr(stamp, "isoformat") else str(stamp),
    )


def _set_principal(session: Session, principal: Principal | None) -> None:
    groups = ",".join(principal.groups) if principal else ""
    clearance = principal.clearance if principal else ""
    department = principal.department if principal else ""
    session.execute(select(func.set_config("kbs.groups", groups, True)))
    session.execute(select(func.set_config("kbs.clearance", clearance, True)))
    session.execute(select(func.set_config("kbs.department", department, True)))


def _audit(
    session: Session,
    principal: Principal,
    row,
    decision: str,
    rule,
    request_id: str,
    source_id: str | None = None,
    object_id: str | None = None,
) -> None:
    attributes = {
        "groups": list(principal.groups),
        "clearance": principal.clearance,
        "department": principal.department,
    }
    if rule:
        attributes["rule"] = rule
    if object_id:
        attributes["object_id"] = object_id
    if row is None:
        chunk_id = None
        resolved_source = source_id or ""
    else:
        chunk_id = int(row["id"])
        resolved_source = row["source_id"] if source_id is None else source_id
    # RETURNING would require SELECT, which gateway_ro does not have.
    session.connection().execute(
        insert(AuditLog.__table__).values(
            principal=principal.subject,
            chunk_id=chunk_id,
            attributes=attributes,
            request_id=request_id,
            decision=decision,
            source_id=resolved_source,
        )
    )


def _fuse(vector_rows: list, lexical_rows: list) -> list[tuple[object, float]]:
    scores: dict[int, float] = {}
    by_id = {}
    for rank, row in enumerate(vector_rows, start=1):
        scores[int(row["id"])] = scores.get(int(row["id"]), 0.0) + 1.0 / (RRF_K + rank)
        by_id[int(row["id"])] = row
    for rank, row in enumerate(lexical_rows, start=1):
        scores[int(row["id"])] = scores.get(int(row["id"]), 0.0) + 1.0 / (RRF_K + rank)
        by_id[int(row["id"])] = row
    ordered = sorted(scores, key=lambda key: scores[key], reverse=True)
    return [(by_id[key], scores[key]) for key in ordered]
