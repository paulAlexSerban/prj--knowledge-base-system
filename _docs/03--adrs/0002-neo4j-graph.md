# ADR 0002: Neo4j for a small typed graph

Status: accepted for the POC.

## Context

The roadmap question is a short typed traversal: a deal depends on an epic, and that epic is owned by a team. Similarity search does not answer it. Showing the path is part of the demo. The ontology is a handful of node types, on purpose.

## Decision

Neo4j 5 Community holds the graph. The writer creates `Person`, `Team`, `Policy`, `Deal`, and `Epic`, and the edges `BELONGS_TO`, `DEPENDS_ON`, and `OWNED_BY`. Those edges come from structured records in code. Account is a field on the deal, not a node. Product specs stay prose and are not nodes. The gateway runs four parameterized templates on a read-only session, including `roadmap_deals`. There is no open Cypher. The query router ([ADR 0026](0026-query-router.md)) picks one of those templates, or search. It does not write Cypher.

Community has no roles. The gateway and the worker share the `neo4j` user. That is an accepted shortcut. The read-only session and the templates are the only database guard on that user.

`database/neo4j/init.cypher` still creates uniqueness constraints for `Account` and `Spec`. Nothing writes those labels. The constraints are leftover, not a second ontology.

## Alternatives rejected

- Edge tables and recursive SQL in Postgres. Fewer containers, and a reasonable production simplification if the ontology stays this small. Rejected for the POC because the path is a demo and Cypher states it more clearly. Revisit if Neo4j is what fails in setup.
- Amazon Neptune. Only worth it if openCypher covers the templates. Not evaluated beyond that, and not needed locally.
- Memgraph, or any other graph engine. Not evaluated. It would be a second container with the same job.

## Consequences

- Operators run Neo4j in Compose, on the internal `kbs-data` network, with no published port.
- A write from the gateway is a bug the database will not stop.
- Fuzzy entity resolution is out of scope. The demo data shares keys on purpose.

## Production path

Neo4j Aura, or Neo4j on EC2, with a separate read role. Neptune only if the templates stay inside openCypher.
