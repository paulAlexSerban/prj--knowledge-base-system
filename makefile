.PHONY: contracts-schema contracts-check

export PYTHONPATH := .

contracts-schema:
	python -m shared.contracts.export_schema

contracts-check: contracts-schema
	python -m shared.contracts.validate infrastructure/config/sources
	python -m pytest shared/contracts/tests
	git diff --exit-code -- shared/contracts/source_contract.schema.json
