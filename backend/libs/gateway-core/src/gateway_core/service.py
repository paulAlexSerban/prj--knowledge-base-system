"""One retrieval service. The API and the MCP server are adapters."""

import logging

from gateway_core.graph_read import LIMIT as GRAPH_LIMIT
from gateway_core.graph_read import GraphReader
from gateway_core.metrics import EMPTY, REQUESTS, RETRIEVAL_SECONDS, ROUTER, TOTAL, permission_group
from gateway_core.recheck import AclSource, apply_recheck
from gateway_core.router import Router
from gateway_core.tools import SNIPPET_END, SNIPPET_START
from knowledge_store.acl import Principal
from knowledge_store.postgres import Hit
from knowledge_store.store import KnowledgeStore
from shared.logging import log_event
from shared.registry import source_payloads
from shared.telemetry import span

LOG = logging.getLogger("gateway")


class GatewayService:
    def __init__(
        self,
        store: KnowledgeStore,
        graph: GraphReader,
        recheck: AclSource | None = None,
        surface: str = "rest",
        router: Router | None = None,
    ) -> None:
        self.store = store
        self.graph = graph
        self.recheck = recheck
        self.surface = surface
        self.router = router if router is not None else Router.from_env()

    def ask(
        self, principal: Principal, query: str, limit: int, request_id: str
    ) -> dict[str, object]:
        """Route one question, then run the same checked retrieval as the explicit tools."""
        with span("router.classify") as current:
            route = self.router.classify(query)
            current.set_attribute("route", route.kind)
            current.set_attribute("source", route.source)
            current.set_attribute("graph", route.graph or "none")
            current.set_attribute("confidence", route.confidence)
        ROUTER.labels(route=route.kind, source=route.source).inc()
        if route.kind == "semantic" or route.graph is None:
            body = self.search(principal, query, limit, request_id)
        elif route.kind == "relational":
            body = self.find_related(
                principal,
                route.graph,
                route.entity_id,
                GRAPH_LIMIT,
                request_id,
                quarter=route.quarter,
                min_arr=route.min_arr,
            )
        else:
            body = self.find_related(
                principal,
                route.graph,
                route.entity_id,
                GRAPH_LIMIT,
                request_id,
                quarter=route.quarter,
                min_arr=route.min_arr,
            )
            object_ids = _object_ids(body["paths"])
            if object_ids:
                hits = self._restricted_search(principal, query, limit, request_id, object_ids)
            else:
                # The graph authorized nothing. Stay empty. A corpus search here
                # would return chunks the path did not name.
                hits = []
            body["results"] = [_public_hit(hit) for hit in hits]
        body["route"] = route.public()
        return body

    def _restricted_search(
        self,
        principal: Principal,
        query: str,
        limit: int,
        request_id: str,
        object_ids: list[str],
    ) -> list[Hit]:
        """Search only the object ids on an authorized roadmap path. Specs are not on that path."""
        with (
            span("retrieval", tool="search", surface=self.surface) as current,
            RETRIEVAL_SECONDS.labels(tool="search", surface=self.surface).time(),
        ):
            # Four times the caller's limit, then one chunk per object. The
            # store multiplies by OVERFETCH again before it queries.
            hits = self.store.search(
                query,
                principal,
                limit=max(limit * 4, limit),
                request_id=request_id,
                object_ids=object_ids,
            )
            hits = self._recheck(hits, principal)
            hits = _one_chunk_per_object(hits)[:limit]
            source = _finish_retrieval(current, hits, limit, request_id, restricted=True)
        self._record("search", principal, request_id, len(hits), source, empty_rate=True)
        return hits

    def search(
        self, principal: Principal, query: str, limit: int, request_id: str
    ) -> dict[str, object]:
        """Hybrid search, HR ACL re-check, then one chunk per object."""
        with (
            span("retrieval", tool="search", surface=self.surface) as current,
            RETRIEVAL_SECONDS.labels(tool="search", surface=self.surface).time(),
        ):
            # Four times the caller's limit, then one chunk per object. The
            # store multiplies by OVERFETCH again before it queries.
            hits = self.store.search(
                query, principal, limit=max(limit * 4, limit), request_id=request_id
            )
            hits = self._recheck(hits, principal)
            hits = _one_chunk_per_object(hits)[:limit]
            source = _finish_retrieval(current, hits, limit, request_id)
        self._record("search", principal, request_id, len(hits), source, empty_rate=True)
        return {"request_id": request_id, "results": [_public_hit(hit) for hit in hits]}

    def get_document(
        self, principal: Principal, chunk_id: int, request_id: str
    ) -> dict[str, object] | None:
        """One authorized chunk. None means missing or the caller cannot read it."""
        with (
            span("retrieval", tool="get_document", surface=self.surface) as current,
            RETRIEVAL_SECONDS.labels(tool="get_document", surface=self.surface).time(),
        ):
            hit = self.store.chunks.get_chunk(chunk_id, principal, request_id)
            if hit is not None:
                kept = self._recheck([hit], principal)
                hit = kept[0] if kept else None
            source = _finish_retrieval(
                current, [] if hit is None else [hit], 1, request_id
            )
        self._record(
            "get_document",
            principal,
            request_id,
            0 if hit is None else 1,
            source,
            empty_rate=False,
        )
        if hit is None:
            return None
        body = _public_hit(hit)
        body["request_id"] = request_id
        return body

    def find_related(
        self,
        principal: Principal,
        kind: str,
        entity_id: str | None,
        limit: int,
        request_id: str,
        quarter: str | None = None,
        min_arr: int | None = None,
    ) -> dict[str, object]:
        """Run one Cypher template, post-check rules, and audit allows and denies."""
        with (
            span("retrieval", tool="find_related", surface=self.surface) as retrieval,
            RETRIEVAL_SECONDS.labels(tool="find_related", surface=self.surface).time(),
        ):
            kind_label = (
                kind if kind in {"teammates", "team", "policies", "roadmap_deals"} else "other"
            )
            with span("graph.related", kind=kind_label) as current:
                accesses = self.graph.related(
                    kind, principal, entity_id, limit, quarter=quarter, min_arr=min_arr
                )
                current.set_attribute("count", sum(1 for item in accesses if item.allowed))
            allows = sum(1 for item in accesses if item.allowed)
            denies = len(accesses) - allows
            with span("audit.write", count=len(accesses)) as current:
                current.set_attribute("allows", allows)
                current.set_attribute("denies", denies)
                self.store.chunks.record_decisions(
                    principal,
                    request_id,
                    [
                        (
                            "allow" if item.allowed else "deny",
                            item.source_id,
                            item.rule,
                            item.object_id,
                        )
                        for item in accesses
                    ],
                )
            paths = [item.path for item in accesses if item.allowed]
            source = next(
                (item.source_id for item in accesses if item.allowed and item.source_id),
                "none",
            )
            retrieval.set_attribute("count", len(paths))
            retrieval.set_attribute("source_id", source)
            retrieval.set_attribute("empty", not paths)
            retrieval.set_attribute("limit", limit)
            retrieval.set_attribute("request_id", request_id)
        # A roadmap path is a sales deal. Counting it keeps the empty rate by source meaningful.
        self._record(
            "find_related",
            principal,
            request_id,
            len(paths),
            source,
            empty_rate=kind == "roadmap_deals",
        )
        return {"request_id": request_id, "kind": kind, "paths": paths}

    def _recheck(self, hits: list[Hit], principal: Principal) -> list[Hit]:
        """Drop HR policy chunks the harness no longer allows. A timeout empties the page."""
        with span("acl.recheck") as current:
            kept, failed = apply_recheck(hits, principal, self.store.contracts, self.recheck)
            current.set_attribute("count", len(kept))
            current.set_attribute("failed", failed)
            return kept

    def _record(
        self,
        tool: str,
        principal: Principal,
        request_id: str,
        count: int,
        source_id: str,
        *,
        empty_rate: bool,
    ) -> None:
        """Increment request counters and write the access-decision log line."""
        result = "empty" if count == 0 else "ok"
        REQUESTS.labels(tool=tool, surface=self.surface, result=result).inc()
        group = permission_group(principal)
        if empty_rate:
            TOTAL.labels(source_id=source_id, permission_group=group).inc()
            if count == 0:
                EMPTY.labels(source_id=source_id, permission_group=group).inc()
        log_event(
            LOG,
            "access decision",
            request_id=request_id,
            principal=principal.subject,
            decision="deny" if count == 0 else "allow",
            tool=tool,
            count=count,
            source_id="" if source_id == "none" else source_id,
        )

    def list_sources(self) -> list[dict[str, object]]:
        """Owner, tier, and last successful sync. No document bodies."""
        payloads = source_payloads(list(self.store.contracts.values()))
        sync = self.store.chunks.last_success()
        for item in payloads:
            item["last_success_at"] = sync.get(str(item["source_id"]))
        return payloads


