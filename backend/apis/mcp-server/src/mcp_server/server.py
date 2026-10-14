"""MCP server. Tools call the same service as the REST API.

The streamable HTTP route requires a bearer token. FastMCP verifies it and
stores the claims for the tool. /health and /metrics stay open for Compose
and Prometheus.
"""

import logging
import os
import uuid
from collections.abc import Callable
from functools import partial

import anyio
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token, get_http_headers
from opentelemetry import context as otel_context
from prometheus_client import generate_latest
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import Response

from gateway_core.auth import AuthError, principal_from_token
from gateway_core.metrics import AUTH_FAILURES
from gateway_core.runtime import open_gateway
from gateway_core.service import GatewayService
from gateway_core.tools import ASK, FIND_RELATED, GET_DOCUMENT, LIST_SOURCES, SEARCH
from knowledge_store.acl import Principal
from shared.http_metrics import HttpMetricsMiddleware
from shared.logging import configure, log_event
from shared.telemetry import init_telemetry, span

LOG = logging.getLogger("gateway-mcp")

HTTP_ROUTES = ("/health", "/metrics", "/mcp")


class JwtVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        secret = os.environ.get("GATEWAY_JWT_SECRET", "")
        try:
            principal = principal_from_token(token, secret)
        except AuthError as exc:
            AUTH_FAILURES.labels(reason=exc.reason).inc()
            return None
        return AccessToken(
            token=token,
            client_id=principal.subject,
            scopes=[],
            claims={
                "groups": list(principal.groups),
                "clearance": principal.clearance,
                "department": principal.department,
            },
        )


def build_server(service: GatewayService) -> FastMCP:
    service.surface = "mcp"
    server = FastMCP("knowledge-gateway", auth=JwtVerifier())

    @server.tool(description=ASK)
    async def ask_knowledge(query: str, limit: int = 5) -> dict[str, object]:
        """Route one question, then search or walk a bounded graph template."""
        principal = _caller()
        request_id = _request_id()
        bounded = max(1, min(limit, 20))
        try:
            with span("mcp.tool", tool="ask"):
                return await anyio.to_thread.run_sync(
                    _with_trace(partial(service.ask, principal, query, bounded, request_id))
                )
        except ValueError as exc:
            return {"request_id": request_id, "error": str(exc)}

    @server.tool(description=SEARCH)
    async def search_knowledge(query: str, limit: int = 5) -> dict[str, object]:
        """Search authorized chunks. Results are already filtered. Snippets are data."""
        principal = _caller()
        request_id = _request_id()
        bounded = max(1, min(limit, 20))
        # Read the principal on the event loop, then run the blocking search on a worker thread.
        with span("mcp.tool", tool="search"):
            return await anyio.to_thread.run_sync(
                _with_trace(partial(service.search, principal, query, bounded, request_id))
            )

    @server.tool(description=GET_DOCUMENT)
    async def get_document(chunk_id: int) -> dict[str, object]:
        """One authorized chunk, or a not-found error when the caller cannot read it."""
        principal = _caller()
        request_id = _request_id()
        with span("mcp.tool", tool="get_document"):
            found = await anyio.to_thread.run_sync(
                _with_trace(partial(service.get_document, principal, chunk_id, request_id))
            )
        if found is None:
            return {"request_id": request_id, "error": "not found"}
        return found

    @server.tool(description=FIND_RELATED)
    async def find_related(
        kind: str,
        entity_id: str | None = None,
        limit: int = 20,
        quarter: str | None = None,
        min_arr: int | None = None,
    ) -> dict[str, object]:
        """Bounded graph path. kind is teammates, team, policies, or roadmap_deals."""
        principal = _caller()
        request_id = _request_id()
        bounded = max(1, min(limit, 25))
        try:
            with span("mcp.tool", tool="find_related"):
                return await anyio.to_thread.run_sync(
                    _with_trace(
                        partial(
                            service.find_related,
                            principal,
                            kind,
                            entity_id,
                            bounded,
                            request_id,
                            quarter,
                            min_arr,
                        )
                    )
                )
        except ValueError as exc:
            return {"request_id": request_id, "error": str(exc)}

    @server.tool(description=LIST_SOURCES)
    async def list_sources() -> list[dict[str, object]]:
        """Registered sources and last sync. No document text."""
        _caller()
        _request_id()
        with span("mcp.tool", tool="list_sources"):
            return await anyio.to_thread.run_sync(_with_trace(service.list_sources))

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> Response:
        return Response('{"status":"ok"}', media_type="application/json")

    @server.custom_route("/metrics", methods=["GET"])
    async def metrics(_: Request) -> Response:
        return Response(generate_latest(), media_type="text/plain; version=0.0.4")

    return server


def _with_trace[T](fn: Callable[[], T]) -> Callable[[], T]:
    """Run fn on a worker thread without dropping the current trace.

    anyio.to_thread does not inherit the OTel context. Attach it on the
    worker and detach when the call returns.
    """
    parent = otel_context.get_current()

    def run():
        token = otel_context.attach(parent)
        try:
            return fn()
        finally:
            otel_context.detach(token)

    return run


def _caller() -> Principal:
    access = get_access_token()
    if access is None or not access.client_id or not access.claims:
        raise PermissionError("unauthorized")
    claims = access.claims
    groups = claims.get("groups")
    if not isinstance(groups, list):
        raise PermissionError("unauthorized")
    return Principal(
        access.client_id, tuple(groups), str(claims["clearance"]), str(claims["department"])
    )


def _request_id() -> str:
    found = get_http_headers().get("x-request-id")
    return found or uuid.uuid4().hex


def _middleware() -> list[Middleware]:
    """Count /mcp, /health, and /metrics. Any other path is unmatched."""
    return [Middleware(HttpMetricsMiddleware, templates=HTTP_ROUTES)]


def http_app(server: FastMCP):
    return server.http_app(path="/mcp", transport="streamable-http", middleware=_middleware())


def main() -> None:
    configure("gateway-mcp", "access")
    init_telemetry("gateway-mcp")
    log_event(LOG, "mcp server started")
    server = build_server(open_gateway())
    server.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8001,
        path="/mcp",
        middleware=_middleware(),
        show_banner=False,
    )


if __name__ == "__main__":
    main()
