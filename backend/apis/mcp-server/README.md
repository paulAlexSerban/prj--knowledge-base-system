# MCP server

MCP process for the gateway. Image `mcp-server`, Compose service `gateway-mcp`, port 8001.

Tools: `ask_knowledge`, `search_knowledge`, `get_document`, `find_related`, and `list_sources`. They call `gateway-core`, the same service as the REST API. `ask_knowledge` classifies the question and then searches or runs a graph template. `find_related` accepts the same kinds as REST, including `roadmap_deals`, plus optional `quarter` and `min_arr`. A bad quarter or a negative ARR returns an error object and does not call the graph. `/mcp` requires `Authorization: Bearer`. `/health` and `/metrics` stay open. The server does not mint tokens and does not write.
