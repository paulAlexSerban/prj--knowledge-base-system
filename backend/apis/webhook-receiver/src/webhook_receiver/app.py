"""Product webhook receiver stub. Accepts the body and does not enqueue it."""

import logging

import uvicorn
from fastapi import FastAPI, Request
from prometheus_client import make_asgi_app

from shared.logging import configure, log_event

LOG = logging.getLogger("webhook-receiver")


def create_app() -> FastAPI:
    app = FastAPI(title="Webhook receiver", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/product", status_code=202)
    async def product_change(request: Request) -> dict[str, object]:
        body = await request.json()
        log_event(
            LOG,
            "accepted product webhook",
            source_id="product",
            object_id=body.get("object_id"),
            enqueued=False,
        )
        return {"accepted": True, "enqueued": False}

    app.mount("/metrics", make_asgi_app())
    return app


app = create_app()


def main() -> None:
    configure("webhook-receiver", "sources")
    uvicorn.run(app, host="0.0.0.0", port=8090, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
