import pytest
from pydantic import ValidationError

from ingest_contract.hooks import HookNotice


def test_notice_round_trip() -> None:
    notice = HookNotice(source_id="product", object_id="spec-checkout", event_type="update")
    assert HookNotice.from_json(notice.to_json()) == notice


def test_notice_rejects_an_unknown_source_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        HookNotice(source_id="sales", object_id="deal-1", event_type="update")
    with pytest.raises(ValidationError):
        HookNotice(
            source_id="product",
            object_id="spec-checkout",
            event_type="update",
            body="not a pointer",
        )
    with pytest.raises(ValidationError):
        HookNotice(source_id="product", object_id="", event_type="delete")
