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
        return self.http.get("/discover").json()

    def list_changes(self, updated_since: str | None, page_token: str | None) -> dict:
        params: dict[str, str | int] = {"limit": PAGE}
        if updated_since:
            params["updated_since"] = updated_since
        if page_token:
            params["page_token"] = page_token
        return self.http.get("/changes", params).json()

    def fetch(self, object_type: str, object_id: str) -> FetchResult:
        record = self.http.get(f"/epics/{object_id}").json()
        return FetchResult(canonical_record(record), "application/json", "json", record)

    def get_acl(self, object_type: str, object_id: str) -> list[str]:
        body = self.http.get(f"/epics/{object_id}/acl").json()
        return list(body["native_acl"])

    def list_ids(self) -> dict:
        return self.http.get("/ids").json()

    def snapshot_body(self, item: dict) -> tuple[bytes, str, str]:
        return canonical_record(item["record"]), "application/json", "json"
