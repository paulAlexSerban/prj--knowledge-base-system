"""The API stub lists registered sources and refuses a bad registry."""

from fastapi.testclient import TestClient

from backend.apis.main import create_app
from backend.apis.registry import load_registry


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


def test_registry_refuses_to_start(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CONTRACTS_DIR", str(tmp_path))
    try:
        load_registry()
    except SystemExit as exc:
        assert "refusing to start" in str(exc)
    else:
        raise AssertionError("expected SystemExit")
