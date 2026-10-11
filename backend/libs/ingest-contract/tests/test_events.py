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
    )
    assert event.version == "3"


def test_event_rejects_missing_version_and_hash() -> None:
    with pytest.raises(ValidationError) as raised:
        ChangeEvent(
            source_id="hr",
            object_id="policy-de",
            event_type="update",
            occurred_at=datetime.now(UTC),
        )
    assert "version or content_hash" in str(raised.value)
