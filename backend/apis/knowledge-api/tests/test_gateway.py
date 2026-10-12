"""The API stub lists registered sources and refuses a bad registry."""

from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from knowledge_api.app import create_app
from shared.registry import load_registry


def test_sources_match_the_registry() -> None:
    client = TestClient(create_app())
    response = client.get("/v1/sources")
    assert response.status_code == 200
    assert {item["source_id"] for item in response.json()} == {
        "hr",
        "sales",
        "product",
        "engineering",
    }
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["sources"] == 4


def test_unknown_path_is_not_a_metric_label() -> None:
    raw = "/v1/people/ada-lovelace"
    client = TestClient(create_app())
    assert client.get(raw).status_code == 404
    assert client.get("/v1/sources").status_code == 200
    routes = _http_routes()
    assert "unmatched" in routes
    assert "/v1/sources" in routes
    assert raw not in routes


def _http_routes() -> set[str]:
    routes: set[str] = set()
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name == "kbs_http_requests_total":
                route = sample.labels.get("route")
                if route is not None:
                    routes.add(route)
    return routes


def test_registry_refuses_to_start(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CONTRACTS_DIR", str(tmp_path))
    try:
        load_registry()
    except SystemExit as exc:
        assert "refusing to start" in str(exc)
    else:
        raise AssertionError("expected SystemExit")
