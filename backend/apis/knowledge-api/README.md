# Knowledge API

REST process for the gateway. Image `knowledge-api`, Compose service `gateway-api`, port 8000.

It loads the contract registry and exits if a contract is invalid. `GET /health`, `GET /v1/sources`, and `/metrics` are served. Retrieval is not built.
