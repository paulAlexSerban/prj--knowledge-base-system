"""Client for the knowledge gateway REST API.

The chat page holds no store credentials. It asks the dev token endpoint for a
persona token, then calls search with that token. Production path: the browser
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

    def search(self, persona: str, query: str, limit: int = 5) -> dict:
        bearer = self.token(persona)
        response = self._client.post(
            "/v1/search",
            json={"query": query, "limit": limit},
            headers={"authorization": f"Bearer {bearer}", **trace_headers()},
        )
        if response.status_code != 200:
            raise GatewayError(f"search: HTTP {response.status_code}")
        return response.json()

    def roadmap_deals(self, persona: str) -> dict:
        bearer = self.token(persona)
        response = self._client.post(
            "/v1/graph/related",
            json={"kind": "roadmap_deals"},
            headers={"authorization": f"Bearer {bearer}", **trace_headers()},
        )
        if response.status_code != 200:
            raise GatewayError(f"roadmap_deals: HTTP {response.status_code}")
        return response.json()
