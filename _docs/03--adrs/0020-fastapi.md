# ADR 0020: FastAPI for the HTTP services

Status: decided during the build. It was not compared in a spike.

## Context

The REST gateway, the webhook receiver, and the four harnesses are HTTP services with typed bodies. The MCP server is not one of them ([ADR 0021](0021-mcp.md)). The architecture table used to say this choice was not evaluated. That is still true. This ADR records the reason it was kept, not a bake-off that did not happen.

## Decision

Those HTTP services use FastAPI, Pydantic v2, and uvicorn. Request and response models are the contract. The source-contract model is the same Pydantic, and its JSON Schema is what `make contracts-check` diffs. OpenTelemetry hooks the FastAPI app from `shared`. The service layer is `gateway-core`. The API is a thin adapter.

## Alternatives rejected

- Flask. Familiar, and it would mean a second validation library next to Pydantic. Not measured.
- Django. An admin, an ORM, and a project layout this gateway does not use. The harnesses already use SQLAlchemy directly. Not measured.
- Litestar, or another ASGI framework. Not evaluated. There is no latency or feature gap on record that would pay for a move.

## Consequences

- The repo has two HTTP stacks: FastAPI, and the MCP SDK's own server. They share `gateway-core` and do not share a router.
- "We chose FastAPI" is not evidence that the others were slower or less safe. It is evidence that one stack was already in use and nothing forced a second look.
