"""Knowledge API stub.

Serves health, metrics, and the list of registered sources. Retrieval is not
implemented. The process exits when the contract registry does not validate.
"""

import logging

import uvicorn
from fastapi import FastAPI, Request
from prometheus_client import make_asgi_app

from shared.logging import configure, log_event
from shared.registry import load_registry, source_payloads

LOG = logging.getLogger("gateway-api")


def create_app() -> FastAPI:
    contracts = load_registry()
    app = FastAPI(title="Knowledge Gateway", version="0.1.0")
    app.state.contracts = contracts

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path != "/health" and not path.startswith("/metrics"):
            fields: dict[str, object] = {
                "path": request.url.path,
                "status": response.status_code,
            }
            request_id = request.headers.get("x-request-id")
            if request_id:
                fields["request_id"] = request_id
            log_event(LOG, "request", **fields)
        return response

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "sources": len(contracts)}

    @app.get("/v1/sources")
    def list_sources() -> list[dict[str, object]]:
        return source_payloads(contracts)

    app.mount("/metrics", make_asgi_app())
    return app


app = create_app()


def main() -> None:
    configure("gateway-api", "access")
    log_event(LOG, "gateway api started")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
