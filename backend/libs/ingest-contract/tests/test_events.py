"""Change-event payload rules."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ingest_contract.events import ChangeEvent


def test_event_accepts_version() -> None:
    event = ChangeEvent(
        source_id="hr",
        object_id="policy-de",
        event_type="update",
        version="3",
        occurred_at=datetime.now(UTC),
        s3_pointer="raw/hr/policy/policy-de.md",
    )
    assert event.version == "3"
    assert event.message_group_id() == "hr:policy-de"
    assert event.dedup_id() == "hr:policy-de:update:3"
    assert ChangeEvent.from_json(event.to_json()) == event


def test_event_rejects_missing_version_and_hash() -> None:
    with pytest.raises(ValidationError) as raised:
        ChangeEvent(
            source_id="hr",
            object_id="policy-de",
            event_type="update",
            occurred_at=datetime.now(UTC),
        )
    assert "version or content_hash" in str(raised.value)


def test_delete_without_pointer_is_valid() -> None:
    event = ChangeEvent(
        source_id="hr",
        object_id="policy-de",
        event_type="delete",
        version="3",
        occurred_at=datetime.now(UTC),
    )
    assert event.s3_pointer is None


def test_update_without_pointer_is_rejected() -> None:
    with pytest.raises(ValidationError, match="s3_pointer"):
        ChangeEvent(
            source_id="hr",
            object_id="policy-de",
            event_type="update",
            version="3",
            occurred_at=datetime.now(UTC),
        )


def test_acl_change_without_pointer_is_rejected() -> None:
    with pytest.raises(ValidationError, match="s3_pointer"):
        ChangeEvent(
            source_id="hr",
            object_id="policy-de",
            event_type="acl_change",
            content_hash="abc",
            occurred_at=datetime.now(UTC),
        )


def test_dedup_id_prefers_the_content_hash() -> None:
    event = ChangeEvent(
        source_id="hr",
        object_id="policy-de",
        event_type="acl_change",
        version="3",
        content_hash="deadbeef",
        occurred_at=datetime.now(UTC),
        s3_pointer="raw/hr/policy/policy-de.md",
    )
    assert event.dedup_id().endswith(":deadbeef")
