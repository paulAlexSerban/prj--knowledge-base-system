"""Dispatch and idempotency of the in-memory store. No queue."""

from datetime import UTC, datetime

import pytest

from ingest_contract.events import ChangeEvent
from ingestion_worker.store import MemoryStore, dispatch
from ingestion_worker.worker import main


def _event(event_type: str = "create", version: str = "1", object_id: str = "emp-1") -> ChangeEvent:
    pointer = None if event_type == "delete" else "raw/hr/person/emp-1.json"
    return ChangeEvent(
        source_id="hr",
        object_id=object_id,
        event_type=event_type,
        version=version,
        occurred_at=datetime.now(UTC),
        s3_pointer=pointer,
    )


def test_dispatch_and_replay_do_not_duplicate() -> None:
    store = MemoryStore()
    created = _event("create")
    dispatch(store, created)
    dispatch(store, created)
    assert len(store.applied) == 1
    assert len(store.objects) == 1
    dispatch(store, _event("update", version="2"))
    assert [event.event_type.value for event in store.applied] == ["create", "update"]
    dispatch(store, _event("acl_change", version="2"))
    assert store.objects[("hr", "emp-1")].event_type.value == "acl_change"
    dispatch(store, _event("delete", version="2"))
    dispatch(store, _event("delete", version="2"))
    assert ("hr", "emp-1") not in store.objects
    assert [event.event_type.value for event in store.applied].count("delete") == 1


def test_startup_refuses_an_invalid_registry(monkeypatch, tmp_path) -> None:
    (tmp_path / "bad.yaml").write_text("source_id: hr\n")
    monkeypatch.setenv("CONTRACTS_DIR", str(tmp_path))
    with pytest.raises(SystemExit, match="refusing to start"):
        main()
