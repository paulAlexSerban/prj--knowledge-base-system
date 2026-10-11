"""Dispatch one change event to the store the consumer was given.

The process passes a KnowledgeStore. MemoryStore is the double the queue
tests use, so those tests do not need Postgres or Neo4j. It dedupes on the
event id. The real store does not: it dedupes on the content hash and the
chunk key, and it writes Postgres before Neo4j.
"""

from typing import Protocol

from ingest_contract.events import ChangeEvent, EventType


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


def dispatch(store: Store, event: ChangeEvent) -> None:
    if event.event_type == EventType.DELETE:
        store.delete_object(event)
    elif event.event_type == EventType.ACL_CHANGE:
        store.apply_acl(event)
    else:
        store.upsert_object(event)
