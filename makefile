.PHONY: sync contracts-schema contracts-check

sync:
	uv sync

contracts-schema:
	uv run python -m shared.contracts.export_schema

contracts-check: contracts-schema
	uv run python -m shared.contracts.validate infrastructure/config/sources
	uv run pytest shared/contracts/tests
	git diff --exit-code -- shared/contracts/source_contract.schema.json
