"""Helpers for the gateway notebooks.

They call the API and the MCP server over HTTP, and they read the gateway
log from Loki. They do not open Postgres or Neo4j.
"""

import asyncio
import json
import os
import threading
import time

import httpx

API = os.environ.get("GATEWAY_API_URL", "http://127.0.0.1:8000")
MCP = os.environ.get("GATEWAY_MCP_URL", "http://127.0.0.1:8001/mcp")
LOKI = os.environ.get("LOKI_URL", "http://127.0.0.1:3100")


def raw(
    method: str,
    path: str,
    token: str | None = None,
    body: dict | None = None,
    request_id: str | None = None,
) -> httpx.Response:
    headers = {}
    if token:
        headers["authorization"] = f"Bearer {token}"
    if request_id:
        headers["x-request-id"] = request_id
    return httpx.request(method, f"{API}{path}", json=body, headers=headers, timeout=60)


def token(persona: str) -> str:
    response = raw("POST", "/dev/token", body={"persona": persona})
    if response.status_code != 200:
        raise SystemExit(
            f"token for {persona} returned {response.status_code}: {response.text[:200]}"
        )
    return str(response.json()["token"])


def search(bearer: str, query: str, limit: int = 5, request_id: str | None = None) -> dict:
    response = raw(
        "POST",
        "/v1/search",
        token=bearer,
        body={"query": query, "limit": limit},
        request_id=request_id,
    )
    if response.status_code != 200:
        raise SystemExit(f"search returned {response.status_code}: {response.text[:200]}")
    return response.json()


def related(
    bearer: str, kind: str, entity_id: str | None = None, limit: int = 25
) -> dict:
    body: dict[str, object] = {"kind": kind, "limit": limit}
    if entity_id:
        body["entity_id"] = entity_id
    response = raw("POST", "/v1/graph/related", token=bearer, body=body)
    if response.status_code != 200:
        raise SystemExit(f"related returned {response.status_code}: {response.text[:200]}")
    return response.json()


def decision_line(request_id: str) -> dict | None:
    """The gateway log line for this request, or None when Loki has not stored it yet."""
    now = time.time()
    params = {
        "query": f'{{service=~"gateway-api|gateway-mcp"}} |= "{request_id}"',
        "start": int((now - 600) * 1e9),
        "end": int((now + 30) * 1e9),
        "limit": 20,
    }
    try:
        response = httpx.get(f"{LOKI}/loki/api/v1/query_range", params=params, timeout=10)
        response.raise_for_status()
    except (httpx.HTTPError, OSError):
        return None
    for stream in response.json().get("data", {}).get("result", []):
        for _stamp, line in stream.get("values", []):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if parsed.get("request_id") == request_id and "decision" in parsed:
                return parsed
    return None


def mcp_tools(bearer: str) -> list[str]:
    return list(_run(lambda: _list_tools(bearer)))


def mcp_call(bearer: str, name: str, arguments: dict | None = None) -> object:
    return _run(lambda: _call_tool(bearer, name, arguments or {}))


def _run(factory):
    """Run a coroutine on a fresh loop. Jupyter may already have one."""
    box: dict[str, object] = {}

    def worker() -> None:
        try:
            box["value"] = asyncio.run(factory())
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


async def _session(bearer: str):
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {bearer}"}, timeout=60)
    return client, streamable_http_client(MCP, http_client=client), ClientSession


async def _list_tools(bearer: str) -> list[str]:
    client, transport, session_type = await _session(bearer)
    try:
        async with transport as streams:
            async with session_type(streams[0], streams[1]) as session:
                await session.initialize()
                listed = await session.list_tools()
                return [tool.name for tool in listed.tools]
    finally:
        await client.aclose()


async def _call_tool(bearer: str, name: str, arguments: dict) -> object:
    client, transport, session_type = await _session(bearer)
    try:
        async with transport as streams:
            async with session_type(streams[0], streams[1]) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                return _payload(result)
    finally:
        await client.aclose()


def _payload(result: object) -> object:
    if getattr(result, "is_error", False):
        raise SystemExit(f"mcp tool failed: {result}")
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, (dict, list)):
        return structured
    texts = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return texts[0]
    return texts
