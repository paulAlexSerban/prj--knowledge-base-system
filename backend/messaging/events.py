"""Change events carried on the ingestion queues.

The body matches spike 04: source, object, event type, a version or content
hash, when it happened, and an optional pointer at the raw snapshot in S3.
"""

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


class EventType(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ACL_CHANGE = "acl_change"


class ChangeEvent(BaseModel):
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
        if not self.version and not self.content_hash:
            raise ValueError("version or content_hash is required")
        return self
