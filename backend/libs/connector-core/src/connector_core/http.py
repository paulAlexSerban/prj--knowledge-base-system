"""Small HTTP helpers for harness clients. HR keeps its own copy."""

import json
import time

import httpx

from connector_core.base import ConnectorError
from shared.telemetry import client_span, peer_host, trace_headers


def canonical_record(record: dict) -> bytes:
    """Stable JSON. Lists are sorted so the hash does not flap."""
    stable = {key: _stable(value) for key, value in sorted(record.items())}
    return json.dumps(stable, separators=(",", ":")).encode()


def _stable(value: object) -> object:
    if isinstance(value, list):
        return sorted(str(item) for item in value)
    return value


class HarnessClient:
    """GET with a short retry on transport errors and 5xx. A 4xx is final."""

    def __init__(self, client: httpx.Client, attempts: int = 3, backoff: float = 0.5) -> None:
        self.client = client
        self.attempts = attempts
        self.backoff = backoff

    def get(self, path: str, params: dict | None = None) -> httpx.Response:
        peer = peer_host(str(self.client.base_url)) or "harness"
        with client_span(peer, "harness.get") as current:
            response = self._get(path, params)
            current.set_attribute("http.status_code", response.status_code)
            return response

    def _get(self, path: str, params: dict | None) -> httpx.Response:
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
