"""Connector interface from spike 01.

`normalize` stays with ingestion. This step fetches and snapshots.
"""

from dataclasses import dataclass


class ConnectorError(Exception):
    pass


@dataclass(frozen=True)
class FetchResult:
    body: bytes
    content_type: str
    ext: str
    record: dict


class SourceConnector:
    source_id: str

    def discover(self) -> dict:
        raise NotImplementedError

    def list_changes(self, updated_since: str | None, page_token: str | None) -> dict:
        raise NotImplementedError

    def fetch(self, object_type: str, object_id: str) -> FetchResult:
        raise NotImplementedError

    def get_acl(self, object_type: str, object_id: str) -> list[str]:
        raise NotImplementedError

    def list_ids(self) -> dict:
        raise NotImplementedError
