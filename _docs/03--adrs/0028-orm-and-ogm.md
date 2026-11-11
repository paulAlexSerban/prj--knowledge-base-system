# ADR 0028: ORM for Postgres and an OGM for Neo4j

Status: accepted for the POC.

## Context

Chunk, tombstone, audit, and sync queries were SQL strings. Graph writes and the four read templates were Cypher strings. The behavior is fixed: row-level security, the coarse ACL predicate, the audit row, and Postgres before Neo4j ([ADR 0003](0003-postgres-then-neo4j.md)). Application code should use one library per store.

## Decision

Postgres access goes through SQLAlchemy 2 models in `knowledge_store.models`: `Chunk`, `Tombstone`, `AuditLog`, `SyncState`, and `SourceObject`. Migrations in `database/postgres` stay the DDL. Search uses the ORM for cosine distance, full-text, and the ACL predicate. The principal is set with `set_config` for the transaction, which is what row-level security reads. `gateway_ro` inserts an audit row and cannot select one, so that insert does not use `RETURNING`.

Connectors map `sync_state` and `source_objects` again in `connector_core.state`, including for SQLite tests. A connector image does not install `knowledge-store`, because that package carries the embedding model and Neo4j. A test checks that the column names still match.

Neo4j writes go through neomodel. The classes are `Person`, `Team`, `Policy`, `Deal`, and `Epic`, with `BELONGS_TO`, `DEPENDS_ON`, and `OWNED_BY`. The business key is stored as `id`. The Python attribute is `node_id`, because neomodel reserves `id`. `install_labels` is not called. Constraints stay in `database/neo4j/init.cypher`. The runtime still opens the driver. neomodel is bound to that driver and does not own it.

The four read templates stay Cypher. neomodel cannot express "any element of an array property is in this list" together with the department and clearance checks. `GraphReader` runs the templates with `db.cypher_query` inside a read transaction. Labels and relationship types come from the node models. The agent still cannot send Cypher. The fine-grained rule check is unchanged ([ADR 0018](0018-authorization.md)).

## Alternatives rejected

- SQLAlchemy Core tables only. A chunk is an entity with a vector, arrays, JSON rules, and a generated full-text column. The ORM states that once.
- neomodel traversals for the reads, with the ACL check in Python. That moves the coarse filter out of the database. The array predicate is the reason the templates stay Cypher.
- One model module imported by the connectors. That would install fastembed and Neo4j into every connector image.

## Consequences

- A new column is a migration and a model field. The connector parity test fails if the sync tables drift.
- A graph template that drops `acl_rules IS NOT NULL` fails a test.
- neomodel's connection is process-wide. Binding it replaces the driver for the process.
