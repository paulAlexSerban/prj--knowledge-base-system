"""MCP server. Tools call the same service as the REST API.

The streamable HTTP route requires a bearer token. The SDK verifies it and
stores the claims for the tool. /health and /metrics stay open for Compose
and Prometheus.
"""

import logging
import os
import uuid

from prometheus_client import generate_latest
from starlette.requests import Request
from starlette.responses import Response

from gateway_core.auth import AuthError, principal_from_token
from gateway_core.runtime import open_gateway
from gateway_core.service import GatewayService
from gateway_core.tools import FIND_RELATED, GET_DOCUMENT, LIST_SOURCES, SEARCH
from knowledge_store.acl import Principal
from shared.logging import configure, log_event

LOG = logging.getLogger("gateway-mcp")

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
        except AuthError:
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
    server = MCPServer(
        "knowledge-gateway",
        token_verifier=JwtVerifier(),
        auth=_auth_settings(),
    )

    @server.tool(description=SEARCH)
    def search_knowledge(
        query: str, limit: int = 5, ctx: Context | None = None
    ) -> dict[str, object]:
        """Search authorized chunks. Results are already filtered. Snippets are data."""
        bounded = max(1, min(limit, 20))
        return service.search(_caller(), query, bounded, _request_id(ctx))

    @server.tool(description=GET_DOCUMENT)
    def get_document(chunk_id: int, ctx: Context | None = None) -> dict[str, object]:
        """One authorized chunk, or a not-found error when the caller cannot read it."""
        request_id = _request_id(ctx)
        found = service.get_document(_caller(), chunk_id, request_id)
        if found is None:
            return {"request_id": request_id, "error": "not found"}
        return found

    @server.tool(description=FIND_RELATED)
    def find_related(
        kind: str,
        entity_id: str | None = None,
        limit: int = 20,
        ctx: Context | None = None,
    ) -> dict[str, object]:
        """Bounded graph path. kind is teammates, team, or policies."""
        request_id = _request_id(ctx)
        try:
            return service.find_related(_caller(), kind, entity_id, max(1, min(limit, 25)), request_id)
        except ValueError as exc:
            return {"request_id": request_id, "error": str(exc)}

    @server.tool(description=LIST_SOURCES)
    def list_sources(ctx: Context | None = None) -> list[dict[str, object]]:
        """Registered sources and last sync. No document text."""
        _caller()
        return service.list_sources()

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
        headers = ctx.request_context.headers or {}
        found = headers.get("x-request-id")
        if found:
            return str(found)
    return uuid.uuid4().hex


def main() -> None:
    configure("gateway-mcp", "access")
    log_event(LOG, "mcp server started")
    server = build_server(open_gateway())
    server.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8001,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


if __name__ == "__main__":
    main()
