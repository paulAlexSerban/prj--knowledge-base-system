# Knowledge API

REST process for the gateway. Image `knowledge-api`, Compose service `gateway-api`, port 8000.

It loads the contract registry and exits if a contract is invalid. Routes: `GET /health`, `GET /metrics`, `GET /v1/sources`, `POST /v1/search`, `GET /v1/documents/{id}`, `POST /v1/graph/related`, and `POST /dev/token` when `DEV_TOKEN_ENDPOINT=1`. Search, documents, and the graph require `Authorization: Bearer`. The service is `gateway-core`. It uses `gateway_ro` and a Neo4j read session. Snippets are data. The process does not write an answer.
