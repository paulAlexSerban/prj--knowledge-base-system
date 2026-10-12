"""OpenTelemetry bootstrap.

Nothing is exported when OTEL_EXPORTER_OTLP_ENDPOINT is unset, so tests and
offline runs do not talk to Alloy. service.name is the Compose service name,
the same string passed to shared.logging.configure.
"""

import atexit
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

_READY = False
_SAFE = (str, int, float, bool)
# Never attributes. A principal, a query, and retrieved text stay out of Tempo.
_DROPPED = frozenset(
    {
        "principal",
        "query",
        "text",
        "snippet",
        "content",
        "body",
        "salary",
        "compensation",
        "ssn",
        "national_id",
        "authorization",
        "password",
        "api_key",
    }
)


def init_telemetry(service: str) -> None:
    """Install a batch exporter once. A missing endpoint leaves the no-op tracer."""
    global _READY
    if _READY:
        return
    _READY = True
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return
    provider = TracerProvider(
        resource=Resource.create({"service.name": service, "service.namespace": "kbs"})
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    atexit.register(provider.shutdown)


@contextmanager
def span(
    name: str,
    *,
    context: Any = None,
    **attributes: Any,
) -> Iterator[Span]:
    """Current span. Attributes are the safe types only, and secrets are dropped."""
    kwargs: dict[str, Any] = {}
    if context is not None:
        kwargs["context"] = context
    tracer = trace.get_tracer("kbs")
    with tracer.start_as_current_span(name, **kwargs) as current:
        for key, value in attributes.items():
            if key in _DROPPED or not isinstance(value, _SAFE):
                continue
            current.set_attribute(key, value)
        try:
            yield current
        except Exception as exc:
            current.set_attribute("error.type", type(exc).__name__)
            current.set_status(Status(StatusCode.ERROR))
            raise


def current_ids() -> tuple[str | None, str | None]:
    """Hex trace id and span id, or nulls when no span is current."""
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None, None
    return format(span_context.trace_id, "032x"), format(span_context.span_id, "016x")


def trace_headers() -> dict[str, str]:
    """W3C traceparent for an outbound HTTP call. Empty when no span is current."""
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier


def sqs_message_attributes() -> dict[str, dict[str, str]]:
    """Message attributes that carry traceparent. Empty when there is no span."""
    traceparent = trace_headers().get("traceparent")
    if not traceparent:
        return {}
    return {"traceparent": {"DataType": "String", "StringValue": traceparent}}


def forwarded_traceparent(message: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Copy an inbound traceparent onto another send. Empty when the message has none."""
    attributes = message.get("MessageAttributes") or {}
    raw = attributes.get("traceparent") if isinstance(attributes, dict) else None
    value = raw.get("StringValue") if isinstance(raw, dict) else None
    if not isinstance(value, str) or not value:
        return {}
    return {"traceparent": {"DataType": "String", "StringValue": value}}


def sqs_parent(message: Mapping[str, Any]) -> Any:
    """Context from a received message. A missing header starts a new trace."""
    attributes = message.get("MessageAttributes") or {}
    raw = attributes.get("traceparent") if isinstance(attributes, dict) else None
    value = raw.get("StringValue") if isinstance(raw, dict) else None
    carrier = {"traceparent": value} if isinstance(value, str) and value else {}
    return extract(carrier)
