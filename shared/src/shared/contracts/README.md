# Source contracts

A connector starts only when its YAML contract validates. Worker bootstrap calls `load_contracts` and refuses to start when that raises `ContractLoadError`.

The Pydantic model in `source_contract.py` is the source of truth. `source_contract.schema.json` is generated from it. Contracts themselves live in `infrastructure/config/sources/`.

## Registration rules

| Field | Rule |
| ----- | ---- |
| `owner.team`, `owner.contact` | Required. Contact is an email. |
| `freshness_tier` | `P0`, `P1`, or `P2`. |
| `freshness_target_seconds` | At most 30 / 300 / 86400 for P0 / P1 / P2. |
| `capabilities` | At least one of `webhook`, `cursor_feed`, `updated_since`, `snapshot_diff`. P0 cannot be `snapshot_diff` alone. |
| `acl_mapping` | At least one rule. Each rule requires `group`, `clearance` (`public`, `internal`, `confidential`, or `restricted`), and `department`. `department: "*"` means every department. `region` is optional and is not an access predicate. |
| `sensitivity` | `high` requires `acl_recheck_at_read: true`. |
| `in_scope_fields` | Required. Must not include `salary`, `compensation`, `ssn`, or `national_id`. |
| `excluded_fields` | Disjoint from `in_scope_fields`. |

`source_id` values must be unique across the directory. Unknown fields are rejected.

## Commands

From the repository root:

```bash
uv run python -m shared.contracts.validate infrastructure/config/sources
uv run python -m shared.contracts.export_schema
make contracts-check
```

`make contracts-check` validates the registry, runs `tests/test_source_contract.py`, and fails if the committed JSON Schema drifted. After changing `SourceContract`, regenerate the schema and commit it.

## Layout

| File | Role |
| ---- | ---- |
| `source_contract.py` | `SourceContract`, `AclRule`, and the cross-field rules. |
| `validate.py` | `load_contracts` and the CLI. |
| `export_schema.py` | Writes `source_contract.schema.json`. |
| `source_contract.schema.json` | Generated schema. Do not edit by hand. |
| `tests/test_source_contract.py` | The four real contracts pass. Incomplete contracts fail. |
