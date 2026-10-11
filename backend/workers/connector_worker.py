"""Connector worker stub.

Loads the contract registry and refuses to start when it is invalid. P0
sources are polled in-process because EventBridge Scheduler cannot run
faster than once a minute. The poll does not fetch from the harness yet.
"""

import logging
import os
import time

from prometheus_client import Gauge, start_http_server

from backend.apis.registry import load_registry
from shared.logging import configure, log_event

LOG = logging.getLogger("connector-worker")
UP = Gauge("kbs_worker_up", "1 when the worker has loaded its contracts", ["worker"])


def main() -> None:
    configure("connector-worker", "ingestion")
    contracts = load_registry()
    for contract in contracts:
        log_event(
            LOG,
            "registered source",
            source_id=contract.source_id,
            freshness_tier=contract.freshness_tier.value,
            capabilities=",".join(item.value for item in contract.capabilities),
        )
    port = int(os.environ.get("METRICS_PORT", "9100"))
    start_http_server(port)
    UP.labels(worker="connector").set(1)
    interval = int(os.environ.get("P0_POLL_SECONDS", "30"))
    while True:
        for contract in contracts:
            if contract.freshness_tier.value == "P0":
                log_event(LOG, "p0 in-process poll", source_id=contract.source_id, stub="no-fetch")
        time.sleep(interval)


if __name__ == "__main__":
    main()
