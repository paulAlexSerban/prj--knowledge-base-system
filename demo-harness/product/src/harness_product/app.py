"""Product harness API.

Read side: GET /discover, /ids, /specs, /specs/{id}, /specs/{id}/content, /specs/{id}/acl.
Write side, standing in for product staff: /admin/specs. Each create, edit, and
delete posts a pointer at WEBHOOK_URL. The post is best-effort.

Capability is `webhook`. There is no change listing. /ids is the backstop.
"""

import os

from fastapi import FastAPI, Response
from pydantic import BaseModel, Field

from harness_common.catalog import guard, register_catalog
from harness_product.catalog import SEED_AT, specs
from harness_product.notify import Notifier
from harness_product.store import Store

SERVICE = "product"
CAPABILITIES = ["webhook"]


class SpecCreate(BaseModel):
    spec_id: str = Field(pattern=r"^spec-[a-z0-9-]{3,60}$")
    title: str = Field(min_length=1)
    product_key: str
    body: str = Field(min_length=1)
    native_acl: list[str] = ["product_org"]


class SpecEdit(BaseModel):
    title: str | None = None
    body: str | None = None


class AclChange(BaseModel):
    native_acl: list[str]


def seed(store: Store) -> dict[str, int]:
    if not store.is_empty():
        return store.counts()
    for row in specs():
        store.create(
            str(row["spec_id"]),
            str(row["title"]),
            str(row["product_key"]),
            str(row["body"]),
            list(row["native_acl"]),  # type: ignore[arg-type]
            at=SEED_AT,
        )
    return store.counts()


def create_app(
    store: Store,
    notifier: Notifier | None = None,
    seed_if_empty: bool = True,
) -> FastAPI:
    if seed_if_empty and store.is_empty():
        seed(store)
    if notifier is None:
        notifier = Notifier(os.environ.get("WEBHOOK_URL"))
    app = FastAPI(title="Harness product", version="0.2.0")
    refresh = register_catalog(
        app,
        service=SERVICE,
        capabilities=CAPABILITIES,
        object_types=[
            {"name": "spec", "id_field": "spec_id", "shape": "document", "formats": ["markdown"]}
        ],
        native_acls=["product_org"],
        counts=store.counts,
        ids=store.ids,
        now=store.now,
    )

    @app.get("/specs")
    def list_specs() -> dict[str, object]:
        return {"items": store.list_specs()}

    @app.get("/specs/{spec_id}")
    def spec(spec_id: str) -> dict[str, object]:
        return guard(lambda: store.get(spec_id), refresh)

    @app.get("/specs/{spec_id}/content")
    def spec_content(spec_id: str) -> Response:
        data, media = guard(lambda: store.content(spec_id), refresh)
        return Response(content=data, media_type=media)

    @app.get("/specs/{spec_id}/acl")
    def spec_acl(spec_id: str) -> dict[str, object]:
        found = guard(lambda: store.get(spec_id), refresh)
        return {"id": spec_id, "native_acl": found["native_acl"]}

    @app.post("/admin/specs", status_code=201)
    def admin_create(payload: SpecCreate) -> dict[str, object]:
        created = guard(lambda: store.create(**payload.model_dump()), refresh)
        notifier.notify(str(created["spec_id"]), "create")
        return created

    @app.put("/admin/specs/{spec_id}")
    def admin_edit(spec_id: str, payload: SpecEdit) -> dict[str, object]:
        before = guard(lambda: store.get(spec_id), refresh)
        edited = guard(lambda: store.edit(spec_id, body=payload.body, title=payload.title), refresh)
        if edited["version"] != before["version"]:
            notifier.notify(spec_id, "update")
        return edited

    @app.put("/admin/specs/{spec_id}/acl")
    def admin_acl(spec_id: str, payload: AclChange) -> dict[str, object]:
        before = guard(lambda: store.get(spec_id), refresh)
        changed = guard(lambda: store.set_acl(spec_id, payload.native_acl), refresh)
        if changed["version"] != before["version"]:
            notifier.notify(spec_id, "update")
        return changed

    @app.delete("/admin/specs/{spec_id}", status_code=204)
    def admin_delete(spec_id: str) -> Response:
        guard(lambda: store.delete(spec_id), refresh)
        notifier.notify(spec_id, "delete")
        return Response(status_code=204)

    @app.post("/admin/reset")
    def admin_reset() -> dict[str, object]:
        store.reset()
        counts = seed(store)
        refresh()
        for row in store.ids()["spec"]:
            notifier.notify(str(row["id"]), "create")
        return {"status": "reset", "counts": counts}

    return app
