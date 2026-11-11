"""A harness GET carries the current span and names the other process."""

import httpx
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from connector_core.http import HarnessClient


def test_get_sends_traceparent_and_names_the_harness(monkeypatch) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        "shared.telemetry.trace.get_tracer",
        lambda *_args, **_kwargs: provider.get_tracer("kbs"),
    )
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["traceparent"] = request.headers.get("traceparent", "")
        return httpx.Response(200, json={"ok": True})

    client = HarnessClient(
        httpx.Client(transport=httpx.MockTransport(handler), base_url="http://harness-sales:8080"),
        backoff=0,
    )
    assert client.get("/discover").json() == {"ok": True}
    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    current = finished[0]
    assert current.name == "harness.get"
    assert current.kind == SpanKind.CLIENT
    assert current.attributes["peer.service"] == "harness-sales"
    parts = seen["traceparent"].split("-")
    assert parts[1] == format(current.context.trace_id, "032x")
    assert parts[2] == format(current.context.span_id, "016x")
