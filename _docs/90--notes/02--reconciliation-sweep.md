# Reconciliation sweep

There are two sweeps. Both exist because the change path is at-least-once and sources can miss a delete or a create. Neither is a full reindex. Both list ids, not bodies. The periodic job is only this; the normal path is still create, update, delete, and ACL-change events.

The design is [spike 04](../01--spikes/04--self-maintaing-over-time-refelcting-additions-modifications-n-deletions-in-the-source-systems-without-manual-intervention.md) and [ADR 0014](../03--adrs/0014-hard-deletes.md). EventBridge Scheduler is not wired. Each sweep is an in-process timer.

## Connector sweep (`RECONCILE_SECONDS`, default 60)

This runs in each connector process. HR, Sales, and Engineering poll, then reconcile. Product has no poll: it consumes hook pointers and reconciles from `/ids`. A lost webhook is recovered here.

`Syncer.reconcile` in `connector-core`:

1. `GET /ids` from the harness. That is every live id and version. Not the bodies.
2. List the snapshot keys in S3 for this source.
3. For every `source_objects` row whose id is not in `/ids`: hard-delete S3, verify the key is gone, enqueue `delete`, then drop the row. A failed check or a failed send keeps the row so the next sweep retries.
4. For every live id that has no row, a different version, or a missing S3 object: `fetch` and force a snapshot. That emits create or update on the tier queue.
5. Set `kbs_source_objects`, `kbs_snapshot_objects`, and `kbs_reconcile_drift` (snapshot count minus live count). Zero drift is healthy.

HR and Engineering have no delete feed. The only way a deleted person, policy, or epic leaves the index is this sweep, then the worker. Sales puts `op: delete` on the cursor feed, so the delete usually happens on the poll; the sweep is the backstop. Product treats a hook whose spec is already 404 as a delete, and the sweep still catches a missed hook.

A missed delete can live until the next 60-second tick. That is accepted. The drift panel is how it shows up.

## Index sweep (`RECONCILE_INDEX_SECONDS`, default 60)

This runs in the ingestion worker, not on a P2 message. The P2 queue exists. Nothing publishes to it.

`KnowledgeStore.reconcile_index`:

1. Orphans: chunks whose `source_objects` row is gone. Tombstone and hard-delete those chunks and graph nodes, then verify both stores.
2. Missing: a `source_objects` row with no chunk at the current version and the active embedding model. Read the S3 pointer and upsert. One bad snapshot is skipped; the sweep continues.
3. Set `kbs_index_objects` and `kbs_index_drift` (indexed object count minus `source_objects` count).

This is the backstop after the connector already deleted S3 and dropped the row, or after a graph write failed and a later hash-match retry still needs the node. `make reindex` calls the same sweep, then drops chunks written by any other embedder.

## What it is not

- Not a scheduled full crawl of every body.
- Not P2. P2 is documented as daily backfill; the POC timer is 60 seconds so the demo delete is visible without waiting a day.
- Not a substitute for the poll or the product hook. Those are how a change usually arrives. The sweep is what makes a missed event visible and correctable without a human re-index.
