"""Pointer the product harness posts. This is not a change event.

The receiver validates it and puts it on `kbs-product-hooks`. The product
connector fetches the spec, then emits the change event on the tier queue.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HOOK_QUEUE = "kbs-product-hooks.fifo"
HOOK_DLQ = "kbs-product-hooks-dlq.fifo"


class HookNotice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: Literal["product"]
    object_id: str = Field(min_length=1)
    event_type: Literal["create", "update", "delete"]

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, body: str) -> "HookNotice":
        return cls.model_validate_json(body)
