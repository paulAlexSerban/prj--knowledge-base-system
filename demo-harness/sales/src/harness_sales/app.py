"""Sales harness API.

Read side: GET /discover, /changes?cursor=, /ids, /deals, /deals/{id}, /deals/{id}/acl.
Write side, standing in for sales staff: /admin/deals.

Capability is `cursor_feed`. Deletes are events on the feed. /ids remains the backstop.
"""

from fastapi import FastAPI, Query, Response
from pydantic import BaseModel, Field

from harness_common.catalog import guard, register_catalog
from harness_sales.catalog import SEED_AT, deals
from harness_sales.store import Store

SERVICE = "sales"
CAPABILITIES = ["cursor_feed"]
MAX_PAGE = 500


class DealCreate(BaseModel):
    deal_id: str = Field(pattern=r"^deal-[a-z0-9-]{3,60}$")
    account_id: str = Field(pattern=r"^acc-[a-z0-9-]{3,40}$")
    account_name: str = Field(min_length=1)
    arr: int = Field(ge=0)
    quarter: str
    stage: str
    owner_email: str
    product_key: str
    epic_id: str | None = None
    native_acl: list[str] = ["account_team"]


class DealEdit(BaseModel):
    account_name: str | None = None
    arr: int | None = Field(default=None, ge=0)
    quarter: str | None = None
    stage: str | None = None
    owner_email: str | None = None
    product_key: str | None = None
    epic_id: str | None = None


class AclChange(BaseModel):
    native_acl: list[str]


def seed(store: Store) -> dict[str, int]:
    if not store.is_empty():
        return store.counts()
    for row in deals():
        store.create(**row, at=SEED_AT)  # type: ignore[arg-type]
    return store.counts()


def create_app(store: Store, seed_if_empty: bool = True) -> FastAPI:
    if seed_if_empty and store.is_empty():
        seed(store)
    app = FastAPI(title="Harness sales", version="0.2.0")
    refresh = register_catalog(
        app,
        service=SERVICE,
        capabilities=CAPABILITIES,
        object_types=[{"name": "deal", "id_field": "deal_id", "shape": "record"}],
        native_acls=["account_team", "sales_leadership"],
        counts=store.counts,
        ids=store.ids,
        now=store.now,
    )

    @app.get("/changes")
    def changes(
        cursor: str | None = None,
        limit: int = Query(100, ge=1, le=MAX_PAGE),
    ) -> dict[str, object]:
        """Change log after `cursor`, oldest first. `next_cursor` is the resume point."""
        items, next_cursor, more = guard(lambda: store.changes(cursor, limit), refresh)
        return {
            "service": SERVICE,
            "capability": CAPABILITIES[0],
            "cursor": cursor or "0",
            "next_cursor": next_cursor,
            "more": more,
            "server_time": store.now(),
            "items": items,
        }

    @app.get("/deals")
    def list_deals() -> dict[str, object]:
        return {"items": store.list_deals()}

    @app.get("/deals/{deal_id}")
    def deal(deal_id: str) -> dict[str, object]:
        return guard(lambda: store.get(deal_id), refresh)

    @app.get("/deals/{deal_id}/acl")
    def deal_acl(deal_id: str) -> dict[str, object]:
        found = guard(lambda: store.get(deal_id), refresh)
        return {"id": deal_id, "native_acl": found["native_acl"]}

    @app.post("/admin/deals", status_code=201)
    def admin_create(payload: DealCreate) -> dict[str, object]:
        return guard(lambda: store.create(**payload.model_dump()), refresh)

    @app.patch("/admin/deals/{deal_id}")
    def admin_edit(deal_id: str, payload: DealEdit) -> dict[str, object]:
        fields = payload.model_dump(exclude_unset=True)
        return guard(lambda: store.update(deal_id, fields), refresh)

    @app.put("/admin/deals/{deal_id}/acl")
    def admin_acl(deal_id: str, payload: AclChange) -> dict[str, object]:
        return guard(lambda: store.set_acl(deal_id, payload.native_acl), refresh)

    @app.delete("/admin/deals/{deal_id}", status_code=204)
    def admin_delete(deal_id: str) -> Response:
        guard(lambda: store.delete(deal_id), refresh)
        return Response(status_code=204)

    @app.post("/admin/reset")
    def admin_reset() -> dict[str, object]:
        store.reset()
        counts = seed(store)
        refresh()
        return {"status": "reset", "counts": counts}

    return app
