"""Entry point for the product harness.

SQLITE_PATH selects the database. Spec files live in FILES_DIR, default
`<database directory>/specs`. WEBHOOK_URL receives a best-effort pointer
after each admin write. An empty database is seeded on start.
"""

import os
from pathlib import Path

import uvicorn

from harness_product.app import create_app
from harness_product.notify import Notifier
from harness_product.store import Store


def main() -> None:
    db_path = Path(os.environ.get("SQLITE_PATH", "/tmp/harness-product/harness.db"))
    files_dir = Path(os.environ.get("FILES_DIR", db_path.parent / "specs"))
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(
        create_app(Store(db_path, files_dir), Notifier(os.environ.get("WEBHOOK_URL"))),
        host="0.0.0.0",
        port=port,
    )


if __name__ == "__main__":
    main()
