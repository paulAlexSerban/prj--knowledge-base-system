# ADR 0017: A YAML contract is the registration gate

Status: accepted for the POC.

## Context

A connector with no owner, no freshness tier, or no permission mapping fails quietly. Stale answers show up later, as a user complaint. The gate has to fail at startup, not at query time.

## Decision

Each source has one YAML file in `infrastructure/config/sources/`. The Pydantic model `SourceContract` is the source of truth. `load_contracts` raises `ContractLoadError` on an invalid file. `load_registry` calls that at startup, and a connector, the worker, and the gateway refuse to start when it raises. Required: `owner.team`, `owner.contact`, `freshness_tier` (`P0`, `P1`, or `P2`), at least one capability, and at least one `acl_mapping` rule with `group`, `clearance`, and `department`. `department: "*"` means every department. High sensitivity requires `acl_recheck_at_read`. `in_scope_fields` must not include `salary`, `compensation`, `ssn`, or `national_id`. `make contracts-check` validates the four files and fails if the committed JSON Schema drifted. A negative test covers a file that does not load.

## Alternatives rejected

- A contract checked only in review, with no startup failure. A bad file would boot and index the wrong thing.
- One shared schema for the source payload. The sources do not have one. The contract normalizes ownership, freshness, and ACL. It does not pretend the bodies match.
- A database table edited at runtime. The POC has four files. A reviewable diff is the control. A live editor is not built.

## Consequences

- Adding a source is a YAML file, a connector process, and `make contracts-check`. It is not a default because an API exists.
- `region` is allowed on a contract and is not an access predicate. Putting it on the file does not make it one.
