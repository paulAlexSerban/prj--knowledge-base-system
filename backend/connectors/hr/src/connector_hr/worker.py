"""HR connector process.

The poll loop lives in connector-core. This module picks the HR contract and
the HR client. The P0 target is 30 seconds and EventBridge Scheduler cannot
run faster than once a minute, so the loop defaults to 10 seconds
(`P0_POLL_SECONDS`).

Each cycle writes raw snapshots to S3 and records them in Postgres. Deletes
are found by the reconciliation sweep (`RECONCILE_SECONDS`, default 60),
because the HR harness has no delete feed.
"""

import httpx

from connector_core.runtime import run_poll, select_source
from connector_hr.client import HrConnector
from shared.contracts.source_contract import SourceContract


def select_hr(contracts: list[SourceContract]) -> SourceContract:
    """Return the HR contract. This process does not start any other source."""
    return select_source(contracts, "hr")


def _client(base_url: str) -> HrConnector:
    return HrConnector(httpx.Client(base_url=base_url, timeout=30))


def main() -> None:
    run_poll("connector-hr", "hr", "HARNESS_HR_URL", _client)
