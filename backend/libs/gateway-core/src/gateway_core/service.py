"""One retrieval service. The API and the MCP server are adapters."""

from gateway_core.graph_read import GraphReader
from gateway_core.tools import SNIPPET_END, SNIPPET_START
from knowledge_store.acl import Principal
from knowledge_store.postgres import Hit
from knowledge_store.store import KnowledgeStore
from shared.registry import source_payloads


class GatewayService:
    def __init__(self, store: KnowledgeStore, graph: GraphReader) -> None:
        self.store = store
        self.graph = graph

    def search(self, principal: Principal, query: str, limit: int, request_id: str) -> dict[str, object]:
        hits = self.store.search(query, principal, limit=limit, request_id=request_id)
        return {"request_id": request_id, "results": [_public_hit(hit) for hit in hits]}

    def get_document(
        self, principal: Principal, chunk_id: int, request_id: str
    ) -> dict[str, object] | None:
        hit = self.store.chunks.get_chunk(chunk_id, principal, request_id)
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
        paths = self.graph.related(kind, principal, entity_id, limit)
        return {"request_id": request_id, "kind": kind, "paths": paths}

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
