"""Entry point for the HR harness service.

SQLITE_PATH selects the database. Policy files live in FILES_DIR, default
`<database directory>/policies`. An empty database is seeded on start.
"""

import logging
import os
from pathlib import Path

import uvicorn

from harness_hr.app import create_app
from harness_hr.store import Store
from shared.logging import configure, log_event
from shared.telemetry import init_telemetry


def main() -> None:
    db_path = Path(os.environ.get("SQLITE_PATH", "/tmp/harness-hr/harness.db"))
    files_dir = Path(os.environ.get("FILES_DIR", db_path.parent / "policies"))
    port = int(os.environ.get("PORT", "8080"))
    configure("harness-hr", "sources")
    init_telemetry("harness-hr")
    log_event(logging.getLogger("harness-hr"), "harness ready", source_id="hr")
    uvicorn.run(
        create_app(Store(db_path, files_dir)),
        host="0.0.0.0",
        port=port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
