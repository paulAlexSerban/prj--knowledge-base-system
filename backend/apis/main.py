"""Knowledge API stub.

Serves health, metrics, and the list of registered sources. Retrieval is not
implemented. The process exits when the contract registry does not validate.
"""

import uvicorn
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from backend.apis.registry import load_registry, source_payloads


def create_app() -> FastAPI:
    contracts = load_registry()
    app = FastAPI(title="Knowledge Gateway", version="0.1.0")
    app.state.contracts = contracts

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
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
