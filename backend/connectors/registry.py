"""Which source ids have a connector. The rest are logged and not started."""

import httpx

from backend.connectors.base import SourceConnector
from backend.connectors.hr import HrConnector

CONNECTORS: dict[str, type[SourceConnector]] = {"hr": HrConnector}


def build_connector(source_id: str, client: httpx.Client) -> SourceConnector | None:
    kind = CONNECTORS.get(source_id)
    if kind is None:
        return None
    return kind(client)
