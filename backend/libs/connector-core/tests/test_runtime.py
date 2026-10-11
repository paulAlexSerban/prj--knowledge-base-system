from pathlib import Path

import pytest

from connector_core.runtime import select_source
from shared.contracts.validate import load_contracts


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "infrastructure" / "config" / "sources").is_dir():
            return parent
    raise RuntimeError("repository root not found")


def test_select_source_returns_the_named_contract() -> None:
    contracts = load_contracts(_repo_root() / "infrastructure" / "config" / "sources")
    assert select_source(contracts, "hr").source_id == "hr"


def test_unknown_source_refuses_to_start() -> None:
    with pytest.raises(SystemExit, match="sales contract"):
        select_source([], "sales")
