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

LOG = logging.getLogger("connector-worker")
UP = Gauge("kbs_worker_up", "1 when the worker has loaded its contracts", ["worker"])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    contracts = load_registry()
    for contract in contracts:
        LOG.info(
            "registered source=%s tier=%s capabilities=%s",
            contract.source_id,
            contract.freshness_tier.value,
            ",".join(item.value for item in contract.capabilities),
        )
    port = int(os.environ.get("METRICS_PORT", "9100"))
    start_http_server(port)
    UP.labels(worker="connector").set(1)
    interval = int(os.environ.get("P0_POLL_SECONDS", "30"))
    while True:
        for contract in contracts:
            if contract.freshness_tier.value == "P0":
                LOG.info("p0 in-process poll source=%s stub=no-fetch", contract.source_id)
        time.sleep(interval)


if __name__ == "__main__":
    main()
