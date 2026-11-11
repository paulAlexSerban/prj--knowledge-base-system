"""Connector interface from spike 01.

`normalize` stays with ingestion. This step fetches and snapshots.
"""

from dataclasses import dataclass


class ConnectorError(Exception):
    """Harness or transport failure. The syncer records the stage and retries next cycle."""


@dataclass(frozen=True)
class FetchResult:
    """Bytes to snapshot plus the structured record used for ACL, version, and graph edges."""

    body: bytes
    content_type: str
    ext: str
    record: dict


class SourceConnector:
    """One department source. Detects changes, fetches bodies, and maps native ACLs.

    The connector never embeds or writes the knowledge stores. Ingestion owns that.
    """

    source_id: str

    def discover(self) -> dict:
        """Source metadata from the harness. Used at start-up, not on every poll."""
        raise NotImplementedError

    def list_changes(self, updated_since: str | None, page_token: str | None) -> dict:
        """One page of creates, updates, or deletes since the stored watermark."""
        raise NotImplementedError

    def fetch(self, object_type: str, object_id: str) -> FetchResult:
        """Current body and record for one object. Reconciliation uses this when the feed missed it."""
        raise NotImplementedError

    def get_acl(self, object_type: str, object_id: str) -> list[str]:
        """Native permission labels. The contract maps these onto group, department, and clearance."""
        raise NotImplementedError

    def list_ids(self) -> dict:
        """Live ids and versions. The sweep deletes snapshots that are no longer listed."""
        raise NotImplementedError

    def snapshot_body(self, item: dict) -> tuple[bytes, str, str]:
        """Bytes, content type, and extension for one change item. The default fetches."""
        fetched = self.fetch(item["object_type"], item["id"])
        return fetched.body, fetched.content_type, fetched.ext
