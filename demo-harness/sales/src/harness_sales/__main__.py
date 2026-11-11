"""Entry point for the sales harness.

SQLITE_PATH selects the database. An empty database is seeded on start.
"""

import logging
import os
from pathlib import Path

import uvicorn

from harness_sales.app import create_app
from harness_sales.store import Store
from shared.logging import configure, log_event
from shared.telemetry import init_telemetry


def main() -> None:
    db_path = Path(os.environ.get("SQLITE_PATH", "/tmp/harness-sales/harness.db"))
    port = int(os.environ.get("PORT", "8080"))
    configure("harness-sales", "sources")
    init_telemetry("harness-sales")
    log_event(logging.getLogger("harness-sales"), "harness ready", source_id="sales")
    uvicorn.run(
        create_app(Store(db_path)),
        host="0.0.0.0",
        port=port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
