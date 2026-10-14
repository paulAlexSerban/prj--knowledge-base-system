"""HR harness connector. Polls updated_since and reads ids for the delete sweep."""

import json
import time

import httpx

from connector_core.base import ConnectorError, FetchResult, SourceConnector
from shared.telemetry import client_span, peer_host, trace_headers

PAGE = 200


def canonical_record(record: dict) -> bytes:
    """Stable JSON for a person. Lists are sorted so the hash does not flap."""
    stable = {key: _stable(value) for key, value in sorted(record.items())}
    return json.dumps(stable, separators=(",", ":")).encode()


def _stable(value: object) -> object:
    if isinstance(value, list):
        return sorted(str(item) for item in value)
    return value


class HrConnector(SourceConnector):
    source_id = "hr"

    def __init__(self, client: httpx.Client, attempts: int = 3, backoff: float = 0.5) -> None:
        self.client = client
        self.attempts = attempts
        self.backoff = backoff

    def discover(self) -> dict:
        return self._get("/discover").json()

    def list_changes(self, updated_since: str | None, page_token: str | None) -> dict:
        params: dict[str, str | int] = {"limit": PAGE}
        if updated_since:
            params["updated_since"] = updated_since
        if page_token:
            params["page_token"] = page_token
        return self._get("/changes", params).json()

    def fetch(self, object_type: str, object_id: str) -> FetchResult:
        if object_type == "person":
            record = self._get(f"/people/{object_id}").json()
            return FetchResult(canonical_record(record), "application/json", "json", record)
        record = self._get(f"/policies/{object_id}").json()
        content = self._get(f"/policies/{object_id}/content")
        ext = "pdf" if record.get("format") == "pdf" else "md"
        media = content.headers.get("content-type", "application/octet-stream")
        return FetchResult(content.content, media, ext, record)

    def get_acl(self, object_type: str, object_id: str) -> list[str]:
        path = "people" if object_type == "person" else "policies"
        body = self._get(f"/{path}/{object_id}/acl").json()
        return list(body["native_acl"])

    def list_ids(self) -> dict:
        return self._get("/ids").json()

    def snapshot_body(self, item: dict) -> tuple[bytes, str, str]:
        """People are canonical JSON of the change record. Policies are fetched."""
        if item["object_type"] == "person":
            record = item["record"]
            return canonical_record(record), "application/json", "json"
        return super().snapshot_body(item)

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        peer = peer_host(str(self.client.base_url)) or "harness-hr"
        with client_span(peer, "harness.get") as current:
            response = self._attempt(path, params)
            current.set_attribute("http.status_code", response.status_code)
            return response

    def _attempt(self, path: str, params: dict | None) -> httpx.Response:
        delay = self.backoff
        last: Exception | None = None
        for attempt in range(self.attempts):
            try:
                response = self.client.get(path, params=params, headers=trace_headers())
            except httpx.TransportError as exc:
                last = exc
            else:
                if response.status_code < 500:
                    if response.is_error:
                        raise ConnectorError(
                            f"{path} returned {response.status_code}: {response.text[:200]}"
                        )
                    return response
                last = ConnectorError(f"{path} returned {response.status_code}")
            if attempt + 1 < self.attempts:
                time.sleep(delay)
                delay *= 2
        raise ConnectorError(str(last)) from last
