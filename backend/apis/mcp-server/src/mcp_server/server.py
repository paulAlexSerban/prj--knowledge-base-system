"""MCP server. Tools call the same service as the REST API.

The streamable HTTP route requires a bearer token. The SDK verifies it and
stores the claims for the tool. /health and /metrics stay open for Compose
and Prometheus.
"""

import logging
import os
import uuid
from functools import partial

import anyio
from prometheus_client import generate_latest
from starlette.requests import Request
from starlette.responses import Response

from gateway_core.auth import AuthError, principal_from_token
from gateway_core.metrics import AUTH_FAILURES
from gateway_core.runtime import open_gateway
from gateway_core.service import GatewayService
from gateway_core.tools import FIND_RELATED, GET_DOCUMENT, LIST_SOURCES, SEARCH
from knowledge_store.acl import Principal
from shared.http_metrics import HttpMetricsMiddleware
from shared.logging import configure, log_event

LOG = logging.getLogger("gateway-mcp")

HTTP_ROUTES = ("/health", "/metrics", "/mcp")

try:
    from mcp.server.auth.middleware.auth_context import get_access_token
    from mcp.server.auth.provider import AccessToken, TokenVerifier
    from mcp.server.auth.settings import AuthSettings
    from mcp.server.mcpserver import Context, MCPServer
    from mcp.server.transport_security import TransportSecuritySettings
except ImportError as exc:  # pragma: no cover - exercised only if the SDK moves again
    raise SystemExit(
        "mcp.server.mcpserver.MCPServer is required; the FastMCP name was removed in mcp 2"
    ) from exc


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
            subject=principal.subject,
            claims={
                "groups": list(principal.groups),
                "clearance": principal.clearance,
                "department": principal.department,
            },
        )


def build_server(service: GatewayService) -> MCPServer:
    service.surface = "mcp"
    server = MCPServer(
        "knowledge-gateway",
        token_verifier=JwtVerifier(),
        auth=_auth_settings(),
    )

    @server.tool(description=SEARCH)
    async def search_knowledge(
        query: str, limit: int = 5, ctx: Context | None = None
    ) -> dict[str, object]:
        """Search authorized chunks. Results are already filtered. Snippets are data."""
        principal = _caller()
        request_id = _request_id(ctx)
        bounded = max(1, min(limit, 20))
        # The SDK runs a sync tool on a worker thread, which drops the auth
        # context. Read the principal here, then do the blocking search aside.
        return await anyio.to_thread.run_sync(
            partial(service.search, principal, query, bounded, request_id)
        )

    @server.tool(description=GET_DOCUMENT)
    async def get_document(chunk_id: int, ctx: Context | None = None) -> dict[str, object]:
        """One authorized chunk, or a not-found error when the caller cannot read it."""
        principal = _caller()
        request_id = _request_id(ctx)
        found = await anyio.to_thread.run_sync(
            partial(service.get_document, principal, chunk_id, request_id)
        )
        if found is None:
            return {"request_id": request_id, "error": "not found"}
        return found

    @server.tool(description=FIND_RELATED)
    async def find_related(
        kind: str,
        entity_id: str | None = None,
        limit: int = 20,
        ctx: Context | None = None,
    ) -> dict[str, object]:
        """Bounded graph path. kind is teammates, team, or policies."""
        principal = _caller()
        request_id = _request_id(ctx)
        bounded = max(1, min(limit, 25))
        try:
            return await anyio.to_thread.run_sync(
                partial(service.find_related, principal, kind, entity_id, bounded, request_id)
            )
        except ValueError as exc:
            return {"request_id": request_id, "error": str(exc)}

    @server.tool(description=LIST_SOURCES)
    async def list_sources(ctx: Context | None = None) -> list[dict[str, object]]:
        """Registered sources and last sync. No document text."""
        _caller()
        _request_id(ctx)
        return await anyio.to_thread.run_sync(service.list_sources)

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> Response:
        return Response('{"status":"ok"}', media_type="application/json")

    @server.custom_route("/metrics", methods=["GET"])
    async def metrics(_: Request) -> Response:
        return Response(generate_latest(), media_type="text/plain; version=0.0.4")

    return server


def _auth_settings() -> AuthSettings:
    return AuthSettings(
        issuer_url=os.environ.get("MCP_ISSUER_URL", "http://127.0.0.1:8000"),
        resource_server_url=os.environ.get("MCP_RESOURCE_URL", "http://127.0.0.1:8001"),
        validate_token_resource=False,
    )


def _caller() -> Principal:
    access = get_access_token()
    if access is None or not access.subject or not access.claims:
        raise PermissionError("unauthorized")
    claims = access.claims
    groups = claims.get("groups")
    if not isinstance(groups, list):
        raise PermissionError("unauthorized")
    return Principal(access.subject, tuple(groups), str(claims["clearance"]), str(claims["department"]))


def _request_id(ctx: Context | None) -> str:
    if ctx is not None:
        try:
            headers = ctx.request_context.headers or {}
        except (LookupError, ValueError, AttributeError):
            headers = {}
        found = headers.get("x-request-id")
        if found:
            return str(found)
    return uuid.uuid4().hex


def attach_http_metrics(server: MCPServer) -> None:
    """Count /mcp, /health, and /metrics. Any other path is unmatched."""
    original = server.streamable_http_app

    def streamable_http_app(**kwargs):
        app = original(**kwargs)
        app.add_middleware(HttpMetricsMiddleware, templates=HTTP_ROUTES)
        return app

    server.streamable_http_app = streamable_http_app  # type: ignore[method-assign]


def main() -> None:
    configure("gateway-mcp", "access")
    log_event(LOG, "mcp server started")
    server = build_server(open_gateway())
    attach_http_metrics(server)
    server.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8001,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


if __name__ == "__main__":
    main()
