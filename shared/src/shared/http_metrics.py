"""HTTP rate, error, and duration.

`route` is one of the templates the process passes in, or `unmatched`.
The raw path is never a label, so a scan cannot create a series per URL.
`job` from the scrape already names the process, so it is not a label here.
"""

import time
from collections.abc import Awaitable, Callable, Iterable

from opentelemetry.propagate import extract
from opentelemetry.trace import Status, StatusCode
from prometheus_client import Counter, Histogram

from shared.telemetry import span

BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5)
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})

REQUESTS = Counter(
    "kbs_http_requests_total",
    "HTTP requests finished",
    ["route", "method", "status"],
)
DURATION = Histogram(
    "kbs_http_request_seconds",
    "HTTP request duration",
    ["route", "method"],
    buckets=BUCKETS,
)

Scope = dict
Message = dict
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def route_label(path: str, templates: Iterable[str]) -> str:
    """Return the matching template, or `unmatched` when nothing matches."""
    normalized = _normalize(path)
    for template in templates:
        if _matches(normalized, _normalize(template)):
            return _normalize(template)
    return "unmatched"


def _normalize(path: str) -> str:
    if not path or path == "/":
        return "/"
    return "/" + path.strip("/")


def _matches(path: str, template: str) -> bool:
    path_parts = _parts(path)
    template_parts = _parts(template)
    if len(path_parts) != len(template_parts):
        return False
    for got, want in zip(path_parts, template_parts, strict=True):
        if want.startswith("{") and want.endswith("}") and len(want) > 2:
            if not got:
                return False
            continue
        if got != want:
            return False
    return True


def _parts(path: str) -> list[str]:
    if path == "/":
        return []
    return path.strip("/").split("/")


def _record(route: str, method: str, status: int, started: float) -> None:
    DURATION.labels(route=route, method=method).observe(time.perf_counter() - started)
    REQUESTS.labels(route=route, method=method, status=str(status)).inc()


def _carrier(scope: Scope) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, value in scope.get("headers") or []:
        if isinstance(key, bytes) and isinstance(value, bytes):
            found[key.decode("latin1")] = value.decode("latin1")
    return found


class HttpMetricsMiddleware:
    """ASGI middleware. Starlette and FastAPI accept it via add_middleware."""

    def __init__(self, app: ASGIApp, templates: tuple[str, ...] = ()) -> None:
        self.app = app
        self.templates = templates

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method", "GET")).upper()
        if method not in METHODS:
            method = "OTHER"
        route = route_label(str(scope.get("path", "")), self.templates)
        status = 500
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                status = int(message.get("status", 500))
            await send(message)

        # Health checks and scrapes are not traces. They would fill Tempo.
        if route in {"/health", "/metrics"}:
            try:
                await self.app(scope, receive, send_wrapper)
            finally:
                _record(route, method, status, started)
            return

        parent = extract(_carrier(scope))
        try:
            with span(f"{method} {route}", context=parent, method=method, route=route) as current:
                try:
                    await self.app(scope, receive, send_wrapper)
                finally:
                    current.set_attribute("http.status_code", status)
                    if status >= 500:
                        current.set_status(Status(StatusCode.ERROR))
        finally:
            _record(route, method, status, started)
