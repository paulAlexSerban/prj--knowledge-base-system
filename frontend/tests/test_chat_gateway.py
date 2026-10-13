"""The gateway call is a span, and the token stays out of it."""

import httpx
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from chat_ui.gateway import Gateway


def test_ask_continues_one_trace_and_hides_the_token() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    previous_provider = trace._TRACER_PROVIDER
    previous_once = trace._TRACER_PROVIDER_SET_ONCE
    trace._TRACER_PROVIDER = provider
    trace._TRACER_PROVIDER_SET_ONCE = True
    try:

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/dev/token":
                return httpx.Response(200, json={"token": "secret-token"})
            assert request.headers["authorization"] == "Bearer secret-token"
            assert request.headers["traceparent"]
            return httpx.Response(
                200,
                json={"request_id": "req-1", "results": [], "route": {"class": "semantic"}},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://gateway")
        body = Gateway("http://gateway", client).ask("employee_eng", "parental leave")
    finally:
        trace._TRACER_PROVIDER = previous_provider
        trace._TRACER_PROVIDER_SET_ONCE = previous_once

    assert body["request_id"] == "req-1"
    finished = exporter.get_finished_spans()
    by_name = {item.name: item for item in finished}
    assert set(by_name) == {"chat.token", "chat.gateway.ask"}
    assert by_name["chat.token"].parent.span_id == by_name["chat.gateway.ask"].context.span_id
    rendered = " ".join(str(item.attributes) for item in finished)
    assert "secret-token" not in rendered
    assert "parental leave" not in rendered
