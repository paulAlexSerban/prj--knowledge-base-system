"""Product webhook receiver stub. Accepts the body and does not enqueue it."""

import logging

import uvicorn
from fastapi import FastAPI, Request
from prometheus_client import make_asgi_app

LOG = logging.getLogger("webhook-receiver")


def create_app() -> FastAPI:
    app = FastAPI(title="Webhook receiver", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/product", status_code=202)
    async def product_change(request: Request) -> dict[str, object]:
        body = await request.json()
        LOG.info("accepted product webhook object_id=%s enqueued=false", body.get("object_id"))
        return {"accepted": True, "enqueued": False}

    app.mount("/metrics", make_asgi_app())
    return app


app = create_app()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    uvicorn.run(app, host="0.0.0.0", port=8090)


if __name__ == "__main__":
    main()
