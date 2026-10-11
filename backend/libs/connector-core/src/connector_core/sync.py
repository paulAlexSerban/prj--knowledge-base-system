"""One sync cycle and the reconciliation sweep.

S3 is written before Postgres. A crash retries the object; an unchanged hash
does not create another S3 version. The watermark moves only when every object
in the cycle succeeded, so a failure is retried and a success is skipped.

An item with `op: delete` uses the same verified delete as the sweep. The
watermark may be a cursor rather than a timestamp. Lag is recorded only when
the watermark is an ISO timestamp.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.exc import SQLAlchemyError

from connector_core import metrics
from connector_core.base import ConnectorError, SourceConnector
from connector_core.state import ObjectRow, State
from connector_core.store import (
    METADATA_ACL,
    METADATA_HASH,
    METADATA_SNAPSHOT_AT,
    METADATA_SOURCE_UPDATED,
    METADATA_VERSION,
    SnapshotStore,
    object_key,
)
from ingest_contract.events import ChangeEvent, EventType
from ingest_contract.hashing import snapshot_hash
from shared.contracts.source_contract import SourceContract
from shared.logging import log_event

LOG = logging.getLogger("connector")
HARD_EXCLUDED = frozenset({"salary", "compensation", "ssn", "national_id"})


@dataclass
class SyncReport:
    ok: bool
    written: int
    skipped: int
    errors: int
    watermark: str | None


@dataclass
class ItemOutcome:
    status: str
    stage: str | None = None
    nbytes: int = 0
    object_type: str = ""
    lag: float = 0.0
    version: str = ""


class Syncer:
    def __init__(
        self,
        connector: SourceConnector,
        contract: SourceContract,
        state: State,
        snapshots: SnapshotStore,
        workers: int = 8,
        publisher: object | None = None,
    ) -> None:
        self.connector = connector
        self.contract = contract
        self.state = state
        self.snapshots = snapshots
        self.workers = workers
        # None only in tests that do not start a queue. The connector process always sets one.
        self.publisher = publisher
        self._logged_extra: set[str] = set()
        self._lock = Lock()

    def run(self) -> SyncReport:
        source_id = self.contract.source_id
        started = time.perf_counter()
        log_event(
            LOG,
            "sync start",
            source_id=source_id,
            watermark=self.state.get_watermark(source_id) or "",
        )
        try:
            items, server_time = self._collect()
        except ConnectorError as exc:
            return self._fail(started, "list", str(exc))
        outcomes = self._map(items)
        written = sum(1 for item in outcomes if item.status == "written")
        skipped = sum(1 for item in outcomes if item.status == "skipped")
        failed = [item for item in outcomes if item.status == "error"]
        for item in outcomes:
            if item.status == "written":
                metrics.WRITTEN.labels(source_id, item.object_type).inc()
                metrics.BYTES.labels(source_id).inc(item.nbytes)
                metrics.OBJECT_LAG.labels(source_id).observe(item.lag)
            elif item.status == "skipped":
                metrics.SKIPPED.labels(source_id).inc()
            elif item.stage:
                metrics.ERRORS.labels(source_id, item.stage).inc()
        watermark = self.state.get_watermark(source_id)
        if failed:
            self.state.mark_error(source_id, f"{len(failed)} object(s) failed")
            ok = False
        else:
            self.state.mark_success(source_id, server_time)
            watermark = server_time
            ok = True
            metrics.LAST_SUCCESS.labels(source_id).set(time.time())
        self._observe_lag(watermark)
        elapsed = time.perf_counter() - started
        metrics.SYNC_DURATION.labels(source_id).observe(elapsed)
        metrics.SYNC_RUNS.labels(source_id, "ok" if ok else "error").inc()
        log_event(
            LOG,
            "sync done",
            source_id=source_id,
            written=written,
            skipped=skipped,
            errors=len(failed),
            duration=round(elapsed, 3),
        )
        return SyncReport(ok, written, skipped, len(failed), watermark)

    def reconcile(self) -> None:
        """Delete snapshots the source no longer lists, and backfill ids the feed missed."""
        source_id = self.contract.source_id
        try:
            payload = self.connector.list_ids()
        except ConnectorError as exc:
            metrics.ERRORS.labels(source_id, "list").inc()
            log_event(
                LOG,
                "reconcile list failed",
                level=logging.ERROR,
                source_id=source_id,
                error=str(exc),
            )
            return
        try:
            snapped = self.snapshots.list_object_ids(source_id)
        except (ClientError, BotoCoreError) as exc:
            metrics.DEPENDENCY.labels(dependency="s3").set(0)
            metrics.ERRORS.labels(source_id, "s3_put").inc()
            log_event(
                LOG,
                "snapshot list failed",
                level=logging.ERROR,
                source_id=source_id,
                error=str(exc),
            )
            return
        metrics.DEPENDENCY.labels(dependency="s3").set(1)
        live: dict[str, tuple[str, str]] = {}
        for kind, rows in payload.get("ids", {}).items():
            for row in rows:
                live[row["id"]] = (kind, str(row["version"]))
        for object_id, row in self.state.list_objects(source_id).items():
            if object_id not in live:
                self._delete(row.object_id, row.s3_key)
        stored = self.state.list_objects(source_id)
        for object_id, (kind, version) in live.items():
            current = stored.get(object_id)
            missing = object_id not in snapped
            if current is None or current.version != version or missing:
                self._resync(kind, object_id)
        try:
            snapped = self.snapshots.list_object_ids(source_id)
        except (ClientError, BotoCoreError) as exc:
            metrics.DEPENDENCY.labels(dependency="s3").set(0)
            metrics.ERRORS.labels(source_id, "s3_put").inc()
            log_event(
                LOG,
                "snapshot list failed",
                level=logging.ERROR,
                source_id=source_id,
                error=str(exc),
            )
            return
        metrics.SOURCE_OBJECTS.labels(source_id).set(len(live))
        metrics.SNAPSHOT_OBJECTS.labels(source_id).set(len(snapped))
        metrics.DRIFT.labels(source_id).set(len(snapped) - len(live))

    def _collect(self) -> tuple[list[dict], str]:
        since = self.state.get_watermark(self.contract.source_id)
        items: list[dict] = []
        token: str | None = None
        server_time = ""
        while True:
            page = self.connector.list_changes(since, token)
            if not server_time:
                server_time = str(page["server_time"])
            items.extend(page["items"])
            token = page.get("next_page_token")
            if not token:
                return items, server_time

    def _map(self, items: list[dict]) -> list[ItemOutcome]:
        if self.workers <= 1 or len(items) <= 1:
            return [self._one(item) for item in items]
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            return list(pool.map(self._one, items))

    def _one(self, item: dict, force: bool = False) -> ItemOutcome:
        if item.get("op") == "delete":
            return self._feed_delete(item)
        source_id = self.contract.source_id
        record = item["record"]
        object_type = item["object_type"]
        object_id = item["id"]
        version = str(record["version"])
        updated_at = str(record["updated_at"])
        refused = _forbidden(record, self.contract)
        if refused:
            log_event(
                LOG,
                "object refused",
                level=logging.ERROR,
                source_id=source_id,
                object_type=object_type,
                fields=",".join(refused),
            )
            return ItemOutcome("error", "contract", object_type=object_type, version=version)
        self._note_extra(record)
        acl = [str(value) for value in record.get("native_acl", [])]
        acl_text = ",".join(sorted(acl))
        existing = self.state.get_object(source_id, object_id)
        if (
            not force
            and existing
            and existing.version == version
            and existing.native_acl == acl_text
        ):
            log_event(
                LOG,
                "snapshot skipped",
                level=logging.DEBUG,
                source_id=source_id,
                object_type=object_type,
                version=version,
            )
            return ItemOutcome("skipped", object_type=object_type, version=version)
        try:
            body, content_type, ext = self._body(item)
        except ConnectorError as exc:
            log_event(LOG, "fetch failed", level=logging.ERROR, source_id=source_id, error=str(exc))
            return ItemOutcome("error", "fetch", object_type=object_type, version=version)
        digest = snapshot_hash(body, acl)
        key = object_key(source_id, object_type, object_id, ext)
        metadata = {
            METADATA_HASH: digest,
            METADATA_VERSION: version,
            METADATA_ACL: acl_text,
            METADATA_SOURCE_UPDATED: updated_at,
            METADATA_SNAPSHOT_AT: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            put = self.snapshots.put(key, body, content_type, metadata)
        except (ClientError, BotoCoreError) as exc:
            metrics.DEPENDENCY.labels(dependency="s3").set(0)
            log_event(
                LOG, "snapshot put failed", level=logging.ERROR, source_id=source_id, error=str(exc)
            )
            return ItemOutcome("error", "s3_put", object_type=object_type, version=version)
        event = ChangeEvent(
            source_id=source_id,
            object_id=object_id,
            event_type=_event_type(existing, version, acl_text),
            version=version,
            content_hash=digest,
            occurred_at=_occurred_at(updated_at),
            s3_pointer=key,
        )
        # Send before the state row. A crash here retries the object and sends again.
        if not self._publish(event):
            return ItemOutcome("error", "enqueue", object_type=object_type, version=version)
        try:
            self.state.upsert_object(
                source_id,
                object_id,
                object_type,
                version,
                digest,
                acl_text,
                key,
                put.version_id,
                updated_at,
            )
        except SQLAlchemyError as exc:
            log_event(
                LOG, "state upsert failed", level=logging.ERROR, source_id=source_id, error=str(exc)
            )
            return ItemOutcome("error", "state", object_type=object_type, version=version)
        if put.skipped:
            return ItemOutcome("skipped", object_type=object_type, version=version)
        log_event(
            LOG,
            "snapshot written",
            source_id=source_id,
            object_type=object_type,
            version=version,
            bytes=len(body),
        )
        return ItemOutcome(
            "written",
            nbytes=len(body),
            object_type=object_type,
            version=version,
            lag=_seconds_since(updated_at),
        )

    def _body(self, item: dict) -> tuple[bytes, str, str]:
        return self.connector.snapshot_body(item)

    def _feed_delete(self, item: dict) -> ItemOutcome:
        """A delete delivered on the change feed. The sweep remains the backstop."""
        source_id = self.contract.source_id
        object_id = str(item["id"])
        object_type = str(item.get("object_type", ""))
        row = self.state.get_object(source_id, object_id)
        if row is None:
            log_event(
                LOG,
                "delete skipped",
                source_id=source_id,
                object_type=object_type,
                object_id=object_id,
            )
            return ItemOutcome("skipped", object_type=object_type)
        self._delete(row.object_id, row.s3_key)
        if self.state.get_object(source_id, object_id) is not None:
            # _delete already counted the failure. Do not count it again.
            return ItemOutcome("error", object_type=object_type)
        return ItemOutcome("deleted", object_type=object_type)

    def _delete(self, object_id: str, key: str) -> None:
        source_id = self.contract.source_id
        try:
            self.snapshots.delete_all_versions(key)
        except (ClientError, BotoCoreError) as exc:
            metrics.ERRORS.labels(source_id, "delete").inc()
            log_event(
                LOG, "delete failed", level=logging.ERROR, source_id=source_id, error=str(exc)
            )
            return
        if self.snapshots.exists(key):
            metrics.DELETE_FAILURES.labels(source_id, "s3").inc()
            log_event(
                LOG,
                "delete verification failed",
                level=logging.ERROR,
                source_id=source_id,
                object_type=key,
            )
            return
        # The row stays until the event is accepted, so a failed send is retried next sweep.
        row = self.state.get_object(source_id, object_id)
        if row is not None and not self._publish(
            ChangeEvent(
                source_id=source_id,
                object_id=object_id,
                event_type=EventType.DELETE,
                version=row.version,
                content_hash=row.content_hash,
                occurred_at=datetime.now(UTC),
            )
        ):
            metrics.ERRORS.labels(source_id, "enqueue").inc()
            return
        self.state.delete_object(source_id, object_id)
        metrics.DELETED.labels(source_id).inc()
        log_event(LOG, "delete verified", source_id=source_id, object_type=key)

    def _resync(self, object_type: str, object_id: str) -> None:
        try:
            fetched = self.connector.fetch(object_type, object_id)
        except ConnectorError as exc:
            metrics.ERRORS.labels(self.contract.source_id, "fetch").inc()
            log_event(
                LOG,
                "resync fetch failed",
                level=logging.ERROR,
                source_id=self.contract.source_id,
                error=str(exc),
            )
            return
        record = dict(fetched.record)
        if object_type == "person":
            record = {**record}
        outcome = self._one(
            {
                "object_type": object_type,
                "id": object_id,
                "record": record,
                "updated_at": record.get("updated_at", ""),
            },
            force=True,
        )
        source_id = self.contract.source_id
        if outcome.status == "written":
            metrics.WRITTEN.labels(source_id, outcome.object_type).inc()
            metrics.BYTES.labels(source_id).inc(outcome.nbytes)
        elif outcome.status == "error" and outcome.stage:
            metrics.ERRORS.labels(source_id, outcome.stage).inc()

    def _note_extra(self, record: dict) -> None:
        allowed = set(self.contract.in_scope_fields)
        extra = sorted(set(record) - allowed)
        with self._lock:
            fresh = [key for key in extra if key not in self._logged_extra]
            self._logged_extra.update(fresh)
        if fresh:
            log_event(
                LOG,
                "fields outside contract",
                source_id=self.contract.source_id,
                fields=",".join(fresh),
            )

    def _fail(self, started: float, stage: str, error: str) -> SyncReport:
        source_id = self.contract.source_id
        metrics.ERRORS.labels(source_id, stage).inc()
        self.state.mark_error(source_id, error)
        elapsed = time.perf_counter() - started
        metrics.SYNC_DURATION.labels(source_id).observe(elapsed)
        metrics.SYNC_RUNS.labels(source_id, "error").inc()
        log_event(
            LOG,
            "sync done",
            level=logging.ERROR,
            source_id=source_id,
            written=0,
            skipped=0,
            errors=1,
            duration=round(elapsed, 3),
            error=error,
        )
        return SyncReport(False, 0, 0, 1, self.state.get_watermark(source_id))

    def _publish(self, event: ChangeEvent) -> bool:
        if self.publisher is None:
            return True
        try:
            self.publisher.publish(event)
        except (ClientError, BotoCoreError, OSError) as exc:
            log_event(
                LOG,
                "enqueue failed",
                level=logging.ERROR,
                source_id=event.source_id,
                object_id=event.object_id,
                event_type=event.event_type.value,
                error=str(exc),
            )
            return False
        metrics.PUBLISHED.labels(event.source_id, event.event_type.value).inc()
        log_event(
            LOG,
            "event published",
            source_id=event.source_id,
            object_id=event.object_id,
            event_type=event.event_type.value,
        )
        return True

    def _observe_lag(self, watermark: str | None) -> None:
        if not watermark:
            return
        try:
            lag = _seconds_since(watermark)
        except ValueError:
            return
        metrics.WATERMARK_LAG.labels(self.contract.source_id).set(lag)


def _event_type(existing: ObjectRow | None, version: str, acl_text: str) -> EventType:
    if existing is None:
        return EventType.CREATE
    if existing.version != version:
        return EventType.UPDATE
    if existing.native_acl != acl_text:
        return EventType.ACL_CHANGE
    return EventType.UPDATE


def _occurred_at(stamp: str) -> datetime:
    if not stamp:
        return datetime.now(UTC)
    moment = datetime.fromisoformat(stamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def _forbidden(record: dict, contract: SourceContract) -> list[str]:
    excluded = {name.lower() for name in contract.excluded_fields} | HARD_EXCLUDED
    return sorted(key for key in record if str(key).lower() in excluded)


def _seconds_since(stamp: str) -> float:
    moment = datetime.fromisoformat(stamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - moment).total_seconds())
