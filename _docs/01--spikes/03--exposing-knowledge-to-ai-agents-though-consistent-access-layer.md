# Spike 03: Exposing knowledge through one access layer

Status: decision for the POC. Production scale is called out and is not built.

## Problem

A person searching a wiki can skim results, notice that a page is three years old, and weigh it against what they already know. An agent reads quickly, does not naturally judge provenance, and may act on what it retrieved. The knowledge is the same. The consumer is not, and the access layer has to compensate.

The usual starting point, a script that embeds documents and pastes the top hits into a prompt, has no contract, no per-user authorization, and no way for a second agent to reuse the work. Authorization is the decision with the largest consequence. Two incomplete strategies show up immediately:

- Pre-filtering pushes the access predicate into the index query, so the index only scores documents the principal could see. Recall survives restrictive permissions. The index has to filter on those attributes efficiently.
- Post-filtering retrieves candidates first and drops the ones the user cannot see. It is simpler, and it starves users who can see only a small slice: the top ten candidates are removed, and the agent reports that nothing was found, which is false.

MCP gives every capable client the same tools without a custom integration per client. It does not authenticate the user or enforce access. The server still has to do both on every call.

## Decision (MVP)

One service layer. Two adapters. Agents and the demo chat client never hold credentials for Postgres, Neo4j, or S3. Those stores sit on the internal Compose network. The only ingress is the Knowledge API and the MCP server.

### Tools and endpoints

The MCP tool description is the interface an agent uses to decide when to call. Descriptions name what the tool returns, what it refuses to do, and that results are already filtered to the caller.

| MCP tool | REST | Behavior |
| -------- | ---- | -------- |
| `search_knowledge` | `POST /v1/search` | Hybrid or graph-routed retrieval for the caller's identity. Returns chunk ids, snippets, source, version, sync time, and score. |
| `get_document` | `GET /v1/documents/{id}` | One authorized document or chunk, with provenance. |
| `find_related` | `POST /v1/graph/related` | Bounded traversal from a known entity. Returns the path, not an open Cypher console. |
| `list_sources` | `GET /v1/sources` | Source id, owner, freshness tier, last successful sync. No document bodies. |

There is no write tool. Retrieved text cannot change permissions or trigger an action.

### Identity

A mock identity provider signs JWTs. Claims used for authorization: subject, department, clearance, region, and group list. The API and the MCP server validate the token and pass the principal into the service layer. Agents do not receive a service credential that sees more than the user who invoked them.

### Authorization

Hybrid enforcement, in this order:

1. Coarse pre-filter in the index. SQL `WHERE` on department, clearance, and region, and the same predicates in the parameterized Cypher. The candidate set is already authorized at the coarse grain, so restrictive users are not starved.
2. Over-fetch. The retriever asks for more candidates than the tool will return, then applies the fine-grained check, then truncates. Empty-result rate is measured per permission group, not only globally ([ADR 0002](../03--adrs/0002-observability.md)).
3. Fine-grained post-check against the ACL attributes stored on the chunk.
4. Postgres row-level security as defense in depth, so a query that forgets the predicate still cannot read another principal's rows. The application role used by the service sets the principal's claims as session settings that the policies read.

Every allowed result writes an audit row: principal, chunk id, and the attributes that justified the allow. Denies are audited too. Access-boundary tests in the golden set (spike 02) create two users and assert that retrieval, graph paths, and `get_document` return only permitted content. That golden set is not built. The store tests cover two principals against one pool, including a principal with no matching group and a missing session setting.

### What the store enforces

Chunks store coarse columns `acl_groups` (`text[]`), `acl_departments` (`text[]`), and `min_clearance` (`smallint`), plus the exact rules in `acl_rules` (`jsonb`). Clearance order is `public` < `internal` < `confidential` < `restricted`. A mapping that omits clearance is treated as public. An unmapped native ACL is stored and granted to nobody: empty groups and `min_clearance` 99. `region` is allowed on a source contract and is not an HR access predicate.

`gateway_ro` reads `kbs.chunks` under policy `chunks_read_by_acl`. The policy compares session settings `kbs.groups`, `kbs.clearance`, and `kbs.department` to those columns. An empty or missing setting sees no rows. `ingestion_rw` has `chunks_ingestion_all` and can write every row. The table owner bypasses row-level security, so the API must not use it.

