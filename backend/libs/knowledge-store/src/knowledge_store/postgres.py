"""Chunk rows, tombstones, and authorized search.

The writer connects as ingestion_rw. Search connects as gateway_ro, so a query
that forgets the predicate still cannot read another principal's rows.
"""

import json
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from knowledge_store.acl import AclView, Principal, allows
from knowledge_store.chunkers import ChunkDraft
from knowledge_store.embedder import HASH_MODEL

RRF_K = 60
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
                connection.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 - the driver raises several connection errors
            return False
        return True

    def same_hash(self, source_id: str, object_id: str, content_hash: str, model: str) -> bool:
        statement = text(
            """
            SELECT count(*) AS n FROM chunks
            WHERE source_id = :source_id AND object_id = :object_id
              AND content_hash = :content_hash AND embedding_model = :model
            """
        )
        with self.writer.connect() as connection:
            row = connection.execute(
                statement,
                {
                    "source_id": source_id,
                    "object_id": object_id,
                    "content_hash": content_hash,
                    "model": model,
                },
            ).one()
        return int(row.n) > 0

    def blocked_by_tombstone(self, source_id: str, object_id: str, version: str, content_hash: str) -> bool:
        statement = text(
            """
            SELECT version, content_hash FROM tombstones
            WHERE source_id = :source_id AND object_id = :object_id
            """
        )
        with self.writer.connect() as connection:
            row = connection.execute(
                statement, {"source_id": source_id, "object_id": object_id}
            ).first()
        if row is None:
            return False
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
        delete = text(
            """
            DELETE FROM chunks
            WHERE source_id = :source_id AND object_id = :object_id AND embedding_model = :model
            """
        )
        forget = text(
            "DELETE FROM tombstones WHERE source_id = :source_id AND object_id = :object_id"
        )
        insert = text(
            """
            INSERT INTO chunks (
                source_id, object_id, object_type, chunk_index, version, section_path,
                content, content_hash, chunker_version, embedding_model, acl_groups,
                acl_departments, min_clearance, acl_rules, embedding, synced_at
            ) VALUES (
                :source_id, :object_id, :object_type, :chunk_index, :version, :section_path,
                :content, :content_hash, :chunker_version, :embedding_model,
                CAST(:acl_groups AS text[]), CAST(:acl_departments AS text[]),
                :min_clearance, CAST(:acl_rules AS jsonb),
                CAST(:embedding AS vector), now()
            )
            """
        )
        rules = json.dumps(acl.as_json())
        with self.writer.begin() as connection:
            connection.execute(forget, {"source_id": source_id, "object_id": object_id})
            connection.execute(
                delete, {"source_id": source_id, "object_id": object_id, "model": model}
            )
            for chunk, vector in zip(chunks, vectors, strict=True):
                connection.execute(
                    insert,
                    {
                        "source_id": source_id,
                        "object_id": object_id,
                        "object_type": object_type,
                        "chunk_index": chunk.index,
                        "version": version,
                        "section_path": chunk.section_path,
                        "content": chunk.text,
                        "content_hash": content_hash,
                        "chunker_version": chunk.chunker_version,
                        "embedding_model": model,
                        "acl_groups": _pg_array(acl.groups),
                        "acl_departments": _pg_array(acl.departments),
                        "min_clearance": acl.min_clearance,
                        "acl_rules": rules,
                        "embedding": _vector(vector),
                    },
                )

    def touch(self, source_id: str, object_id: str, version: str) -> None:
        statement = text(
            """
            UPDATE chunks SET synced_at = now(), version = :version
            WHERE source_id = :source_id AND object_id = :object_id
            """
        )
        with self.writer.begin() as connection:
            connection.execute(
                statement, {"source_id": source_id, "object_id": object_id, "version": version}
            )

    def update_acl(self, source_id: str, object_id: str, acl: AclView) -> int:
        statement = text(
            """
            UPDATE chunks
            SET acl_groups = CAST(:acl_groups AS text[]),
                acl_departments = CAST(:acl_departments AS text[]),
                min_clearance = :min_clearance, acl_rules = CAST(:acl_rules AS jsonb),
                synced_at = now()
            WHERE source_id = :source_id AND object_id = :object_id
            """
        )
        with self.writer.begin() as connection:
            result = connection.execute(
                statement,
                {
                    "source_id": source_id,
                    "object_id": object_id,
                    "acl_groups": _pg_array(acl.groups),
                    "acl_departments": _pg_array(acl.departments),
                    "min_clearance": acl.min_clearance,
                    "acl_rules": json.dumps(acl.as_json()),
                },
            )
        return result.rowcount or 0

    def tombstone(self, source_id: str, object_id: str, version: str, content_hash: str) -> None:
        statement = text(
            """
            INSERT INTO tombstones (source_id, object_id, version, content_hash)
            VALUES (:source_id, :object_id, :version, :content_hash)
            ON CONFLICT (source_id, object_id) DO UPDATE
            SET version = EXCLUDED.version, content_hash = EXCLUDED.content_hash,
                deleted_at = now()
            """
        )
        delete = text(
            "DELETE FROM chunks WHERE source_id = :source_id AND object_id = :object_id"
        )
        params = {
            "source_id": source_id,
            "object_id": object_id,
            "version": version or "",
            "content_hash": content_hash or "",
        }
        with self.writer.begin() as connection:
            connection.execute(statement, params)
            connection.execute(delete, params)

    def count_chunks(self, source_id: str, object_id: str) -> int:
        statement = text(
            "SELECT count(*) AS n FROM chunks WHERE source_id = :source_id AND object_id = :object_id"
        )
        with self.writer.connect() as connection:
            return int(
                connection.execute(
                    statement, {"source_id": source_id, "object_id": object_id}
                ).one().n
            )

    def count_visible(self, principal: Principal | None) -> int:
        """No application predicate. Row-level security is the only filter."""
        with self.reader.begin() as connection:
            _set_principal(connection, principal)
            return int(connection.execute(text("SELECT count(*) AS n FROM chunks")).one().n)

    def search(
        self,
        query: str,
        principal: Principal,
        model: str,
        query_vector: list[float],
        limit: int,
        request_id: str,
    ) -> list[Hit]:
        fetch = max(limit * OVERFETCH, limit)
        params = {
            "model": model,
            "query": query,
            "vector": _vector(query_vector),
            "groups": _pg_array(principal.groups),
            "department": principal.department,
            "clearance": principal.clearance,
            "fetch": fetch,
        }
        vector_sql = text(
            """
            SELECT id, source_id, object_id, object_type, version, section_path,
                   content, acl_rules, synced_at
            FROM chunks
            WHERE embedding_model = :model
              AND acl_groups && CAST(:groups AS text[])
              AND (
                cardinality(acl_departments) = 0
                OR :department = ANY (acl_departments)
              )
              AND min_clearance <= kbs.clearance_rank(:clearance)
            ORDER BY embedding <=> CAST(:vector AS vector)
            LIMIT :fetch
            """
        )
        lexical_sql = text(
            """
            SELECT id, source_id, object_id, object_type, version, section_path,
                   content, acl_rules, synced_at
            FROM chunks
            WHERE embedding_model = :model
              AND acl_groups && CAST(:groups AS text[])
              AND (
                cardinality(acl_departments) = 0
                OR :department = ANY (acl_departments)
              )
              AND min_clearance <= kbs.clearance_rank(:clearance)
              AND fts @@ plainto_tsquery('english', :query)
            ORDER BY ts_rank(fts, plainto_tsquery('english', :query)) DESC
            LIMIT :fetch
            """
        )
        with self.reader.begin() as connection:
            _set_principal(connection, principal)
            vector_rows = list(connection.execute(vector_sql, params).mappings())
            lexical_rows = list(connection.execute(lexical_sql, params).mappings())
            ranked = _fuse(vector_rows, lexical_rows)
            # hash-v1 is not semantic. A vector-only neighbor is not an answer.
            lexical_ids = {int(row["id"]) for row in lexical_rows}
            hits: list[Hit] = []
            denied = False
            for row, score in ranked:
                if model == HASH_MODEL and int(row["id"]) not in lexical_ids:
                    continue
                view = _view_from_rules(row["acl_rules"])
                allowed, rule = allows(view, principal)
                if not allowed:
                    denied = True
                    _audit(connection, principal, row, "deny", None, request_id)
                    continue
                _audit(connection, principal, row, "allow", rule, request_id)
                hits.append(_hit(row, score))
                if len(hits) >= limit:
                    break
            if not hits and not denied:
                _audit(connection, principal, None, "deny", None, request_id)
            return hits

    def get_chunk(self, chunk_id: int, principal: Principal, request_id: str) -> Hit | None:
        """One chunk the caller may read. Missing and unauthorized both return None."""
        statement = text(
            """
            SELECT id, source_id, object_id, object_type, version, section_path,
                   content, acl_rules, synced_at
            FROM chunks
            WHERE id = :id
            """
        )
        with self.reader.begin() as connection:
            _set_principal(connection, principal)
            row = connection.execute(statement, {"id": chunk_id}).mappings().first()
            if row is None:
                return None
            view = _view_from_rules(row["acl_rules"])
            allowed, rule = allows(view, principal)
            if not allowed:
                _audit(connection, principal, row, "deny", None, request_id)
                return None
            _audit(connection, principal, row, "allow", rule, request_id)
            return _hit(row, 1.0)

    def last_success(self) -> dict[str, str | None]:
        statement = text("SELECT source_id, last_success_at FROM sync_state")
        with self.reader.connect() as connection:
            found: dict[str, str | None] = {}
            for row in connection.execute(statement):
                stamp = row.last_success_at
                found[row.source_id] = None if stamp is None else stamp.isoformat()
            return found

    def orphans(self) -> list[tuple[str, str, str]]:
        statement = text(
            """
            SELECT DISTINCT c.source_id, c.object_id, c.object_type
            FROM chunks c
            LEFT JOIN source_objects s
              ON s.source_id = c.source_id AND s.object_id = c.object_id
            WHERE s.object_id IS NULL
            """
        )
        with self.writer.connect() as connection:
            return [
                (row.source_id, row.object_id, row.object_type)
                for row in connection.execute(statement)
            ]

    def missing(self, model: str) -> list[dict]:
        statement = text(
            """
            SELECT s.source_id, s.object_id, s.object_type, s.version, s.content_hash, s.s3_key
            FROM source_objects s
            WHERE NOT EXISTS (
                SELECT 1 FROM chunks c
                WHERE c.source_id = s.source_id AND c.object_id = s.object_id
                  AND c.version = s.version AND c.embedding_model = :model
            )
            """
        )
        with self.writer.connect() as connection:
            return [dict(row) for row in connection.execute(statement, {"model": model}).mappings()]

    def drift(self) -> dict[str, tuple[int, int]]:
        statement = text(
            """
            SELECT source_id, count(*) AS n, 'index' AS kind FROM (
                SELECT DISTINCT source_id, object_id FROM chunks
            ) indexed
            GROUP BY source_id
            UNION ALL
            SELECT source_id, count(*) AS n, 'source' AS kind FROM source_objects
            GROUP BY source_id
            """
        )
        found: dict[str, dict[str, int]] = {}
        with self.writer.connect() as connection:
            for row in connection.execute(statement):
                found.setdefault(row.source_id, {"index": 0, "source": 0})[row.kind] = int(row.n)
        return {key: (value["index"], value["source"]) for key, value in found.items()}


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


