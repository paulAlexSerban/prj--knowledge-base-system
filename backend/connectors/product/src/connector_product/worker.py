"""Product connector. Consumes hook pointers and reconciles on a timer.

There is no updated_since poll. A lost hook is recovered from /ids.
"""

import logging
import os
import time

import httpx
from botocore.exceptions import BotoCoreError, ClientError

from connector_core.runtime import prepare, sqs_client
from connector_core.sync import Syncer
from connector_product.apply import apply_notice
from connector_product.client import ProductConnector
from connector_product.queue import HookQueue
from ingest_contract.hooks import HookNotice
from shared.logging import log_event
from shared.telemetry import span

LOG = logging.getLogger("connector-product")


def _client(base_url: str) -> ProductConnector:
    return ProductConnector(httpx.Client(base_url=base_url, timeout=30))


def take_notices(hooks: HookQueue) -> list[tuple[HookNotice, str, object]] | None:
    """Read one batch. None means the queue could not be reached, so the process stays up."""
    try:
        hooks.observe()
        return hooks.receive()
    except (BotoCoreError, ClientError, OSError) as exc:
        log_event(
            LOG,
            "hook queue unreachable",
            level=logging.WARNING,
            source_id="product",
            error=str(exc),
        )
        hooks.ready()
        return None


def main() -> None:
    ready = prepare("connector-product", "product", "HARNESS_PRODUCT_URL", _client)
    hooks = HookQueue(sqs_client())
    delay = 2.0
    while not hooks.ready():
        log_event(LOG, "waiting for the hook queue", level=logging.WARNING, source_id="product")
        time.sleep(delay)
        delay = min(delay * 2, 30)
    syncer = Syncer(
        ready.connector,
        ready.contract,
        ready.state,
        ready.snapshots,
        int(os.environ.get("SYNC_WORKERS", "4")),
        ready.publisher,
    )
    try:
        syncer.reconcile()
    except Exception as exc:  # noqa: BLE001 - startup backfill must not kill the process
        log_event(
            LOG, "reconcile crashed", level=logging.ERROR, source_id="product", error=str(exc)
        )
    reconcile_every = int(os.environ.get("RECONCILE_SECONDS", "60"))
    last = time.monotonic()
    while True:
        notices = take_notices(hooks)
        if notices is None:
            time.sleep(2)
            continue
        for notice, handle, parent in notices:
            try:
                with span(
                    "hook.apply",
                    context=parent,
                    source_id="product",
                    event_type=notice.event_type,
                ):
                    report = apply_notice(
                        ready.connector,
                        ready.contract,
                        ready.state,
                        ready.snapshots,
                        ready.publisher,
                        notice,
                    )
            except Exception as exc:  # noqa: BLE001 - one bad notice must not kill the process
                log_event(
                    LOG,
                    "hook crashed",
                    level=logging.ERROR,
                    source_id="product",
                    object_id=notice.object_id,
                    error=str(exc),
                )
                continue
            if report.ok:
                try:
                    hooks.ack(handle)
                except (BotoCoreError, ClientError, OSError) as exc:
                    log_event(
                        LOG,
                        "hook ack failed",
                        level=logging.WARNING,
                        source_id="product",
                        object_id=notice.object_id,
                        error=str(exc),
                    )
            else:
                log_event(
                    LOG,
                    "hook not applied",
                    level=logging.WARNING,
                    source_id="product",
                    object_id=notice.object_id,
                    errors=report.errors,
                )
        if time.monotonic() - last >= reconcile_every:
            try:
                syncer.reconcile()
            except Exception as exc:  # noqa: BLE001
                log_event(
                    LOG,
                    "reconcile crashed",
                    level=logging.ERROR,
                    source_id="product",
                    error=str(exc),
                )
            last = time.monotonic()
