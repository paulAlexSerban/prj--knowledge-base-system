"""HR harness API.

Read side (what the connector uses):
  GET /discover, /changes, /ids, /people, /people/{id}, /people/{id}/acl,
  /policies, /policies/{id}, /policies/{id}/content, /policies/{id}/acl

Write side (what the mutation script uses, standing in for HR staff):
  /admin/...

Capability is `updated_since` plus `snapshot_diff`. There is no webhook and no delete feed.
A delete is visible only as an id missing from /ids.
"""

import time
from collections.abc import Callable
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Response
from hr_generate import generate_people, generate_policies
from hr_seed import seed
from hr_store import Conflict, Invalid, NotFound, Store, utc_now
from prometheus_client import Gauge, make_asgi_app
from pydantic import BaseModel, Field

SERVICE = "hr"
CAPABILITIES = ["updated_since", "snapshot_diff"]
MAX_PAGE = 500
MAX_SEED = 50_000

UP = Gauge("kbs_harness_up", "1 when a harness service is serving", ["service"])
OBJECTS = Gauge("kbs_harness_objects", "Live objects in the source", ["service", "object_type"])


class PolicyCreate(BaseModel):
    policy_id: str = Field(pattern=r"^pol-[a-z0-9-]{3,60}$")
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    region: str = Field(min_length=2)
    format: str = "markdown"
    native_acl: list[str] = ["hr_employee"]
    body: str = Field(min_length=1)


class PolicyEdit(BaseModel):
    title: str | None = None
    body: str | None = None


class AclChange(BaseModel):
    native_acl: list[str]


class PersonCreate(BaseModel):
    person_id: str = Field(pattern=r"^emp-[0-9]{3,}$")
    email: str
    full_name: str
    team: str
    role: str
    region: str
    manager_id: str | None = None
    native_acl: list[str] = ["hr_employee"]


class SeedLoad(BaseModel):
    kind: Literal["person", "policy"]
    count: int = Field(ge=1, le=MAX_SEED)
    seed: int = 1
    replace: bool = False


class PersonEdit(BaseModel):
    team: str | None = None
    role: str | None = None
    region: str | None = None
    manager_id: str | None = None
    native_acl: list[str] | None = None


