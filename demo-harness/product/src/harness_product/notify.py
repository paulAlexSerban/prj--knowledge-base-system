"""Best-effort product webhook. A failure never fails the write that caused it."""

import json
import urllib.error
import urllib.request

from shared.telemetry import client_span, peer_host, trace_headers

EVENTS = frozenset({"create", "update", "delete"})


class Notifier:
    def __init__(self, url: str | None, timeout: float = 2.0) -> None:
        self.url = url or ""
        self.timeout = timeout

    def notify(self, object_id: str, event_type: str) -> bool:
        if not self.url or event_type not in EVENTS:
            return False
        body = json.dumps(
            {"source_id": "product", "object_id": object_id, "event_type": event_type}
        ).encode()
        peer = peer_host(self.url) or "webhook-receiver"
        try:
            with client_span(peer, "product.hook"):
                request = urllib.request.Request(
                    self.url,
                    data=body,
                    headers={"content-type": "application/json", **trace_headers()},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return 200 <= response.status < 300
        except (urllib.error.URLError, TimeoutError, OSError):
            return False
