"""Helpers for the sales, product, and engineering notebooks.

Notebooks call a harness over HTTP and read raw snapshots from S3. They do
not open SQLite, Postgres, or Neo4j. These sources have no bulk seed.
"""

import os

import httpx
from hr_demo import (
    BUCKET,
    DELETE_WAIT,
    META_ACL,
    META_VERSION,
    object_body,
    parse_metric,
    snapshot,
    versions,
    wait_until,
)

# Poll is 60 seconds. Reconciliation, which is how engineering deletes arrive, is 60 seconds.
WRITE_WAIT = 150
GONE_WAIT = DELETE_WAIT


class Harness:
    def __init__(self, name: str, base: str, metrics: str, prefix: str) -> None:
        self.name = name
        self.base = base
        self.metrics = metrics
        self.prefix = prefix

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        timeout: float = 60,
    ) -> httpx.Response:
        return httpx.request(method, f"{self.base}{path}", json=body, timeout=timeout)

    def admin(self, method: str, path: str, body: dict | None = None) -> dict:
        response = self.request(method, path, body)
        if response.status_code not in (200, 201, 204):
            raise SystemExit(
                f"{method} {path} returned {response.status_code}: {response.text[:300]}"
            )
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def expect_status(self, method: str, path: str, status: int, body: dict | None = None) -> None:
        response = self.request(method, path, body)
        if response.status_code != status:
            raise AssertionError(
                f"{method} {path} returned {response.status_code}, expected {status}: "
                f"{response.text[:300]}"
            )

    def reset(self) -> dict:
        return self.admin("POST", "/admin/reset")

    def counts(self) -> dict:
        return self.admin("GET", "/discover")["counts"]

    def delete_if_present(self, collection: str, object_id: str) -> None:
        response = self.request("DELETE", f"/admin/{collection}/{object_id}")
        if response.status_code not in (204, 404):
            raise SystemExit(
                f"DELETE {collection}/{object_id} returned {response.status_code}: "
                f"{response.text[:300]}"
            )

    def metric(self, name: str, **labels: str) -> float | None:
        try:
            response = httpx.get(self.metrics, timeout=3)
            response.raise_for_status()
        except (httpx.HTTPError, OSError):
            return None
        return parse_metric(response.text, name, labels)

    def key(self, object_id: str, ext: str) -> str:
        return f"{self.prefix}{object_id}.{ext}"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


sales = Harness(
    "sales",
    _env("HARNESS_SALES_URL", "http://127.0.0.1:8102"),
    _env("SALES_METRICS_URL", "http://127.0.0.1:9103/metrics"),
    "raw/sales/deal/",
)
engineering = Harness(
    "engineering",
    _env("HARNESS_ENGINEERING_URL", "http://127.0.0.1:8104"),
    _env("ENGINEERING_METRICS_URL", "http://127.0.0.1:9102/metrics"),
    "raw/engineering/epic/",
)
product = Harness(
    "product",
    _env("HARNESS_PRODUCT_URL", "http://127.0.0.1:8103"),
    _env("PRODUCT_METRICS_URL", "http://127.0.0.1:9104/metrics"),
    "raw/product/spec/",
)

__all__ = [
    "BUCKET",
    "GONE_WAIT",
    "META_ACL",
    "META_VERSION",
    "WRITE_WAIT",
    "engineering",
    "object_body",
    "product",
    "sales",
    "snapshot",
    "versions",
    "wait_until",
]