def create_app(store: Store, seed_if_empty: bool = True) -> FastAPI:
    if seed_if_empty and store.is_empty():
        seed(store)
    app = FastAPI(title="Harness HR", version="0.2.0")
    UP.labels(service=SERVICE).set(1)

    def refresh_gauges() -> None:
        for kind, count in store.counts().items():
            OBJECTS.labels(service=SERVICE, object_type=kind).set(count)

    refresh_gauges()

    def guard[T](action: Callable[[], T]) -> T:
        try:
            result = action()
        except NotFound as error:
            raise HTTPException(404, f"not found: {error}") from error
        except Conflict as error:
            raise HTTPException(409, f"conflict: {error}") from error
        except Invalid as error:
            raise HTTPException(422, str(error)) from error
        refresh_gauges()
        return result

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "service": SERVICE, "capability": CAPABILITIES[0]}

    @app.get("/discover")
    def discover() -> dict[str, object]:
        return {
            "service": SERVICE,
            "capabilities": CAPABILITIES,
            "object_types": [
                {"name": "person", "id_field": "person_id", "shape": "record"},
                {
                    "name": "policy",
                    "id_field": "policy_id",
                    "shape": "document",
                    "formats": ["markdown", "pdf"],
                },
            ],
            "native_acls": ["hr_employee", "hr_policy_admin"],
            "counts": store.counts(),
        }

    @app.get("/changes")
    def changes(
        updated_since: str | None = None,
        page_token: str | None = None,
        limit: int = Query(100, ge=1, le=MAX_PAGE),
    ) -> dict[str, object]:
        """Objects with updated_at at or after `updated_since`, oldest first.

        Inclusive on purpose, so the same-timestamp neighbour is never skipped. The
        connector dedups on version and content hash. Keep `server_time` as the next
        watermark only after the last page.
        """
        items, token = guard(lambda: store.changes(updated_since, page_token, limit))
        return {
            "service": SERVICE,
            "capability": CAPABILITIES[0],
            "updated_since": updated_since,
            "server_time": store.now(),
            "next_page_token": token,
            "items": items,
        }

    @app.get("/ids")
    def ids() -> dict[str, object]:
        """Every live id and version. The reconciliation sweep diffs this against the index."""
        return {"service": SERVICE, "server_time": store.now(), "ids": store.ids()}

    @app.get("/people")
    def people() -> dict[str, object]:
        return {"items": store.list_people()}

    @app.get("/people/{person_id}")
    def person(person_id: str) -> dict[str, object]:
        return guard(lambda: store.get_person(person_id))

    @app.get("/people/{person_id}/acl")
    def person_acl(person_id: str) -> dict[str, object]:
        record = guard(lambda: store.get_person(person_id))
        return {"id": person_id, "native_acl": record["native_acl"], "region": record["region"]}

    @app.get("/policies")
    def policies() -> dict[str, object]:
        return {"items": store.list_policies()}

    @app.get("/policies/{policy_id}")
    def policy(policy_id: str) -> dict[str, object]:
        return guard(lambda: store.get_policy(policy_id))

    @app.get("/policies/{policy_id}/content")
    def policy_content(policy_id: str) -> Response:
        data, media = guard(lambda: store.policy_content(policy_id))
        return Response(content=data, media_type=media)

    @app.get("/policies/{policy_id}/acl")
    def policy_acl(policy_id: str) -> dict[str, object]:
        """Live ACL read. HR is high sensitivity, so the gateway re-checks this at read time."""
        record = guard(lambda: store.get_policy(policy_id))
        return {"id": policy_id, "native_acl": record["native_acl"], "region": record["region"]}

    # Admin: simulated edits in the source system.

    @app.post("/admin/policies", status_code=201)
    def admin_create_policy(payload: PolicyCreate) -> dict[str, object]:
        return guard(
            lambda: store.create_policy(
                payload.policy_id,
                payload.title,
                payload.category,
                payload.region,
                payload.format,
                payload.native_acl,
                payload.body,
            )
        )

    @app.put("/admin/policies/{policy_id}")
    def admin_edit_policy(policy_id: str, payload: PolicyEdit) -> dict[str, object]:
        return guard(lambda: store.edit_policy(policy_id, payload.body, payload.title))

    @app.put("/admin/policies/{policy_id}/acl")
    def admin_policy_acl(policy_id: str, payload: AclChange) -> dict[str, object]:
        return guard(lambda: store.set_policy_acl(policy_id, payload.native_acl))

    @app.delete("/admin/policies/{policy_id}", status_code=204)
    def admin_delete_policy(policy_id: str) -> Response:
        guard(lambda: store.delete_policy(policy_id))
        return Response(status_code=204)

    @app.post("/admin/people", status_code=201)
    def admin_create_person(payload: PersonCreate) -> dict[str, object]:
        if any(p["person_id"] == payload.person_id for p in store.list_people()):
            raise HTTPException(409, f"conflict: {payload.person_id}")
        return guard(lambda: store.put_person(payload.model_dump()))

    @app.patch("/admin/people/{person_id}")
    def admin_edit_person(person_id: str, payload: PersonEdit) -> dict[str, object]:
        return guard(lambda: store.update_person(person_id, payload.model_dump(exclude_unset=True)))

    @app.delete("/admin/people/{person_id}", status_code=204)
    def admin_delete_person(person_id: str) -> Response:
        guard(lambda: store.delete_person(person_id))
        return Response(status_code=204)

    @app.post("/admin/seed")
    def admin_seed(payload: SeedLoad) -> dict[str, object]:
        """Generate `count` people or policies with Faker and insert them.

        The notebook calls this. Generation stays in the service so a 10,000 row load
        does not ship bodies over HTTP. `replace` drops existing rows of that kind first.
        """
        started = time.perf_counter()
        if payload.replace:
            store.clear_kind(payload.kind)
        start = store.next_number(payload.kind)
        if payload.kind == "person":
            rows = generate_people(payload.count, payload.seed, start)
        else:
            rows = generate_policies(payload.count, payload.seed, start)
        inserted = guard(lambda: store.bulk_insert(payload.kind, rows))
        return {
            "kind": payload.kind,
            "inserted": inserted,
            "seed": payload.seed,
            "counts": store.counts(),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }

    @app.post("/admin/reset")
    def admin_reset() -> dict[str, object]:
        """Return the source to its seed state so the demo can be repeated."""
        store.reset()
        counts = seed(store)
        refresh_gauges()
        return {"status": "reset", "counts": counts, "at": utc_now().isoformat()}

    app.mount("/metrics", make_asgi_app())
    return app
