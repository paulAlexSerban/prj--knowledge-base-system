# ingest-contract

Library. No image and no process.

`ChangeEvent`, the FIFO queue names (`kbs-p0.fifo`, `kbs-p1.fifo`, `kbs-p2.fifo`, and one dead-letter queue per tier), the raw-snapshot bucket name, and `snapshot_hash`. The HR connector publishes these events after the S3 put. The ingestion worker consumes them. A delete is valid without an S3 pointer. The product webhook does not enqueue yet.
