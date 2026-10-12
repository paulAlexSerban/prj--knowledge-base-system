"""Engineering connector process. The poll loop is connector-core."""

import httpx

from connector_core.runtime import run_poll
from connector_engineering.client import EngineeringConnector


def _client(base_url: str) -> EngineeringConnector:
    return EngineeringConnector(httpx.Client(base_url=base_url, timeout=30))


def main() -> None:
    run_poll("connector-engineering", "engineering", "HARNESS_ENGINEERING_URL", _client)