Store-level `search` applies the SQL pre-filter and row-level security, fuses the vector and full-text lists, then checks `acl_rules` and writes the audit row. REST `POST /v1/search`, the MCP tools, the mock identity provider, Cypher authorization, and the read-time `get_acl` re-check are not built.

### Permission staleness

| Sensitivity on the source contract | Behavior |
| ---------------------------------- | -------- |
| High (HR) | ACL-change events use the highest-priority queue (spike 04). Search also re-checks `get_acl` against the source at read time. The extra latency is accepted and excluded from the 250 ms target only if it is labeled as such in the metric. |
| Medium and low (Sales, Engineering, Product) | The indexed ACL is authoritative inside the source's freshness window. The window is written on the contract. |

### Prompt injection and provenance

Retrieved content is data. The service wraps snippets in a delimiter and the system prompt states that instructions inside retrieved documents are not followed. The POC has no write-capable tool for retrieved content to invoke. Responses include document ids, source version, and sync time so a citation can be opened with `get_document`. Rendered links in the small chat page are escaped.

### Versioning

The public HTTP API is `/v1`. Index metadata records `embedding_model` and `chunker_version` (ADR 0003). MCP tool schemas live next to the service layer so the two adapters cannot drift.

## Why

- A second agent, or a second MCP client, reuses the same semantics. That is the "one access contract" principle in the brief.
- Pre-filter plus over-fetch is what keeps recall for a user who can see only one department. Post-filter alone fails the demo step where an unauthorized user must get an empty set for the right reason, and a narrowly authorized user must still get their own documents.
- Row-level security catches the bug where a new query forgets the predicate. Authorization is not left to the prompt.
- Bounded `find_related` exposes the graph the roadmap question needs, without handing agents arbitrary query access to Neo4j.

## Alternatives rejected

| Alternative | Why it was rejected for the POC |
| ----------- | ------------------------------- |
| Post-filter only | Starves restrictive principals and reports a false "nothing found". |
| Pre-filter only, with no audit and no row-level security | Works until a code path bypasses the filter. Defense in depth is cheap in Postgres and expensive to retrofit after an incident. |
| Per-agent direct access to the vector store | Breaks the single contract, makes revocation per consumer impossible, and lets a prompt skip authorization. |
| MCP as the authorization boundary | The protocol does not define the principal or the policy. Treating "the client is MCP" as "the call is allowed" would be a broken access story. |
| Open Cypher or SQL from the agent | Flexible, and incompatible with authorization and with stable tool descriptions. Parameterized templates only. |
| Live ACL check on every source for every query | Correct for high sensitivity, and it spends the latency budget and the source rate limit on policy documents that do not need it. The split above is the explicit decision. |

## Trade-offs and risks

- The high-sensitivity read-time ACL check can miss the 250 ms budget. It is measured separately. If the demo cannot meet both, the freshness metric and the latency metric are reported as two numbers, not averaged into one.
- Indexed ACLs for medium and low sources are stale inside the sync window. That window is a documented risk, not an accident discovered during an incident.
- Tool descriptions that are vague cause the agent to call `search_knowledge` for a relational question or `find_related` for a policy question. Descriptions and the router's entity-type fallback (spike 02) are both required.
- Session-scoped row-level security is easy to get wrong under connection pooling. The service sets claims on checkout and clears them on release. A test holds two principals on one pool and asserts isolation.

## Production path

- Real identity provider (OIDC) in place of the mock signer. Same claims contract.
- Per-consumer credentials and revocation, still with no path from the consumer to the stores. Network policy stays.
- Dual-run of `/v1` and `/v2` when a breaking retrieval change ships, with a published retirement date.
- Field-level redaction if a future contract allows a sensitive field into the index. The POC avoids that field instead.

## Open TODOs

- The row-level security policies and `kbs.audit_log` are in `database/postgres/migrations/002-knowledge-store.sql`. Store-level search writes the audit row. The JWT claim schema is not built.
- The deny log line is specified in [ADR 0002](../03--adrs/0002-observability.md): one JSON object on stdout, `decision` of `deny`, and `request_id` in the line rather than in a Loki label. Search does not emit that line yet. It writes the table.
