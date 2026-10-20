"""Change events carried on the ingestion queues.

The body matches spike 04: source, object, event type, a version or content
hash, when it happened, and an optional pointer at the raw snapshot in S3.
A delete is valid without the pointer, because the connector removes the
object before it sends the event. Every other event must name the key.
"""

import hashlib
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

P0_QUEUE = "kbs-p0.fifo"
P1_QUEUE = "kbs-p1.fifo"
P2_QUEUE = "kbs-p2.fifo"
P0_DLQ = "kbs-p0-dlq.fifo"
P1_DLQ = "kbs-p1-dlq.fifo"
P2_DLQ = "kbs-p2-dlq.fifo"
RAW_BUCKET = "kbs-raw-snapshots"

TIER_QUEUES = {
    "P0": P0_QUEUE,
    "P1": P1_QUEUE,
    "P2": P2_QUEUE,
}
TIER_ORDER = (P0_QUEUE, P1_QUEUE, P2_QUEUE)
DLQ_FOR = {
    P0_QUEUE: P0_DLQ,
    P1_QUEUE: P1_DLQ,
    P2_QUEUE: P2_DLQ,
}
SQS_ID_LIMIT = 128


class EventType(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ACL_CHANGE = "acl_change"


class ChangeEvent(BaseModel):
    """Claim-check event: identity, type, version or hash, and an S3 pointer (except delete)."""
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    event_type: EventType
    version: str | None = None
    content_hash: str | None = None
    occurred_at: datetime
    s3_pointer: str | None = None

    @model_validator(mode="after")
    def require_version_or_hash(self) -> "ChangeEvent":
        """A delete may omit the S3 pointer. Every other event must name the snapshot key."""
        if not self.version and not self.content_hash:
            raise ValueError("version or content_hash is required")
        if self.event_type != EventType.DELETE and not self.s3_pointer:
            raise ValueError("s3_pointer is required unless the event is a delete")
        return self

    def message_group_id(self) -> str:
        """FIFO group. Two updates of one object stay ordered."""
        return fit_sqs_id(f"{self.source_id}:{self.object_id}")

    def dedup_id(self) -> str:
        """FIFO dedup token. The content hash wins, so an ACL-only change is a new send."""
        token = self.content_hash or self.version or ""
        return fit_sqs_id(f"{self.source_id}:{self.object_id}:{self.event_type.value}:{token}")

    def to_json(self) -> str:
        """Queue body. The document itself stays in S3 (claim-check)."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, body: str) -> "ChangeEvent":
        """Parse one queue message. Extra fields are rejected."""
        return cls.model_validate_json(body)


def fit_sqs_id(value: str, limit: int = SQS_ID_LIMIT) -> str:
    """SQS rejects a group or dedup id longer than 128 characters."""
    if len(value) <= limit:
        return value
    digest = hashlib.sha256(value.encode()).hexdigest()[:16]
    keep = limit - len(digest) - 1
    return f"{value[:keep]}-{digest}"
