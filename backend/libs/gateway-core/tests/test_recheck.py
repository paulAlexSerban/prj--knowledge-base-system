"""The ACL re-check carries the current span to the HR harness."""

import json

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from gateway_core.recheck import HttpAclRecheck


def test_recheck_sends_traceparent(monkeypatch) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        "shared.telemetry.trace.get_tracer",
        lambda *_args, **_kwargs: provider.get_tracer("kbs"),
    )
    seen: dict[str, str] = {}

    class _Body:
        status = 200

        def read(self) -> bytes:
            return json.dumps({"native_acl": ["hr_employee"]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    def fake_open(request: object, timeout: float) -> _Body:
        headers = {key.lower(): value for key, value in request.header_items()}  # type: ignore[attr-defined]
        seen["traceparent"] = headers.get("traceparent", "")
        assert timeout == 2.0
        return _Body()

    monkeypatch.setattr("gateway_core.recheck.urllib.request.urlopen", fake_open)
    values = HttpAclRecheck("http://harness-hr:8080").current_acl("hr", "policy", "pol-1")
    assert values == ["hr_employee"]
    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    current = finished[0]
    assert current.name == "acl.recheck.http"
    assert current.kind == SpanKind.CLIENT
    assert current.attributes["peer.service"] == "harness-hr"
    assert current.attributes["http.status_code"] == 200
    assert "pol-1" not in current.name
    parts = seen["traceparent"].split("-")
    assert parts[1] == format(current.context.trace_id, "032x")
    assert parts[2] == format(current.context.span_id, "016x")
