# ADR 0018: Authorization in the data layer

Status: accepted for the POC.

## Context

An agent will not notice that a page was confidential. Pre-filtering keeps recall for a narrow principal. Post-filtering alone can drop every hit and report that nothing was found. A filter that lives only in the prompt is not a control.

## Decision

Attributes are stored on the chunk and on the graph node: `acl_groups`, `acl_departments`, `min_clearance`, and the exact `acl_rules`. Clearance order is `public`, `internal`, `confidential`, `restricted`. Enforcement order:

1. A coarse pre-filter in the SQLAlchemy query and in the Cypher templates, on group, department, and clearance.
2. Over-fetch. Search asks for three times the caller's limit (`OVERFETCH = 3`), then keeps one chunk per object.
3. A fine check. One `acl_rules` element has to match group, department, and clearance together. A mix of two rules does not.
4. Postgres row-level security (`chunks_read_by_acl` in migration 004) as defense in depth. A missing session setting sees no rows. `gateway_ro` inserts an audit row and cannot select one, so the insert does not use `RETURNING`. The audit table is append-only. The models are [ADR 0028](0028-orm-and-ogm.md).

An unmapped native ACL is stored and granted to nobody. `region` is not a predicate. HR policy search calls the harness ACL endpoint before returning a chunk, and a timeout or a bad body fails closed: no chunks, and `kbs_acl_recheck_failures_total` increments. People records are not re-checked. `find_related` uses the indexed graph ACL and does not call the harness. Sales, Engineering, and Product trust the indexed ACL inside the freshness window.

`roadmap_readers` uses `department: "*"` so one persona can see both a deal and an epic. That is a deliberate widening. The real fix is a principal with more than one department. Leadership objects do not carry that grant.

## Alternatives rejected

- Application filtering only, with no row-level security. Works until a new query forgets the predicate.
- OPA or Cedar. Not integrated. The attributes already live on the row the query is reading. A second policy engine would be a second implementation of the same three fields, and it was not needed to enforce them.
- A live ACL check on every source for every query. Correct, and it spends the latency budget on documents that do not need it. HR is the exception, and it is measured separately.
- MCP, or the agent, as the authorization boundary. The protocol does not define the principal. Store credentials never leave the gateway.

## Consequences

- A policy with more chunks than the over-fetch window can still crowd out other objects.
- Indexed ACLs for medium and low sources are stale inside the sync window. That window is written on the contract.
- Session settings are set for the transaction. A test holds two principals on one pool.
