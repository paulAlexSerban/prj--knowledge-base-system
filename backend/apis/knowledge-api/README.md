# Knowledge API

REST process for the gateway. Image `knowledge-api`, Compose service `gateway-api`, port 8000.

It loads the contract registry and exits if a contract is invalid. 
Routes: 
- `GET /health`
- `GET /metrics`
- `GET /v1/sources`
- `POST /v1/ask`
- `POST /v1/search`
- `GET /v1/documents/{id}`
- `POST /v1/graph/related`
- `POST /dev/token` when `DEV_TOKEN_ENDPOINT=1`. 

Ask, search, documents, and the graph require `Authorization: Bearer`. `POST /v1/ask` classifies the question and then searches or runs a graph template.

`POST /v1/search` and `POST /v1/graph/related` stay explicit. 
`POST /v1/graph/related` accepts kinds `teammates`, `team`, `policies`, and `roadmap_deals`. `roadmap_deals` takes optional `quarter` (`YYYY-Qn`, default `2026-Q3`) and `min_arr` (default 200000, zero or greater). 

A bad quarter or a negative ARR is 422. 

The service is `gateway-core`. 

It uses `gateway_ro` and a Neo4j read session. 

Snippets and paths are data. 

The process does not write an answer.
