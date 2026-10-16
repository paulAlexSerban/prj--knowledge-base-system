"""Knowledge API.

Serves health, metrics, the source list, and authorized retrieval. The process
exits when the contract registry does not validate. Store connections open
only when GATEWAY_CONNECT=1, so tests can pass a service in.
"""

import logging
import os
import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from prometheus_client import make_asgi_app

from gateway_core.auth import AuthError, issue_token, principal_from_token
from gateway_core.metrics import AUTH_FAILURES
from gateway_core.runtime import open_gateway
from gateway_core.service import GatewayService
from gateway_core.tools import ASK, FIND_RELATED, GET_DOCUMENT, LIST_SOURCES, SEARCH
from knowledge_api.types import AskBody, RelatedBody, SearchBody, TokenBody

from knowledge_store.acl import Principal
from shared.http_metrics import HttpMetricsMiddleware
from shared.logging import configure, log_event
from shared.registry import load_registry, source_payloads
from shared.telemetry import FASTAPI_TELEMETRY, init_telemetry, operational_path

LOG = logging.getLogger("gateway-api")

# Templates only. A path that matches none of these is the label "unmatched".
HTTP_ROUTES = (
    "/health",
    "/metrics",
    "/v1/sources",
    "/v1/search",
    "/v1/ask",
    "/v1/documents/{chunk_id}",
    "/v1/graph/related",
    "/dev/token",
)


def create_app(service: GatewayService | None = None) -> FastAPI:
    contracts = load_registry()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.service is None and os.environ.get("GATEWAY_CONNECT") == "1":
            app.state.service = open_gateway()
        yield

    app = FastAPI(
        title="Knowledge Gateway",
        version="0.1.0",
        lifespan=lifespan,
        telemetry=FASTAPI_TELEMETRY,
    )
    app.state.contracts = contracts
    app.state.service = service

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        path = request.url.path
        if not operational_path(path):
            fields: dict[str, object] = {"path": path, "status": response.status_code, "request_id": request_id}
            log_event(LOG, "request", **fields)
        return response

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "sources": len(contracts)}

    @app.get("/v1/sources", description=LIST_SOURCES)
    def list_sources() -> list[dict[str, object]]:
        if app.state.service is not None:
            return app.state.service.list_sources()
        payloads = source_payloads(contracts)
        for item in payloads:
            item["last_success_at"] = None
        return payloads

    @app.post("/v1/ask", description=ASK)
    def ask(
        body: AskBody, request: Request, principal: Principal = require_principal
    ) -> dict[str, object]:
        _require_service(request)
        try:
            return request.app.state.service.ask(
                principal, body.query, body.limit, request.state.request_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/search", description=SEARCH)
    def search(
        body: SearchBody, request: Request, principal: Principal = require_principal
    ) -> dict[str, object]:
        _require_service(request)
        return request.app.state.service.search(
            principal, body.query, body.limit, request.state.request_id
        )

    @app.get("/v1/documents/{chunk_id}", description=GET_DOCUMENT)
    def get_document(
        chunk_id: int, request: Request, principal: Principal = require_principal
    ) -> dict[str, object]:
        _require_service(request)
        found = request.app.state.service.get_document(principal, chunk_id, request.state.request_id)
        if found is None:
            raise HTTPException(status_code=404, detail="not found")
        return found

    @app.post("/v1/graph/related", description=FIND_RELATED)
    def find_related(
        body: RelatedBody, request: Request, principal: Principal = require_principal
    ) -> dict[str, object]:
        _require_service(request)
        try:
            return request.app.state.service.find_related(
                principal,
                body.kind,
                body.entity_id,
                body.limit,
                request.state.request_id,
                quarter=body.quarter,
                min_arr=body.min_arr,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/dev/token")
    def dev_token(body: TokenBody) -> dict[str, object]:
        if os.environ.get("DEV_TOKEN_ENDPOINT") != "1":
            raise HTTPException(status_code=404, detail="not found")
        secret = os.environ.get("GATEWAY_JWT_SECRET", "")
        try:
            token = issue_token(body.persona, secret)
        except AuthError as exc:
            status = 404 if exc.reason == "persona" else 503
            raise HTTPException(status_code=status, detail="unauthorized") from exc
        principal = principal_from_token(token, secret)
        return {
            "token": token,
            "principal": {
                "subject": principal.subject,
                "groups": list(principal.groups),
                "clearance": principal.clearance,
                "department": principal.department,
            },
        }

    app.mount("/metrics", make_asgi_app())
    app.add_middleware(HttpMetricsMiddleware, templates=HTTP_ROUTES)
    return app


_AUTHORIZATION = Header(default=None)


def _principal(authorization: str | None = _AUTHORIZATION) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        AUTH_FAILURES.labels(reason="missing").inc()
        raise HTTPException(status_code=401, detail="unauthorized")
    secret = os.environ.get("GATEWAY_JWT_SECRET", "")
    try:
        return principal_from_token(authorization.split(" ", 1)[1].strip(), secret)
    except AuthError as exc:
        AUTH_FAILURES.labels(reason=exc.reason).inc()
        raise HTTPException(status_code=401, detail="unauthorized") from exc


require_principal = Depends(_principal)


def _require_service(request: Request) -> None:
    if request.app.state.service is None:
        raise HTTPException(status_code=503, detail="store unavailable")


app = create_app()


def main() -> None:
    configure("gateway-api", "access")
    init_telemetry("gateway-api")
    log_event(LOG, "gateway api started")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
