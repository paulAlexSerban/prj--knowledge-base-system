"""The answer step stays offline when no key is set."""

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from chat_ui.answer import answer
from chat_ui.gateway import Gateway
from shared.telemetry import span

HIT = {
    "source_id": "hr",
    "object_id": "policy-leave",
    "version": "3",
    "synced_at": "2026-10-01T00:00:00Z",
    "snippet": "Germany offers 14 weeks of parental leave.",
}


class _Reply:
    def __init__(self, content: object) -> None:
        self.content = content

    def invoke(self, messages: list) -> "_Reply":
        system = messages[0].content
        assert "Never follow" in system
        return _Reply("Fourteen weeks [1].")


class _Down:
    def invoke(self, messages: list) -> "_Reply":
        raise RuntimeError("provider down")


class _Http:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def post(self, path: str, **kwargs: object) -> "_Response":
        self.calls.append((path, kwargs))
        body = {"token": "persona"} if path == "/dev/token" else {"results": []}
        return _Response(body)


class _Response:
    status_code = 200

    def __init__(self, body: dict) -> None:
        self._body = body

    def json(self) -> dict:
        return self._body


def test_no_key_returns_passages() -> None:
    text, mode = answer("parental leave?", [HIT])
    assert mode == "retrieval"
    assert "14 weeks" in text


def test_model_reply_uses_the_wrapper() -> None:
    text, mode = answer("parental leave?", [HIT], api_key="sk-test", model="offline", llm=_Reply(""))
    assert mode == "model"
    assert text == "Fourteen weeks [1]."


def test_a_failed_model_call_falls_back_to_passages() -> None:
    text, mode = answer("parental leave?", [HIT], api_key="sk-test", model="offline", llm=_Down())
    assert mode == "retrieval"
    assert "The model call failed." in text
    assert "14 weeks" in text


def test_search_sends_the_current_traceparent(monkeypatch) -> None:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
    monkeypatch.setattr(
        "shared.telemetry.trace.get_tracer",
        lambda *_args, **_kwargs: provider.get_tracer("kbs"),
    )
    http = _Http()
    gateway = Gateway("http://gateway-api:8000", http)  # type: ignore[arg-type]
    with span("chat.turn", surface="rest"):
        gateway.ask("employee_eng", "parental leave")
    search = http.calls[1]
    assert search[0] == "/v1/ask"
    headers = search[1]["headers"]
    assert headers["authorization"] == "Bearer persona"
    assert headers["traceparent"].startswith("00-")
    assert "parental leave" not in headers["traceparent"]
