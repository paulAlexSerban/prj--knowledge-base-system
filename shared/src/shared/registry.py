"""Load the source-contract registry or refuse to start."""

import os
from pathlib import Path

from shared.contracts.source_contract import SourceContract
from shared.contracts.validate import DEFAULT_DIRECTORY, ContractLoadError, load_contracts


def contracts_directory() -> Path:
    return Path(os.environ.get("CONTRACTS_DIR", DEFAULT_DIRECTORY))


def load_registry() -> list[SourceContract]:
    """Return validated contracts. Exit if the registry is missing or invalid."""
    try:
        return load_contracts(contracts_directory())
    except ContractLoadError as exc:
        raise SystemExit(f"refusing to start without a valid contract registry: {exc}") from exc


def source_payloads(contracts: list[SourceContract]) -> list[dict[str, object]]:
    return [
        {
            "source_id": contract.source_id,
            "display_name": contract.display_name,
            "freshness_tier": contract.freshness_tier.value,
            "sensitivity": contract.sensitivity.value,
            "capabilities": [item.value for item in contract.capabilities],
            "owner": contract.owner.team,
        }
        for contract in contracts
    ]
