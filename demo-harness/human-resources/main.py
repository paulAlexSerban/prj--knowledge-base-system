"""Entry point for the HR harness service.

SQLITE_PATH selects the database. Policy files live in FILES_DIR, default
`<database directory>/policies`. An empty database is seeded on start.
"""

import os
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).parent))

from hr_app import create_app
from hr_store import Store


def main() -> None:
    db_path = Path(os.environ.get("SQLITE_PATH", "/tmp/harness-hr/harness.db"))
    files_dir = Path(os.environ.get("FILES_DIR", db_path.parent / "policies"))
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(create_app(Store(db_path, files_dir)), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
