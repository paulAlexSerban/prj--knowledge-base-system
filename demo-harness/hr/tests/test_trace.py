"""The HR harness continues a caller trace. A bad header starts a new one."""

from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from harness_hr.app import create_app
from harness_hr.store import Store
from shared.telemetry import current_ids, span

TRACE = "0af7651916cd43dd8448eb211c80319c"
PARENT = "b7ad6b7169203331"


def _install(monkeypatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        "shared.telemetry.trace.get_tracer",
        lambda *_args, **_kwargs: provider.get_tracer("kbs"),
    )
    return exporter


def _client(tmp_path) -> TestClient:
    store = Store(tmp_path / "hr.db", tmp_path / "policies")
    return TestClient(create_app(store, seed_if_empty=False))


def test_harness_continues_traceparent(monkeypatch, tmp_path) -> None:
    exporter = _install(monkeypatch)
    response = _client(tmp_path).get(
        "/discover",
        headers={"traceparent": f"00-{TRACE}-{PARENT}-01"},
    )
    assert response.status_code == 200
    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    current = finished[0]
    assert current.name == "GET /discover"
    assert current.kind == SpanKind.SERVER
    assert format(current.context.trace_id, "032x") == TRACE
    assert format(current.parent.span_id, "016x") == PARENT


def test_malformed_traceparent_starts_a_new_trace(monkeypatch, tmp_path) -> None:
    exporter = _install(monkeypatch)
    client = _client(tmp_path)
    with span("caller"):
        caller = current_ids()[0]
    response = client.get("/discover", headers={"traceparent": "not-a-traceparent"})
    assert response.status_code == 200
    finished = [item for item in exporter.get_finished_spans() if item.name == "GET /discover"]
    assert len(finished) == 1
    assert format(finished[0].context.trace_id, "032x") != caller
    assert finished[0].parent is None or not finished[0].parent.is_valid
