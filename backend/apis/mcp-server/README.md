# MCP server

MCP process for the gateway. Image `mcp-server`, Compose service `gateway-mcp`, port 8001.

Tools: `search_knowledge`, `get_document`, `find_related`, and `list_sources`. They call `gateway-core`, the same service as the REST API. `/mcp` requires `Authorization: Bearer`. `/health` and `/metrics` stay open. The server does not mint tokens and does not write.
