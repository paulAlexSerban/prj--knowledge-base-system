"""One change event, applied to Postgres and then Neo4j.

A Neo4j failure leaves the message unacknowledged. The Postgres upsert is
idempotent, so the retry is safe. A delete writes a tombstone first, so a
replayed older create does not bring the object back.
"""

import logging
from datetime import UTC, datetime

from botocore.exceptions import BotoCoreError, ClientError

from ingest_contract.events import ChangeEvent, EventType
from knowledge_store import metrics
from knowledge_store.acl import Principal, map_acl
from knowledge_store.chunkers import Parsed, parse_object
from knowledge_store.embedder import Embedder
from knowledge_store.graph import GraphWriter
from knowledge_store.postgres import ChunkTable, Hit, VerifyError
from shared.contracts.source_contract import SourceContract
from shared.logging import log_event
from shared.telemetry import span

LOG = logging.getLogger("ingestion-worker")


class StoreError(Exception):
    pass


class KnowledgeStore:
    def __init__(
        self,
        chunks: ChunkTable,
        graph: GraphWriter,
        snapshots: object,
        bucket: str,
        embedder: Embedder,
        contracts: dict[str, SourceContract],
    ) -> None:
        self.chunks = chunks
        self.graph = graph
        self.snapshots = snapshots
        self.bucket = bucket
        self.embedder = embedder
        self.contracts = contracts

    def upsert_object(self, event: ChangeEvent) -> None:
        self._write(event, acl_only=False)

    def apply_acl(self, event: ChangeEvent) -> None:
        self._write(event, acl_only=True)

    def delete_object(self, event: ChangeEvent) -> None:
        source_id = event.source_id
        object_id = event.object_id
        object_type = _type_from_pointer(event.s3_pointer)
        try:
            with (
                span(
                    "postgres.commit", store="postgres", op="delete", source_id=source_id
                ) as current,
                metrics.STORE_WRITE.labels(store="postgres", op="delete").time(),
            ):
                _annotate_write(current, event, object_type)
                self.chunks.tombstone(
                    source_id, object_id, event.version or "", event.content_hash or ""
                )
        except Exception:
            metrics.STORE_ERRORS.labels(store="postgres", op="delete").inc()
            raise
        with span("delete.verify", store="postgres", source_id=source_id) as current:
            remaining = self.chunks.count_chunks(source_id, object_id)
            current.set_attribute("remaining", remaining)
            if remaining:
                metrics.DELETE_FAILURES.labels(source_id, "postgres").inc()
                raise VerifyError("postgres")
        try:
            with (
                span("neo4j.write", store="neo4j", op="delete", source_id=source_id) as current,
                metrics.STORE_WRITE.labels(store="neo4j", op="delete").time(),
            ):
                _annotate_write(current, event, object_type)
                self.graph.delete(source_id, object_id)
        except Exception:
            metrics.STORE_ERRORS.labels(store="neo4j", op="delete").inc()
            raise
        with span("delete.verify", store="neo4j", source_id=source_id) as current:
            remaining = self.graph.count_node(source_id, object_id)
            current.set_attribute("remaining", remaining)
            if remaining:
                metrics.DELETE_FAILURES.labels(source_id, "neo4j").inc()
                raise VerifyError("neo4j")
        log_event(LOG, "object deleted", source_id=source_id, object_id=object_id)

    def search(
        self,
        query: str,
        principal: Principal,
        limit: int = 5,
        request_id: str = "",
        object_ids: list[str] | None = None,
    ) -> list[Hit]:
        with span("embed.query", model=self.embedder.name, semantic=self.embedder.semantic):
            vector = self.embedder.embed_query(query)
        metrics.EMBED_CALLS.labels(model=self.embedder.name).inc()
        return self.chunks.search(
            query,
            principal,
            self.embedder.name,
            vector,
            limit,
            request_id,
            semantic=self.embedder.semantic,
            object_ids=object_ids,
        )

    def reconcile_index(self) -> None:
        for source_id, object_id, _object_type in self.chunks.orphans():
            self.delete_object(
                ChangeEvent(
                    source_id=source_id,
                    object_id=object_id,
                    event_type=EventType.DELETE,
                    version="orphan",
                    occurred_at=datetime.now(UTC),
                )
            )
        for row in self.chunks.missing(self.embedder.name):
            try:
                self.upsert_object(
                    ChangeEvent(
                        source_id=row["source_id"],
                        object_id=row["object_id"],
                        event_type=EventType.UPDATE,
                        version=row["version"],
                        content_hash=row["content_hash"],
                        occurred_at=datetime.now(UTC),
                        s3_pointer=row["s3_key"],
                    )
                )
            except StoreError as exc:
                # One stale or missing snapshot must not stop the sweep. A newer event for
                # that object is already queued, or the next sweep retries it.
                log_event(
                    LOG,
                    "reconcile skipped object",
                    source_id=row["source_id"],
                    object_id=row["object_id"],
                    error=str(exc)[:120],
                )
        for source_id, (indexed, source) in self.chunks.drift().items():
            metrics.INDEX_OBJECTS.labels(source_id=source_id).set(indexed)
            metrics.INDEX_DRIFT.labels(source_id=source_id).set(indexed - source)

    def reindex(self) -> dict[str, int]:
        """Rebuild under the active embedding model, then drop other models' chunks.

        New chunks are written next to the old ones. Search filters to the active
        model, so readers never see a mix. The old rows go last, so a failure
        halfway leaves the previous index serving. ADR 0006.
        """
        pending = self.chunks.missing(self.embedder.name)
        self.reconcile_index()
        still_missing = self.chunks.missing(self.embedder.name)
        removed = 0 if still_missing else self.chunks.delete_other_models(self.embedder.name)
        return {"rebuilt": len(pending) - len(still_missing), "missing": len(still_missing), "removed": removed}

    def _write(self, event: ChangeEvent, acl_only: bool) -> None:
        if not event.s3_pointer:
            raise StoreError("snapshot pointer is required")
        contract = self.contracts.get(event.source_id)
        if contract is None:
            raise StoreError(f"no contract for {event.source_id}")
        if self.chunks.blocked_by_tombstone(
            event.source_id, event.object_id, event.version or "", event.content_hash or ""
        ):
            log_event(
                LOG,
                "event ignored after tombstone",
                source_id=event.source_id,
                object_id=event.object_id,
            )
            return
        body, native, object_type = self._snapshot(event)
        acl = map_acl(native, contract)
        if acl.unmapped:
            log_event(
                LOG,
                "unmapped acl",
                source_id=event.source_id,
                object_id=event.object_id,
                fields=",".join(acl.unmapped),
            )
        parsed = parse_object(object_type, body, contract)
        version = event.version or ""
        content_hash = event.content_hash or ""
        if acl_only and self.chunks.update_acl(event.source_id, event.object_id, acl):
            self._graph_acl(event, acl, object_type)
            log_event(
                LOG,
                "acl updated",
                source_id=event.source_id,
                object_id=event.object_id,
                event_type=event.event_type.value,
                chunker_version=_chunker_version(parsed),
            )
            return
        if content_hash and self.chunks.same_hash(
            event.source_id, event.object_id, content_hash, self.embedder.name
        ):
            # Bytes already indexed. Skip the embed and the chunk rewrite, and
            # still write Neo4j. A retry after a graph failure lands here.
            self.chunks.touch(event.source_id, event.object_id, version)
            metrics.EMBED_SKIPPED.labels(source_id=event.source_id).inc()
            self._graph_write(event, parsed, acl, version)
            _log_written(event, parsed)
            return
        vectors = self.embedder.embed([chunk.text for chunk in parsed.chunks])
        metrics.EMBED_CALLS.labels(model=self.embedder.name).inc(len(parsed.chunks))
        try:
            with (
                span(
                    "postgres.commit", store="postgres", op="upsert", source_id=event.source_id
                ) as current,
                metrics.STORE_WRITE.labels(store="postgres", op="upsert").time(),
            ):
                _annotate_write(current, event, object_type, len(parsed.chunks))
                self.chunks.replace_chunks(
                    event.source_id,
                    event.object_id,
                    object_type,
                    version,
                    content_hash,
                    self.embedder.name,
                    list(parsed.chunks),
                    vectors,
                    acl,
                )
        except Exception:
            metrics.STORE_ERRORS.labels(store="postgres", op="upsert").inc()
            raise
        self._graph_write(event, parsed, acl, version)
        _log_written(event, parsed)

    def _graph_write(self, event: ChangeEvent, parsed: Parsed, acl, version: str) -> None:
        try:
            with (
                span(
                    "neo4j.write", store="neo4j", op="upsert", source_id=event.source_id
                ) as current,
                metrics.STORE_WRITE.labels(store="neo4j", op="upsert").time(),
            ):
                _annotate_write(current, event, parsed.object_type, len(parsed.chunks))
                self.graph.write(
                    event.source_id,
                    parsed.object_type,
                    event.object_id,
                    parsed.record,
                    parsed.title,
                    acl,
                    version,
                )
        except Exception:
            metrics.STORE_ERRORS.labels(store="neo4j", op="upsert").inc()
            raise

    def _graph_acl(self, event: ChangeEvent, acl, object_type: str) -> None:
        try:
            with (
                span("neo4j.write", store="neo4j", op="acl", source_id=event.source_id) as current,
                metrics.STORE_WRITE.labels(store="neo4j", op="acl").time(),
            ):
                _annotate_write(current, event, object_type)
                self.graph.set_acl(event.source_id, event.object_id, acl)
        except Exception:
            metrics.STORE_ERRORS.labels(store="neo4j", op="acl").inc()
            raise

    def _snapshot(self, event: ChangeEvent) -> tuple[bytes, list[str], str]:
        key = event.s3_pointer or ""
        try:
            head = self.snapshots.head_object(Bucket=self.bucket, Key=key)
            body = self.snapshots.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except (ClientError, BotoCoreError, OSError) as exc:
            metrics.DEPENDENCY.labels(dependency="s3").set(0)
            raise StoreError(str(exc)) from exc
        metrics.DEPENDENCY.labels(dependency="s3").set(1)
        meta = {name.lower(): value for name, value in head.get("Metadata", {}).items()}
        stored = meta.get("kbs-content-hash", "")
        # Refuse a stale pointer. The S3 object hash must match the event.
        if event.content_hash and stored and stored != event.content_hash:
            raise StoreError("snapshot hash does not match the event")
        native = [part for part in meta.get("kbs-native-acl", "").split(",") if part]
        return body, native, _object_type(key)


def _chunker_version(parsed: Parsed) -> str:
    if not parsed.chunks:
        return ""
    return parsed.chunks[0].chunker_version


def _log_written(event: ChangeEvent, parsed: Parsed) -> None:
    log_event(
        LOG,
        "object written",
        source_id=event.source_id,
        object_id=event.object_id,
        event_type=event.event_type.value,
        chunker_version=_chunker_version(parsed),
    )


def _object_type(key: str) -> str:
    found = _type_from_pointer(key)
    if not found:
        raise StoreError(f"unexpected snapshot key {key}")
    return found


def _type_from_pointer(key: str | None) -> str:
    parts = (key or "").split("/")
    if len(parts) < 4 or parts[0] != "raw":
        return ""
    return parts[2]


def _annotate_write(
    current, event: ChangeEvent, object_type: str, chunks: int | None = None
) -> None:
    current.set_attribute("event_type", event.event_type.value)
    if object_type:
        current.set_attribute("object_type", object_type)
    if chunks is not None:
        current.set_attribute("chunks", chunks)