def _set_principal(connection, principal: Principal | None) -> None:
    groups = ",".join(principal.groups) if principal else ""
    clearance = principal.clearance if principal else ""
    department = principal.department if principal else ""
    connection.execute(text("SELECT set_config('kbs.groups', :v, true)"), {"v": groups})
    connection.execute(text("SELECT set_config('kbs.clearance', :v, true)"), {"v": clearance})
    connection.execute(text("SELECT set_config('kbs.department', :v, true)"), {"v": department})


def _audit(connection, principal: Principal, row, decision: str, rule, request_id: str) -> None:
    attributes = {
        "groups": list(principal.groups),
        "clearance": principal.clearance,
        "department": principal.department,
    }
    if rule:
        attributes["rule"] = rule
    connection.execute(
        text(
            """
            INSERT INTO audit_log (principal, chunk_id, attributes, request_id, decision, source_id)
            VALUES (
                :principal, :chunk_id, CAST(:attributes AS jsonb), :request_id, :decision, :source_id
            )
            """
        ),
        {
            "principal": principal.subject,
            "chunk_id": None if row is None else int(row["id"]),
            "attributes": json.dumps(attributes),
            "request_id": request_id,
            "decision": decision,
            "source_id": "" if row is None else row["source_id"],
        },
    )


def _view_from_rules(raw) -> AclView:
    rules = raw if isinstance(raw, list) else json.loads(raw or "[]")
    typed = tuple(dict(rule) for rule in rules)
    groups = tuple(sorted({rule["group"] for rule in typed if rule.get("group")}))
    departments = tuple(sorted({rule["department"] for rule in typed if rule.get("department")}))
    return AclView(groups, departments, 0, typed, ())


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


def _pg_array(values: tuple[str, ...] | list[str]) -> str:
    escaped = (value.replace("\\", "\\\\").replace('"', '\\"') for value in values)
    return "{" + ",".join(f'"{value}"' for value in escaped) + "}"


def _vector(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.6f}" for value in values) + "]"
