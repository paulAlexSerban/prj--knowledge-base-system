"""Product harness client. Specs are markdown. Changes arrive as webhook pointers."""

import httpx

from connector_core.base import ConnectorError, FetchResult, SourceConnector
from connector_core.http import HarnessClient
from ingest_contract.hooks import HookNotice

PAGE = 200


class ProductConnector(SourceConnector):
    source_id = "product"

    def __init__(self, client: httpx.Client, attempts: int = 3, backoff: float = 0.5) -> None:
        self.http = HarnessClient(client, attempts, backoff)

    def discover(self) -> dict:
        """Harness metadata. Not used on every poll."""
        return self.http.get("/discover").json()

    def list_changes(self, updated_since: str | None, page_token: str | None) -> dict:
        """Product has no change listing. Reconciliation uses /ids."""
        return {"items": [], "server_time": updated_since or "hook", "next_page_token": None}

    def fetch(self, object_type: str, object_id: str) -> FetchResult:
        """Spec markdown plus the record. Specs stay prose and are not graph nodes."""
        record = self.http.get(f"/specs/{object_id}").json()
        content = self.http.get(f"/specs/{object_id}/content")
        media = content.headers.get("content-type", "text/markdown")
        return FetchResult(content.content, media, "md", record)

    def get_acl(self, object_type: str, object_id: str) -> list[str]:
        """Native ACL. The indexed rule is authoritative inside the freshness window."""
        body = self.http.get(f"/specs/{object_id}/acl").json()
        return list(body["native_acl"])

    def list_ids(self) -> dict:
        """Live spec ids. A lost webhook is recovered from this listing."""
        return self.http.get("/ids").json()

    def snapshot_body(self, item: dict) -> tuple[bytes, str, str]:
        """Fetch the spec body. The hook pointer does not carry the markdown."""
        fetched = self.fetch(item["object_type"], item["id"])
        return fetched.body, fetched.content_type, fetched.ext


class NoticeFeed(SourceConnector):
    """One hook, presented to the syncer as a single change page."""

    source_id = "product"

    def __init__(self, inner: ProductConnector, notice: HookNotice) -> None:
        self.inner = inner
        self.notice = notice

    def discover(self) -> dict:
        """Delegate to the real product client."""
        return self.inner.discover()

    def list_changes(self, updated_since: str | None, page_token: str | None) -> dict:
        """One page with this hook. A 404 fetch is treated as a delete."""
        notice = self.notice
        if notice.event_type == "delete":
            items = [{"op": "delete", "object_type": "spec", "id": notice.object_id}]
        else:
            try:
                fetched = self.inner.fetch("spec", notice.object_id)
            except ConnectorError as exc:
                if "404" not in str(exc):
                    raise
                items = [{"op": "delete", "object_type": "spec", "id": notice.object_id}]
            else:
                items = [
                    {
                        "object_type": "spec",
                        "id": notice.object_id,
                        "record": fetched.record,
                    }
                ]
        return {
            "items": items,
            "server_time": updated_since or "hook",
            "next_page_token": None,
        }

    def fetch(self, object_type: str, object_id: str) -> FetchResult:
        """Delegate fetch to ProductConnector."""
        return self.inner.fetch(object_type, object_id)

    def get_acl(self, object_type: str, object_id: str) -> list[str]:
        """Delegate ACL to ProductConnector."""
        return self.inner.get_acl(object_type, object_id)

    def list_ids(self) -> dict:
        """Delegate /ids. Reconciliation does not use NoticeFeed."""
        return self.inner.list_ids()

    def snapshot_body(self, item: dict) -> tuple[bytes, str, str]:
        """Delegate the spec bytes to ProductConnector."""
        return self.inner.snapshot_body(item)
