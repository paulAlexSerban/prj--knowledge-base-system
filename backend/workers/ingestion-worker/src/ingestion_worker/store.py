"""What the worker does with one event.

Phase 1 ships an in-memory store for tests and a logging store for the
process. The knowledge store replaces the logging store once Postgres and
Neo4j writes exist. Both honor the same three calls.
"""

import logging
from typing import Protocol

from ingest_contract.events import ChangeEvent, EventType
from shared.logging import log_event

LOG = logging.getLogger("ingestion-worker")


class Store(Protocol):
    def upsert_object(self, event: ChangeEvent) -> None: ...

    def apply_acl(self, event: ChangeEvent) -> None: ...

    def delete_object(self, event: ChangeEvent) -> None: ...


class MemoryStore:
    """Idempotent on the event dedup id. A replay does not append twice."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], ChangeEvent] = {}
        self.applied: list[ChangeEvent] = []
        self._seen: set[str] = set()

    def upsert_object(self, event: ChangeEvent) -> None:
        if not self._remember(event):
            return
        self.objects[(event.source_id, event.object_id)] = event

    def apply_acl(self, event: ChangeEvent) -> None:
        if not self._remember(event):
            return
        self.objects[(event.source_id, event.object_id)] = event

    def delete_object(self, event: ChangeEvent) -> None:
        if not self._remember(event):
            return
        self.objects.pop((event.source_id, event.object_id), None)

    def _remember(self, event: ChangeEvent) -> bool:
        token = event.dedup_id()
        if token in self._seen:
            return False
        self._seen.add(token)
        self.applied.append(event)
        return True


class LoggingStore:
    """No database yet. The line is what the manual gate reads."""

    def upsert_object(self, event: ChangeEvent) -> None:
        _log(event)

    def apply_acl(self, event: ChangeEvent) -> None:
        _log(event)

    def delete_object(self, event: ChangeEvent) -> None:
        _log(event)


def dispatch(store: Store, event: ChangeEvent) -> None:
    if event.event_type == EventType.DELETE:
        store.delete_object(event)
    elif event.event_type == EventType.ACL_CHANGE:
        store.apply_acl(event)
    else:
        store.upsert_object(event)


def _log(event: ChangeEvent) -> None:
    log_event(
        LOG,
        "event handled",
        source_id=event.source_id,
        object_id=event.object_id,
        event_type=event.event_type.value,
        version=event.version or "",
    )
