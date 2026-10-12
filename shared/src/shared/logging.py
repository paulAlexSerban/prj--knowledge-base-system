"""JSON logs on stdout.

Loki labels are service, trust_area, level, and source_id. request_id,
principal, chunk_id, decision, trace_id, and span_id stay in the line.
trace_id and span_id are null until a span is current. Retrieved text and
the fields the gateway must not ingest are dropped before the line is written.
"""

import json
import logging
import sys
from typing import Any

from shared.telemetry import current_ids

# Keys Alloy may promote. Anything else stays in the JSON body.
LABEL_KEYS = ("service", "trust_area", "level", "source_id")

# Never written, at any depth. Matched case-insensitively.
DROPPED_KEYS = frozenset(
    {
        "salary",
        "compensation",
        "ssn",
        "national_id",
        "api_key",
        "authorization",
        "openrouter_api_key",
        "aws_secret_access_key",
        "password",
        "retrieved_text",
        "chunk_text",
        "snippet",
        "content",
    }
)


def drop_excluded(value: Any) -> Any:
    """Return a copy with excluded keys removed at every level."""
    if isinstance(value, dict):
        return {
            key: drop_excluded(item)
            for key, item in value.items()
            if str(key).lower() not in DROPPED_KEYS
        }
    if isinstance(value, list):
        return [drop_excluded(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Extra fields live on the record as `kbs`."""

    def __init__(self, service: str, trust_area: str) -> None:
        super().__init__()
        self.service = service
        self.trust_area = trust_area

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "service": self.service,
            "trust_area": self.trust_area,
            "level": record.levelname.lower(),
            "msg": record.getMessage(),
        }
        extra = getattr(record, "kbs", None)
        if isinstance(extra, dict):
            payload.update(drop_excluded(extra))
        # Not labels. Null when no span is current.
        trace_id, span_id = current_ids()
        payload["trace_id"] = trace_id
        payload["span_id"] = span_id
        return json.dumps(drop_excluded(payload), default=str)


def configure(service: str, trust_area: str) -> None:
    """Send the root logger to stdout as JSON. Safe to call once at startup."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service, trust_area))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def log_event(
    logger: logging.Logger,
    message: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Log one event. Excluded keys in `fields` are removed by the formatter."""
    logger.log(level, message, extra={"kbs": fields})


def audit_decision(
    logger: logging.Logger,
    *,
    request_id: str,
    source_id: str,
    principal: str,
    decision: str,
    attributes: list[str],
) -> None:
    """Line demo step 2 asserts on. `decision` is `deny` or `allow`."""
    log_event(
        logger,
        "access decision",
        request_id=request_id,
        source_id=source_id,
        principal=principal,
        decision=decision,
        attributes=attributes,
    )
