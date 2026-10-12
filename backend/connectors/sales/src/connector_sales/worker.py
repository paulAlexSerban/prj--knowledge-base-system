"""Sales connector process. The poll loop is connector-core."""

import httpx

from connector_core.runtime import run_poll
from connector_sales.client import SalesConnector


def _client(base_url: str) -> SalesConnector:
    return SalesConnector(httpx.Client(base_url=base_url, timeout=30))


def main() -> None:
    run_poll("connector-sales", "sales", "HARNESS_SALES_URL", _client)
