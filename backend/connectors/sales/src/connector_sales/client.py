"""Sales harness client. Drains the cursor feed, including deletes."""

import httpx

from connector_core.base import FetchResult, SourceConnector
from connector_core.http import HarnessClient, canonical_record

PAGE = 200


class SalesConnector(SourceConnector):
    source_id = "sales"

    def __init__(self, client: httpx.Client, attempts: int = 3, backoff: float = 0.5) -> None:
        self.http = HarnessClient(client, attempts, backoff)

    def discover(self) -> dict:
        return self.http.get("/discover").json()

    def list_changes(self, updated_since: str | None, page_token: str | None) -> dict:
        """Read the feed until it is quiet. The syncer stores the cursor as the watermark."""
        cursor = page_token or updated_since or "0"
        items: list[dict] = []
        while True:
            body = self.http.get("/changes", {"cursor": cursor, "limit": PAGE}).json()
            items.extend(_item(row) for row in body["items"])
            cursor = str(body["next_cursor"])
            if not body.get("more"):
                return {"items": items, "server_time": cursor, "next_page_token": None}

    def fetch(self, object_type: str, object_id: str) -> FetchResult:
        record = self.http.get(f"/deals/{object_id}").json()
        return FetchResult(canonical_record(record), "application/json", "json", record)

    def get_acl(self, object_type: str, object_id: str) -> list[str]:
        body = self.http.get(f"/deals/{object_id}/acl").json()
        return list(body["native_acl"])

    def list_ids(self) -> dict:
        return self.http.get("/ids").json()

    def snapshot_body(self, item: dict) -> tuple[bytes, str, str]:
        return canonical_record(item["record"]), "application/json", "json"


def _item(row: dict) -> dict:
    if row["op"] == "delete":
        return {"op": "delete", "object_type": row["object_type"], "id": row["id"]}
    return {
        "op": "upsert",
        "object_type": row["object_type"],
        "id": row["id"],
        "record": row["record"],
    }
