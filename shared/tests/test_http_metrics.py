"""Unknown paths collapse to one label. The raw URL is not a series."""

import asyncio

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY

from shared.http_metrics import REQUESTS, HttpMetricsMiddleware, route_label
from shared.telemetry import FASTAPI_TELEMETRY, operational_path

TEMPLATES = ("/health", "/v1/search", "/v1/documents/{chunk_id}")


def test_known_template_wins_and_unknown_path_is_unmatched() -> None:
    assert route_label("/v1/search", TEMPLATES) == "/v1/search"
    assert route_label("/v1/documents/42", TEMPLATES) == "/v1/documents/{chunk_id}"
    assert route_label("/v1/documents/42/", TEMPLATES) == "/v1/documents/{chunk_id}"
    raw = "/v1/people/ada-lovelace"
    assert route_label(raw, TEMPLATES) == "unmatched"
    assert route_label("/v1/documents/42/extra", TEMPLATES) == "unmatched"
    assert route_label(raw, TEMPLATES) != raw


def test_middleware_does_not_label_the_raw_path() -> None:
    raw = "/v1/people/ada-lovelace"

    async def app(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    wrapped = HttpMetricsMiddleware(app, templates=TEMPLATES)
    before = REQUESTS.labels(route="unmatched", method="GET", status="404")._value.get()

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message: dict) -> None:
        return None

    asyncio.run(
        wrapped(
            {"type": "http", "path": raw, "method": "GET"},
            receive,
            send,
        )
    )

    after = REQUESTS.labels(route="unmatched", method="GET", status="404")._value.get()
    assert after == before + 1
    routes = set()
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name == "kbs_http_requests_total":
                routes.add(sample.labels.get("route"))
    assert raw not in routes
    assert "unmatched" in routes


def test_server_span_continues_traceparent_and_drops_the_raw_path(monkeypatch) -> None:
    raw = "/v1/people/ada-lovelace"
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        "shared.telemetry.trace.get_tracer",
        lambda *_args, **_kwargs: provider.get_tracer("kbs"),
    )

    async def app(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    wrapped = HttpMetricsMiddleware(app, templates=TEMPLATES)

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message: dict) -> None:
        return None

    asyncio.run(
        wrapped(
            {
                "type": "http",
                "path": raw,
                "method": "GET",
                "headers": [
                    (
                        b"traceparent",
                        b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
                    )
                ],
            },
            receive,
            send,
        )
    )
    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    current = finished[0]
    assert current.attributes["route"] == "unmatched"
    assert current.attributes["method"] == "GET"
    assert format(current.context.trace_id, "032x") == "0af7651916cd43dd8448eb211c80319c"
    assert raw not in current.name
    assert "ada-lovelace" not in str(current.attributes)


def test_malformed_traceparent_starts_a_new_trace(monkeypatch) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        "shared.telemetry.trace.get_tracer",
        lambda *_args, **_kwargs: provider.get_tracer("kbs"),
    )

    async def app(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    wrapped = HttpMetricsMiddleware(app, templates=("/changes",))

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message: dict) -> None:
        return None

    asyncio.run(
        wrapped(
            {
                "type": "http",
                "path": "/changes",
                "method": "GET",
                "headers": [(b"traceparent", b"not-a-traceparent")],
            },
            receive,
            send,
        )
    )
    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    assert finished[0].name == "GET /changes"
    assert finished[0].parent is None or not finished[0].parent.is_valid
    assert format(finished[0].context.trace_id, "032x") != "notatraceparent"


def test_health_is_counted_and_not_traced(monkeypatch) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        "shared.telemetry.trace.get_tracer",
        lambda *_args, **_kwargs: provider.get_tracer("kbs"),
    )

    async def app(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    wrapped = HttpMetricsMiddleware(app, templates=TEMPLATES)
    before = REQUESTS.labels(route="/health", method="GET", status="200")._value.get()

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message: dict) -> None:
        return None

    asyncio.run(
        wrapped(
            {"type": "http", "path": "/health", "method": "GET", "headers": []},
            receive,
            send,
        )
    )
    after = REQUESTS.labels(route="/health", method="GET", status="200")._value.get()
    assert after == before + 1
    assert exporter.get_finished_spans() == ()


def test_operational_paths_are_probes_and_feature_paths_are_not() -> None:
    for path in (
        "/health",
        "/health/",
        "/metrics",
        "/metrics/",
        "/metrics/{path}",
        "/-/healthy",
        "/-/ready",
        "/_stcore/health",
        "/ready",
        "/livez",
    ):
        assert operational_path(path), path
    for path in (
        "/v1/search",
        "/changes",
        "/ids",
        "/discover",
        "/webhooks/product",
        "/v1/graph/related",
        "/health-policy",
        "/live-deals",
    ):
        assert not operational_path(path), path
    exclude = FASTAPI_TELEMETRY["exclude"]
    assert exclude({"type": "http", "path": "/metrics/foo"})
    assert not exclude({"type": "http", "path": "/changes"})
    assert not exclude({"type": "lifespan", "path": "/health"})


def test_metrics_subpath_is_counted_and_not_traced(monkeypatch) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        "shared.telemetry.trace.get_tracer",
        lambda *_args, **_kwargs: provider.get_tracer("kbs"),
    )

    async def app(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    wrapped = HttpMetricsMiddleware(app, templates=("/health", "/metrics", "/changes"))
    before = REQUESTS.labels(route="unmatched", method="GET", status="200")._value.get()

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message: dict) -> None:
        return None

    asyncio.run(
        wrapped(
            {"type": "http", "path": "/metrics/foo", "method": "GET", "headers": []},
            receive,
            send,
        )
    )
    after = REQUESTS.labels(route="unmatched", method="GET", status="200")._value.get()
    assert after == before + 1
    assert exporter.get_finished_spans() == ()


def test_changes_is_traced(monkeypatch) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        "shared.telemetry.trace.get_tracer",
        lambda *_args, **_kwargs: provider.get_tracer("kbs"),
    )

    async def app(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    wrapped = HttpMetricsMiddleware(app, templates=("/health", "/metrics", "/changes"))

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message: dict) -> None:
        return None

    asyncio.run(
        wrapped(
            {"type": "http", "path": "/changes", "method": "GET", "headers": []},
            receive,
            send,
        )
    )
    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    assert finished[0].name == "GET /changes"
