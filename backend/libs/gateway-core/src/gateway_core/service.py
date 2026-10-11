"""One retrieval service. The API and the MCP server are adapters."""

import logging

from gateway_core.graph_read import GraphReader
from gateway_core.metrics import EMPTY, REQUESTS, RETRIEVAL_SECONDS, TOTAL, permission_group
from gateway_core.recheck import AclSource, apply_recheck
from gateway_core.tools import SNIPPET_END, SNIPPET_START
from knowledge_store.acl import Principal
from knowledge_store.postgres import Hit
from knowledge_store.store import KnowledgeStore
from shared.logging import log_event
from shared.registry import source_payloads

LOG = logging.getLogger("gateway")


class GatewayService:
    def __init__(
        self,
        store: KnowledgeStore,
        graph: GraphReader,
        recheck: AclSource | None = None,
        surface: str = "rest",
    ) -> None:
        self.store = store
        self.graph = graph
        self.recheck = recheck
        self.surface = surface

    def search(
        self, principal: Principal, query: str, limit: int, request_id: str
    ) -> dict[str, object]:
        with RETRIEVAL_SECONDS.labels(tool="search", surface=self.surface).time():
            hits = self.store.search(query, principal, limit=limit, request_id=request_id)
            hits = apply_recheck(hits, principal, self.store.contracts, self.recheck)
        source = hits[0].source_id if hits else "none"
        self._record("search", principal, request_id, len(hits), source, empty_rate=True)
        return {"request_id": request_id, "results": [_public_hit(hit) for hit in hits]}

    def get_document(
        self, principal: Principal, chunk_id: int, request_id: str
    ) -> dict[str, object] | None:
        with RETRIEVAL_SECONDS.labels(tool="get_document", surface=self.surface).time():
            hit = self.store.chunks.get_chunk(chunk_id, principal, request_id)
            if hit is not None:
                kept = apply_recheck([hit], principal, self.store.contracts, self.recheck)
                hit = kept[0] if kept else None
        source = "none" if hit is None else hit.source_id
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
    ) -> dict[str, object]:
        with RETRIEVAL_SECONDS.labels(tool="find_related", surface=self.surface).time():
            paths = self.graph.related(kind, principal, entity_id, limit)
        self._record("find_related", principal, request_id, len(paths), "none", empty_rate=False)
        return {"request_id": request_id, "kind": kind, "paths": paths}

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
        payloads = source_payloads(list(self.store.contracts.values()))
        sync = self.store.chunks.last_success()
        for item in payloads:
            item["last_success_at"] = sync.get(str(item["source_id"]))
        return payloads


def _public_hit(hit: Hit) -> dict[str, object]:
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
