# Ingestion worker

Compose service `ingestion-worker`, metrics port 9101. It loads the contract registry, then drains `kbs-p0.fifo` before `kbs-p1.fifo` before `kbs-p2.fifo`.

A message the handler rejects stays in flight until the visibility timeout (60 seconds) and is received again. After five receives it is copied to that tier's dead-letter queue. `make dlq-replay` sends those messages back. `FAIL_OBJECT_ID` rejects one object id on purpose, for the dead-letter check.

The process logs each event. Chunk and graph writes are the next slice. Queue names and the change-event model live in `ingest-contract`.
