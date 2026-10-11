"""Engineering harness API.

Read side: GET /discover, /changes, /ids, /epics, /epics/{id}, /epics/{id}/acl.
Write side, standing in for engineering staff: /admin/epics.

Capability is `updated_since`. A delete is visible only as an id missing from /ids.
"""

from fastapi import FastAPI, Query, Response
from pydantic import BaseModel, Field

from harness_common.catalog import guard, register_catalog
from harness_engineering.catalog import SEED_AT, epics
from harness_engineering.store import Store

SERVICE = "engineering"
CAPABILITIES = ["updated_since"]
MAX_PAGE = 500


class EpicCreate(BaseModel):
    epic_id: str = Field(pattern=r"^epic-[a-z0-9-]{3,60}$")
    title: str = Field(min_length=1)
    status: str
    product_key: str
    team: str
    native_acl: list[str] = ["project_member"]


class EpicEdit(BaseModel):
    title: str | None = None
    status: str | None = None
    product_key: str | None = None
    team: str | None = None


class AclChange(BaseModel):
    native_acl: list[str]


def seed(store: Store) -> dict[str, int]:
    if not store.is_empty():
        return store.counts()
    for row in epics():
        store.create(
            str(row["epic_id"]),
            str(row["title"]),
            str(row["status"]),
            str(row["product_key"]),
            str(row["team"]),
            list(row["native_acl"]),  # type: ignore[arg-type]
            at=SEED_AT,
        )
    return store.counts()


def create_app(store: Store, seed_if_empty: bool = True) -> FastAPI:
    if seed_if_empty and store.is_empty():
        seed(store)
    app = FastAPI(title="Harness engineering", version="0.2.0")
    refresh = register_catalog(
        app,
        service=SERVICE,
        capabilities=CAPABILITIES,
        object_types=[{"name": "epic", "id_field": "epic_id", "shape": "record"}],
        native_acls=["project_member", "eng_leadership"],
        counts=store.counts,
        ids=store.ids,
        now=store.now,
    )

    @app.get("/changes")
    def changes(
        updated_since: str | None = None,
        page_token: str | None = None,
        limit: int = Query(100, ge=1, le=MAX_PAGE),
    ) -> dict[str, object]:
        """Epics with updated_at at or after `updated_since`, oldest first."""
        items, token = guard(lambda: store.changes(updated_since, page_token, limit), refresh)
        return {
            "service": SERVICE,
            "capability": CAPABILITIES[0],
            "updated_since": updated_since,
            "server_time": store.now(),
            "next_page_token": token,
            "items": items,
        }

    @app.get("/epics")
    def list_epics() -> dict[str, object]:
        return {"items": [store.get(row["id"]) for row in store.ids()["epic"]]}

    @app.get("/epics/{epic_id}")
    def epic(epic_id: str) -> dict[str, object]:
        return guard(lambda: store.get(epic_id), refresh)

    @app.get("/epics/{epic_id}/acl")
    def epic_acl(epic_id: str) -> dict[str, object]:
        found = guard(lambda: store.get(epic_id), refresh)
        return {"id": epic_id, "native_acl": found["native_acl"]}

    @app.post("/admin/epics", status_code=201)
    def admin_create(payload: EpicCreate) -> dict[str, object]:
        return guard(lambda: store.create(**payload.model_dump()), refresh)

    @app.patch("/admin/epics/{epic_id}")
    def admin_edit(epic_id: str, payload: EpicEdit) -> dict[str, object]:
        fields = payload.model_dump(exclude_unset=True)
        return guard(lambda: store.update(epic_id, fields), refresh)

    @app.put("/admin/epics/{epic_id}/acl")
    def admin_acl(epic_id: str, payload: AclChange) -> dict[str, object]:
        return guard(lambda: store.set_acl(epic_id, payload.native_acl), refresh)

    @app.delete("/admin/epics/{epic_id}", status_code=204)
    def admin_delete(epic_id: str) -> Response:
        guard(lambda: store.delete(epic_id), refresh)
        return Response(status_code=204)

    @app.post("/admin/reset")
    def admin_reset() -> dict[str, object]:
        store.reset()
        counts = seed(store)
        refresh()
        return {"status": "reset", "counts": counts}

    return app
