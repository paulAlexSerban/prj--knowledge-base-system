"""The product hook post carries the current span to the webhook receiver."""

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from harness_product.notify import Notifier


def test_notify_sends_traceparent(monkeypatch) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        "shared.telemetry.trace.get_tracer",
        lambda *_args, **_kwargs: provider.get_tracer("kbs"),
    )
    seen: dict[str, str] = {}

    class _Body:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    def fake_open(request: object, timeout: float) -> _Body:
        headers = {key.lower(): value for key, value in request.header_items()}  # type: ignore[attr-defined]
        seen["traceparent"] = headers.get("traceparent", "")
        assert timeout == 2.0
        return _Body()

    monkeypatch.setattr("harness_product.notify.urllib.request.urlopen", fake_open)
    assert Notifier("http://webhook-receiver:8090/webhooks/product").notify("spec-1", "update")
    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    current = finished[0]
    assert current.name == "product.hook"
    assert current.kind == SpanKind.CLIENT
    assert current.attributes["peer.service"] == "webhook-receiver"
    assert "spec-1" not in str(current.attributes)
    parts = seen["traceparent"].split("-")
    assert parts[1] == format(current.context.trace_id, "032x")
    assert parts[2] == format(current.context.span_id, "016x")


def test_unknown_event_does_not_post(monkeypatch) -> None:
    def fail(_request: object, timeout: float) -> object:
        raise AssertionError(timeout)

    monkeypatch.setattr("harness_product.notify.urllib.request.urlopen", fail)
    notifier = Notifier("http://webhook-receiver:8090/webhooks/product")
    assert notifier.notify("spec-1", "rename") is False
