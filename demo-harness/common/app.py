"""One FastAPI app for all four harness services.

SERVICE selects the change API. SQLite is created on SQLITE_PATH so the
volume is real; the tables stay empty until seed data exists.
"""

import os
import sqlite3
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from prometheus_client import Gauge, make_asgi_app

SERVICES = {
    "hr": "updated_since",
    "sales": "cursor_feed",
    "product": "webhook",
    "engineering": "updated_since",
}

UP = Gauge("kbs_harness_up", "1 when a harness service is serving", ["service"])


def open_db(path: str) -> None:
    database = Path(path)
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS objects (id TEXT PRIMARY KEY, updated_at TEXT)"
        )


def create_app(service: str) -> FastAPI:
    if service not in SERVICES:
        raise SystemExit(f"SERVICE must be one of {sorted(SERVICES)}")
    capability = SERVICES[service]
    open_db(os.environ.get("SQLITE_PATH", "/tmp/harness.db"))
    app = FastAPI(title=f"Harness {service}", version="0.1.0")
    UP.labels(service=service).set(1)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": service, "capability": capability}

    @app.get("/changes")
    def changes(updated_since: str | None = None, cursor: str | None = None) -> dict[str, object]:
        return {
            "service": service,
            "capability": capability,
            "updated_since": updated_since,
            "cursor": cursor,
            "items": [],
        }

    @app.post("/changes", status_code=202)
    def notify() -> dict[str, object]:
        return {
            "service": service,
            "capability": capability,
            "webhook": {
                "source_id": service,
                "event_type": "update",
                "enqueued": False,
            },
        }

    app.mount("/metrics", make_asgi_app())
    return app


def main() -> None:
    service = os.environ.get("SERVICE", "")
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(create_app(service), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
