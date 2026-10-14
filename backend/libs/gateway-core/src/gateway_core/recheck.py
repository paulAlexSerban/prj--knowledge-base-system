"""Ask HR for the current policy ACL before returning a chunk.

Search already applied the indexed ACL. This check catches a change the worker
has not applied yet. A timeout or a bad response fails closed and the search
returns nothing. A missing policy is dropped on its own. People records are
not re-checked.
"""

import json
import urllib.error
import urllib.request

from gateway_core.metrics import RECHECK_FAILURES, RECHECK_SECONDS
from knowledge_store.acl import Principal, allows, map_acl
from knowledge_store.postgres import Hit
from shared.contracts.source_contract import SourceContract
from shared.telemetry import client_span, peer_host, trace_headers


class RecheckFailed(Exception):
    """The source did not answer. The caller must return no chunks."""


class AclSource:
    def current_acl(self, source_id: str, object_type: str, object_id: str) -> list[str] | None:
        """Native ACL now. None means the object is gone."""
        raise NotImplementedError


class HttpAclRecheck(AclSource):
    def __init__(self, base_url: str, timeout: float = 2.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def current_acl(self, source_id: str, object_type: str, object_id: str) -> list[str] | None:
        if not self.base_url:
            raise RecheckFailed("no harness url")
        url = f"{self.base_url}/policies/{object_id}/acl"
        peer = peer_host(self.base_url) or "harness-hr"
        try:
            with client_span(peer, "acl.recheck.http") as current:
                request = urllib.request.Request(url, headers=trace_headers())
                try:
                    with urllib.request.urlopen(request, timeout=self.timeout) as response:
                        current.set_attribute("http.status_code", response.status)
                        body = json.loads(response.read().decode())
                except urllib.error.HTTPError as exc:
                    current.set_attribute("http.status_code", exc.code)
                    if exc.code == 404:
                        return None
                    raise
            values = body["native_acl"]
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise RecheckFailed(str(exc.code)) from exc
        except (
            OSError,
            urllib.error.URLError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
        ) as exc:
            raise RecheckFailed(str(exc)) from exc
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise RecheckFailed("acl shape")
        return values


def apply_recheck(
    hits: list[Hit],
    principal: Principal,
    contracts: dict[str, SourceContract],
    source: AclSource | None,
) -> tuple[list[Hit], bool]:
    """Drop hits the source no longer allows. One failed check drops the page.

    The bool is true when the harness did not answer and the page was emptied.
    """
    if source is None:
        return hits, False
    verdict: dict[tuple[str, str], bool] = {}
    kept: list[Hit] = []
    for hit in hits:
        contract = contracts.get(hit.source_id)
        if contract is None or not contract.acl_recheck_at_read or hit.object_type != "policy":
            kept.append(hit)
            continue
        key = (hit.source_id, hit.object_id)
        if key not in verdict:
            try:
                verdict[key] = _still_allowed(source, contract, principal, hit)
            except RecheckFailed:
                RECHECK_FAILURES.labels(source_id=hit.source_id).inc()
                return [], True
        if verdict[key]:
            kept.append(hit)
    return kept, False


def _still_allowed(
    source: AclSource, contract: SourceContract, principal: Principal, hit: Hit
) -> bool:
    with RECHECK_SECONDS.labels(source_id=hit.source_id).time():
        native = source.current_acl(hit.source_id, hit.object_type, hit.object_id)
    if native is None:
        return False
    view = map_acl(native, contract)
    allowed, _rule = allows(view, principal)
    return allowed
