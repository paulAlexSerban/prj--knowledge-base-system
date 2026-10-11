"""HR client: stable person JSON, no retry on 4xx, retry on a dead connection."""

import httpx
import pytest

from connector_core.base import ConnectorError
from connector_hr.client import HrConnector, canonical_record
from connector_hr.worker import select_hr
from shared.registry import load_registry


def test_canonical_record_sorts_keys_and_acl_values() -> None:
    raw = canonical_record({"b": 1, "native_acl": ["z", "a"], "a": "x"})
    assert raw == b'{"a":"x","b":1,"native_acl":["a","z"]}'


def test_client_does_not_retry_a_4xx() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, text="missing")

    connector = HrConnector(
        httpx.Client(transport=httpx.MockTransport(handler), base_url="http://hr"),
        backoff=0,
    )
    with pytest.raises(ConnectorError, match="404"):
        connector.discover()
    assert calls["n"] == 1


def test_client_retries_a_transport_error() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"ok": True})

    connector = HrConnector(
        httpx.Client(transport=httpx.MockTransport(handler), base_url="http://hr"),
        backoff=0,
    )
    assert connector.discover() == {"ok": True}
    assert calls["n"] == 3


def test_missing_hr_contract_stops_the_process() -> None:
    contracts = [item for item in load_registry() if item.source_id != "hr"]
    with pytest.raises(SystemExit, match="hr contract"):
        select_hr(contracts)
