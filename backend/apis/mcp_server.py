"""MCP stub with one tool, list_sources.

DNS rebinding protection is off so Prometheus and local clients can call the
port. Turn it on before this process is reachable beyond the demo machine.
"""

from prometheus_client import generate_latest
from starlette.requests import Request
from starlette.responses import Response

from backend.apis.registry import load_registry, source_payloads

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings
except ImportError as exc:  # pragma: no cover - exercised only if the SDK moves again
    raise SystemExit(
        "mcp.server.mcpserver.MCPServer is required; the FastMCP name was removed in mcp 2"
    ) from exc


def build_server() -> MCPServer:
    contracts = load_registry()
    server = MCPServer("knowledge-gateway")

    @server.tool()
    def list_sources() -> list[dict[str, object]]:
        """List registered sources. Returns no document text."""
        return source_payloads(contracts)

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> Response:
        return Response('{"status":"ok"}', media_type="application/json")

    @server.custom_route("/metrics", methods=["GET"])
    async def metrics(_: Request) -> Response:
        return Response(generate_latest(), media_type="text/plain; version=0.0.4")

    return server


def main() -> None:
    server = build_server()
    server.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8001,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


if __name__ == "__main__":
    main()
