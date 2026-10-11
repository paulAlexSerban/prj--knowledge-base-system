"""Load source contracts and fail when one cannot be registered.

Worker bootstrap must call ``load_contracts`` on the registry directory
and must refuse to start a connector when this raises ``ContractLoadError``.
A connector with no YAML file, or with a file missing owner, freshness
tier, or ACL mapping, does not run.
"""

import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from shared.contracts.source_contract import SourceContract


def _default_directory() -> Path:
    """Repo path when this package is installed from the checkout. Docker sets CONTRACTS_DIR."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "infrastructure" / "config" / "sources"
        if candidate.is_dir():
            return candidate
    return here.parents[2] / "infrastructure" / "config" / "sources"


DEFAULT_DIRECTORY = _default_directory()


class ContractLoadError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


def load_contracts(directory: Path) -> list[SourceContract]:
    """Validate every ``*.yaml`` contract in ``directory``.

    ``source_id`` values must be unique across the directory. An empty
    directory is a failure: the gateway does not start with zero sources
    by accident.
    """
    path = Path(directory)
    files = sorted(path.glob("*.yaml"))
    if not files:
        raise ContractLoadError([f"{path}: no source contracts found"])

    contracts: list[SourceContract] = []
    errors: list[str] = []
    seen: dict[str, Path] = {}
    for file in files:
        raw = yaml.safe_load(file.read_text())
        if not isinstance(raw, dict):
            errors.append(f"{file}: contract must be a YAML mapping")
            continue
        try:
            contract = SourceContract.model_validate(raw)
        except ValidationError as exc:
            errors.append(f"{file}: {exc}")
            continue
        previous = seen.get(contract.source_id)
        if previous is not None:
            errors.append(
                f"{file}: duplicate source_id {contract.source_id!r} "
                f"also declared in {previous.name}"
            )
            continue
        seen[contract.source_id] = file
        contracts.append(contract)
    if errors:
        raise ContractLoadError(errors)
    return contracts


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    directory = Path(args[0]) if args else DEFAULT_DIRECTORY
    try:
        contracts = load_contracts(directory)
    except ContractLoadError as exc:
        for line in exc.errors:
            print(line, file=sys.stderr)
        return 1
    print(f"validated {len(contracts)} source contracts from {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
