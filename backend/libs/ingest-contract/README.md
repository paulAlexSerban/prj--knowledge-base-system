# ingest-contract

Library. No image and no process.

`ChangeEvent`, the FIFO queue names, the raw-snapshot bucket name, and `snapshot_hash`. The ingestion worker and the connectors share this so a later queue emit uses the same hash the snapshot was written with.
