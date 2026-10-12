"""A missing endpoint stays quiet. A current span is copied onto the log line."""

import json
import logging

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from shared.logging import configure, log_event
from shared.telemetry import init_telemetry, span


def test_unset_endpoint_does_not_install_an_exporter(monkeypatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    init_telemetry("gateway-api")
    init_telemetry("gateway-api")


def test_log_line_copies_the_current_span_and_drops_the_query(capsys, monkeypatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    root = logging.getLogger()
    previous_handlers = root.handlers[:]
    previous_level = root.level
    try:
        configure("gateway-api", "access")
        with span("search", tool="search", surface="rest", query="parental leave"):
            log_event(logging.getLogger("test-span"), "access decision", decision="deny")
    finally:
        root.handlers[:] = previous_handlers
        root.setLevel(previous_level)
    raw = capsys.readouterr().out
    line = json.loads(raw.strip().splitlines()[-1])
    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    assert finished[0].attributes["tool"] == "search"
    assert "query" not in (finished[0].attributes or {})
    assert line["trace_id"] == format(finished[0].context.trace_id, "032x")
    assert line["span_id"] == format(finished[0].context.span_id, "016x")
    assert "parental leave" not in raw
