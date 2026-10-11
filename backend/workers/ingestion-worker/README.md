# Ingestion worker

Compose service `ingestion-worker`, metrics port 9101. It checks that the three FIFO queues answer on floci and then idles.

Chunking, embedding, and graph writes are not built. Queue names and the change-event model live in `ingest-contract`.
