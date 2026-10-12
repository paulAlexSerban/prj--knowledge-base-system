# connector-core

Library. No image and no process.

The connector interface, the sync cycle, the S3 snapshot store, Postgres sync state, the SQS publisher, the connector metrics, and the poll loop (`run_poll`) live here. `prepare` is the same startup without the poll loop, for a connector that reads a hook queue instead. Each connector image is a thin main that names its source and harness.

On a create or update the syncer writes the snapshot, publishes the change event, then upserts `kbs.source_objects`. A failed publish does not advance the watermark. On a delete, from the reconciliation sweep or from an `op: delete` feed item, it removes the S3 object, checks the key is gone, publishes `delete`, then removes the row. A watermark that is not an ISO timestamp is stored and is not turned into a lag sample. Delete verification failures increment `kbs_delete_verification_failures_total` with `store="s3"`.
