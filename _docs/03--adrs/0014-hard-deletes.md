# ADR 0014: Hard deletes, verified, with a sweep behind them

Status: accepted for the POC.

## Context

The failure that matters is a source that forgot an object and an index that keeps answering from it. A hidden row fails the same way if one query forgets the filter. A graph edge that survives the delete is still traversable.

## Decision

Deletes are hard deletes. The connector removes every S3 version, checks the key is gone, enqueues `delete` with the version and hash and no pointer, then removes the `kbs.source_objects` row. A failed check or a failed enqueue keeps the row. The worker inserts `kbs.tombstones`, deletes the chunks and the graph nodes this object owns, and checks both counts are zero. A failed check increments `kbs_delete_verification_failures_total` and the message retries. A replayed create with the same version and content hash does not resurrect a tombstone. A newer version clears it.

HR and Engineering have no delete feed, so this runs from the `/ids` sweep. Sales puts a delete on the cursor feed, and the sweep stays the backstop. A product delete pointer takes the same path. The worker also reconciles every `RECONCILE_INDEX_SECONDS`: chunks with no `source_objects` row are deleted, and a row with no current chunk is read back from S3.

## Alternatives rejected

- Soft delete only. A missed filter returns the row, and the graph keeps the edge. A legal hold, if one appears, belongs on the source contract as an exception. None exists.
- Trust the event and skip the verification query. The event is at-least-once and the sweep exists because connectors miss events. The count is the proof.

## Consequences

- A missed delete can live until the next sweep. `kbs_index_drift` is how that gap is visible.
- The worker does not delete the S3 object. The connector does, before the event.
