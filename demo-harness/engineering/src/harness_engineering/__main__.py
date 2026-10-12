"""Entry point for the engineering harness.

SQLITE_PATH selects the database. An empty database is seeded on start.
"""

import os
from pathlib import Path

import uvicorn

from harness_engineering.app import create_app
from harness_engineering.store import Store
from shared.telemetry import init_telemetry


def main() -> None:
    db_path = Path(os.environ.get("SQLITE_PATH", "/tmp/harness-engineering/harness.db"))
    port = int(os.environ.get("PORT", "8080"))
    init_telemetry("harness-engineering")
    uvicorn.run(create_app(Store(db_path)), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
