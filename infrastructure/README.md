# Infrastructure

Local Compose projects and Terraform that talks only to [floci](https://floci.io). Nothing here creates a VPC, a database service, or compute in a cloud account. Docker networks show the intended boundaries. They are not a security boundary.

## Projects

`make up` includes every file below. To start one area, pass `-p` so it does not share a project name with the others:

```bash
docker compose --env-file ../../.env -p kbs-harness \
  -f infrastructure/orchestration/compose.demo-harness.yaml up -d --build
```

External networks must exist first (`make networks`).

| File | Services | Networks |
| ---- | -------- | -------- |
| `orchestration/compose.aws.yaml` | floci, a curl wait, Terraform apply | `kbs-aws` |
| `orchestration/compose.knowledge-store.yaml` | Postgres, Neo4j, constraint init | `kbs-data` (internal) |
| `orchestration/compose.ingestion-pipeline.yaml` | connector worker, ingestion worker, webhook receiver | `kbs-data`, `kbs-sources`, `kbs-aws`, `kbs-obs` |
| `orchestration/compose.knowledge-gateway.yaml` | API on 8000, MCP on 8001 | `kbs-data`, `kbs-obs` |
| `orchestration/compose.demo-harness.yaml` | HR 8101, Sales 8102, Product 8103, Engineering 8104 | `kbs-sources`, `kbs-obs` |
| `orchestration/compose.observability.yaml` | Alloy, Loki, Prometheus 9090, Grafana 3000 | `kbs-obs` |

`kbs-data` is `--internal`. Postgres and Neo4j have no published ports. Debug with `docker compose exec`.

## Startup

Copy `.env.example` to `.env`. `make up` creates the networks and starts the stack. The first run downloads images and the Terraform AWS provider. Neo4j takes about two minutes. Terraform then creates `kbs-p0.fifo`, `kbs-p1.fifo`, `kbs-p2.fifo`, a FIFO dead-letter queue for each, and the `kbs-raw-snapshots` bucket. floci is in memory mode, so a restart of that container forgets the queues until Terraform runs again.

Scheduler schedules are not created. EventBridge Scheduler cannot run faster than once a minute, and a schedule needs an IAM role this stub does not invent. P0 polling is a loop in the connector worker.

## What the processes do

They load, or they wait. The gateway lists the four source contracts and exits if the registry is invalid. The connector worker does the same and logs a P0 poll without fetching. The ingestion worker checks that the three queues answer. The harness serves an empty change list from SQLite. No chunking, embedding, or retrieval runs.

Alloy scrapes `/metrics` on `kbs-obs` and remote-writes to Prometheus. It also tails container logs through a read-only Docker socket and pushes them to Loki. That socket is a trust-boundary exception: it can see stdout from containers that are not on `kbs-obs`. Loki and Alloy publish no host ports. Grafana on port 3000 is how you read logs. Details are in [ADR 0002](../_docs/03--adrs/0002-observability.md).
