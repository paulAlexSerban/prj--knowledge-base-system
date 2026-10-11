# connector-core

Library. No image and no process.

The connector interface, the sync cycle, the S3 snapshot store, Postgres sync state, the SQS publisher, and the connector metrics live here. `connector-hr` is the first process that uses it.

On a create or update the syncer writes the snapshot, publishes the change event, then upserts `kbs.source_objects`. A failed publish does not advance the watermark. On a delete it removes the S3 object, checks the key is gone, publishes `delete`, then removes the row. Delete verification failures increment `kbs_delete_verification_failures_total` with `store="s3"`.
