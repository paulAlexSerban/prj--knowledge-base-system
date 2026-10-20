"""Pointer the product harness posts. This is not a change event.

The receiver validates it and puts it on `kbs-product-hooks`. The product
connector fetches the spec, then emits the change event on the tier queue.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HOOK_QUEUE = "kbs-product-hooks.fifo"
HOOK_DLQ = "kbs-product-hooks-dlq.fifo"


class HookNotice(BaseModel):
    """Pointer only. The product connector fetches the spec, then emits a ChangeEvent."""
    model_config = ConfigDict(extra="forbid")

    source_id: Literal["product"]
    object_id: str = Field(min_length=1)
    event_type: Literal["create", "update", "delete"]

    def to_json(self) -> str:
        """Hook-queue body. The product connector fetches the spec after this."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, body: str) -> "HookNotice":
        """Parse a pointer. A bad body stays on the queue and lands in the DLQ."""
        return cls.model_validate_json(body)
