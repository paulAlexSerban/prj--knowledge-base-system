# ADR 0003: Postgres commits first, then Neo4j

Status: accepted for the POC.

## Context

Chunk text and the graph are two stores. A crash between them leaves one updated and the other not. The answer has to say which store is right, and what a retry does.

## Decision

Postgres is authoritative for prose. Neo4j is a derived index. The worker commits Postgres, then merges Neo4j. A failed graph write leaves the message unacknowledged, so the event is received again after the visibility timeout. Upserts are idempotent on source id, object id, chunk index, chunker version, and embedding model. A retry whose content hash already matches still writes the graph, so the node is created. Deletes verify both stores ([ADR 0014](0014-hard-deletes.md)).

## Alternatives rejected

- Write the graph first. A failed Postgres commit would leave a traversable edge with no citation.
- One distributed transaction across Postgres and Neo4j. No shared transaction manager is in this stack, and adding one is not justified at demo volume.
- Ignore a graph failure and acknowledge the message. The roadmap path would be missing until a person noticed.

## Consequences

- The two stores can diverge until the retry finishes. The chunk can exist without the node during that window.
- The idempotent upsert is the real exactly-once. The queue is at-least-once ([ADR 0012](0012-sqs-fifo.md)).
- Ordering is per object, not across objects. A deal and its epic can land out of order. The `DEPENDS_ON` merge is written from whichever side arrives, and dropped when either side is tombstoned.
