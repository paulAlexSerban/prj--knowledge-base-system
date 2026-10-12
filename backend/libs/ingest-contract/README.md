# ingest-contract

Library. No image and no process.

`ChangeEvent`, the FIFO queue names (`kbs-p0.fifo`, `kbs-p1.fifo`, `kbs-p2.fifo`, and one dead-letter queue per tier), the raw-snapshot bucket name, and `snapshot_hash`. Each connector publishes these events after the S3 put. The ingestion worker consumes them. A delete is valid without an S3 pointer. `HookNotice` is the product pointer. The webhook receiver puts it on `kbs-product-hooks`, which is not a change-event queue.
