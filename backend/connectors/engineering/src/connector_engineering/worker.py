"""Engineering connector process. The poll loop is connector-core."""

import httpx

from connector_core.runtime import run_poll
from connector_engineering.client import EngineeringConnector


def _client(base_url: str) -> EngineeringConnector:
    """Engineering harness client. The poll loop lives in connector-core."""
    return EngineeringConnector(httpx.Client(base_url=base_url, timeout=30))


def main() -> None:
    """P1 poll of updated_since, plus the reconciliation sweep."""
    run_poll("connector-engineering", "engineering", "HARNESS_ENGINEERING_URL", _client)