def _finish_retrieval(
    current, hits: list[Hit], limit: int, request_id: str, *, restricted: bool = False
) -> str:
    """Span attributes for one retrieval. Returns the first hit's source_id or none."""
    source = hits[0].source_id if hits else "none"
    current.set_attribute("count", len(hits))
    current.set_attribute("source_id", source)
    current.set_attribute("empty", not hits)
    current.set_attribute("limit", limit)
    current.set_attribute("request_id", request_id)
    if restricted:
        current.set_attribute("restricted", True)
    return source


def _object_ids(paths: object) -> list[str]:
    """Deal, epic, policy, and person ids from an authorized path. Teams are not chunks."""
    if not isinstance(paths, list):
        return []
    found: list[str] = []
    seen: set[str] = set()
    for path in paths:
        steps = path.get("steps") if isinstance(path, dict) else None
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict) or step.get("type") not in {
                "Deal",
                "Epic",
                "Policy",
                "Person",
            }:
                continue
            object_id = step.get("id")
            if not isinstance(object_id, str) or not object_id or object_id in seen:
                continue
            seen.add(object_id)
            found.append(object_id)
    return found


def _one_chunk_per_object(hits: list[Hit]) -> list[Hit]:
    """Keep the first hit for each source+object. Search ranks chunks, agents get objects."""
    seen: set[tuple[str, str]] = set()
    kept: list[Hit] = []
    for hit in hits:
        key = (hit.source_id, hit.object_id)
        if key in seen:
            continue
        seen.add(key)
        kept.append(hit)
    return kept


def _public_hit(hit: Hit) -> dict[str, object]:
    """API payload. Snippet is wrapped as untrusted content so it cannot instruct the agent."""
    return {
        "chunk_id": hit.chunk_id,
        "source_id": hit.source_id,
        "object_id": hit.object_id,
        "object_type": hit.object_type,
        "version": hit.version,
        "section_path": hit.section_path,
        "snippet": f"{SNIPPET_START}\n{hit.text}\n{SNIPPET_END}",
        "score": hit.score,
        "synced_at": hit.synced_at,
    }
