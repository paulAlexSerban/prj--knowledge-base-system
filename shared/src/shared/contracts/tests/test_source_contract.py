"""Registration gates for source contracts."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.contracts.export_schema import SCHEMA_PATH, render_schema
from shared.contracts.source_contract import SourceContract
from shared.contracts.validate import ContractLoadError, load_contracts, main


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "infrastructure" / "config" / "sources").is_dir():
            return parent
    raise RuntimeError("repository root not found")


REPO_ROOT = _repo_root()
SOURCES = REPO_ROOT / "infrastructure" / "config" / "sources"


def _valid(**overrides: object) -> dict:
    contract = {
        "source_id": "hr",
        "display_name": "People and policies",
        "owner": {"team": "People", "contact": "people-platform@example.com"},
        "freshness_tier": "P0",
        "freshness_target_seconds": 30,
        "sensitivity": "high",
        "capabilities": ["updated_since"],
        "acl_recheck_at_read": True,
        "in_scope_fields": ["policy_id", "title"],
        "excluded_fields": ["salary"],
        "acl_mapping": [
            {
                "native": "hr_employee",
                "attributes": {"department": "people", "group": "employees"},
            }
        ],
    }
    contract.update(overrides)
    return contract


def test_registry_contracts_load() -> None:
    contracts = load_contracts(SOURCES)
    assert {item.source_id for item in contracts} == {
        "hr",
        "sales",
        "product",
        "engineering",
    }
    hr = next(item for item in contracts if item.source_id == "hr")
    assert hr.freshness_tier.value == "P0"
    assert hr.acl_recheck_at_read is True
    assert hr.acl_mapping


def test_committed_schema_matches_model() -> None:
    assert json.loads(SCHEMA_PATH.read_text()) == json.loads(render_schema())


def test_cli_accepts_registry() -> None:
    assert main([str(SOURCES)]) == 0


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"owner": None}, "owner"),
        ({"freshness_tier": None}, "freshness_tier"),
        ({"acl_mapping": []}, "acl_mapping"),
        ({"acl_mapping": None}, "acl_mapping"),
        (
            {
                "acl_mapping": [
                    {"native": "hr_employee", "attributes": {"salary": "hidden"}}
                ]
            },
            "attributes",
        ),
        (
            {
                "sensitivity": "high",
                "acl_recheck_at_read": False,
            },
            "acl_recheck_at_read",
        ),
        (
            {
                "freshness_tier": "P0",
                "freshness_target_seconds": 30,
                "capabilities": ["snapshot_diff"],
                "sensitivity": "low",
                "acl_recheck_at_read": False,
            },
            "snapshot_diff",
        ),
        ({"in_scope_fields": ["title", "salary"]}, "salary"),
        (
            {
                "in_scope_fields": ["title", "body"],
                "excluded_fields": ["body"],
            },
            "excluded_fields",
        ),
    ],
)
def test_contract_rejects_incomplete_registration(
    overrides: dict, message: str
) -> None:
    with pytest.raises(ValidationError) as raised:
        SourceContract.model_validate(_valid(**overrides))
    assert message in str(raised.value)


def test_duplicate_source_id_rejected(tmp_path: Path) -> None:
    first = _valid(source_id="hr")
    second = _valid(source_id="hr", display_name="Other")
    (tmp_path / "a.yaml").write_text(_dump(first))
    (tmp_path / "b.yaml").write_text(_dump(second))
    with pytest.raises(ContractLoadError) as raised:
        load_contracts(tmp_path)
    assert "duplicate source_id" in str(raised.value)


def test_cli_rejects_invalid_directory(tmp_path: Path) -> None:
    assert main([str(tmp_path)]) == 1


def _dump(payload: dict) -> str:
    import yaml

    return yaml.safe_dump(payload)
