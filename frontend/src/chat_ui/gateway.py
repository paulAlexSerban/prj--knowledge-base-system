"""Client for the knowledge gateway REST API.

The chat page holds no store credentials. It asks the dev token endpoint for a
persona token, then calls /v1/ask with that token. Production path: the browser
user's OIDC token replaces the persona token.
"""

import httpx

from shared.telemetry import trace_headers


class GatewayError(Exception):
    """The gateway refused or could not answer."""


class Gateway:
    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=base_url, timeout=30)

    def token(self, persona: str) -> str:
        response = self._client.post("/dev/token", json={"persona": persona})
        if response.status_code != 200:
            raise GatewayError(f"token for {persona}: HTTP {response.status_code}")
        return str(response.json()["token"])

    def ask(self, persona: str, query: str, limit: int = 5) -> dict:
        bearer = self.token(persona)
        response = self._client.post(
            "/v1/ask",
            json={"query": query, "limit": limit},
            headers={"authorization": f"Bearer {bearer}", **trace_headers()},
        )
        if response.status_code != 200:
            raise GatewayError(f"ask: HTTP {response.status_code}")
        return response.json()
