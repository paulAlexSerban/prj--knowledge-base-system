"""Engineering harness client. Polls updated_since and reads ids for the delete sweep."""

import httpx

from connector_core.base import FetchResult, SourceConnector
from connector_core.http import HarnessClient, canonical_record

PAGE = 200


class EngineeringConnector(SourceConnector):
    source_id = "engineering"

    def __init__(self, client: httpx.Client, attempts: int = 3, backoff: float = 0.5) -> None:
        self.http = HarnessClient(client, attempts, backoff)

    def discover(self) -> dict:
        """Harness metadata. Not used on every poll."""
        return self.http.get("/discover").json()

    def list_changes(self, updated_since: str | None, page_token: str | None) -> dict:
        """updated_since listing of epics. Deletes are found by the /ids sweep."""
        params: dict[str, str | int] = {"limit": PAGE}
        if updated_since:
            params["updated_since"] = updated_since
        if page_token:
            params["page_token"] = page_token
        return self.http.get("/changes", params).json()

    def fetch(self, object_type: str, object_id: str) -> FetchResult:
        """One epic as canonical JSON. team is the HR team key."""
        record = self.http.get(f"/epics/{object_id}").json()
        return FetchResult(canonical_record(record), "application/json", "json", record)

    def get_acl(self, object_type: str, object_id: str) -> list[str]:
        """Native ACL. The indexed rule is authoritative inside the freshness window."""
        body = self.http.get(f"/epics/{object_id}/acl").json()
        return list(body["native_acl"])

    def list_ids(self) -> dict:
        """Live epic ids for the delete sweep."""
        return self.http.get("/ids").json()

    def snapshot_body(self, item: dict) -> tuple[bytes, str, str]:
        """Canonical JSON of the listing record. Avoids a second GET on every change."""
        return canonical_record(item["record"]), "application/json", "json"
