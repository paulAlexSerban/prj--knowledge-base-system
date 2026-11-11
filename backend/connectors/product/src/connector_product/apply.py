"""Turn one hook notice into a sync cycle."""

from connector_core.state import State
from connector_core.store import SnapshotStore
from connector_core.sync import Syncer, SyncReport
from connector_product.client import NoticeFeed, ProductConnector
from ingest_contract.hooks import HookNotice
from shared.contracts.source_contract import SourceContract


def apply_notice(
    connector: ProductConnector,
    contract: SourceContract,
    state: State,
    snapshots: SnapshotStore,
    publisher: object | None,
    notice: HookNotice,
) -> SyncReport:
    """Wrap the notice as a one-item feed and run the shared syncer."""
    feed = NoticeFeed(connector, notice)
    return Syncer(feed, contract, state, snapshots, 1, publisher).run()
