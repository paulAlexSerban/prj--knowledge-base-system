from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_common.catalog import guard, register_catalog
from harness_common.db import NotFound


def _client() -> tuple[TestClient, dict[str, int]]:
    counts = {"epic": 2}
    app = FastAPI()
    refresh = register_catalog(
        app,
        service="engineering",
        capabilities=["updated_since"],
        object_types=[{"name": "epic", "id_field": "epic_id", "shape": "record"}],
        native_acls=["project_member", "eng_leadership"],
        counts=lambda: counts,
        ids=lambda: {"epic": [{"id": "epic-1", "version": 1}, {"id": "epic-2", "version": 3}]},
        now=lambda: "2026-06-01T00:00:00.000000Z",
    )

    @app.get("/missing")
    def missing() -> dict[str, str]:
        def boom() -> dict[str, str]:
            raise NotFound("epic-9")

        return guard(boom, refresh)

    return TestClient(app), counts


def test_discover_ids_and_metrics():
    client, _counts = _client()
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "service": "engineering",
        "capability": "updated_since",
    }
    discovered = client.get("/discover").json()
    assert discovered["capabilities"] == ["updated_since"]
    assert discovered["counts"] == {"epic": 2}
    assert discovered["native_acls"] == ["project_member", "eng_leadership"]
    listed = client.get("/ids").json()
    assert listed["ids"]["epic"][1] == {"id": "epic-2", "version": 3}
    assert listed["server_time"] == "2026-06-01T00:00:00.000000Z"
    metrics = client.get("/metrics").text
    assert 'kbs_harness_up{service="engineering"} 1.0' in metrics
    assert 'kbs_harness_objects{object_type="epic",service="engineering"} 2.0' in metrics


def test_guard_maps_not_found():
    client, _counts = _client()
    response = client.get("/missing")
    assert response.status_code == 404
    assert "epic-9" in response.json()["detail"]
