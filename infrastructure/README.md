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
| `orchestration/compose.aws.yaml` | floci, a curl wait | `kbs-aws` |
| `orchestration/compose.knowledge-store.yaml` | Postgres 18, Neo4j, constraint init | `kbs-data` (internal) |
| `orchestration/compose.ingestion-pipeline.yaml` | `connector-hr`, ingestion worker, webhook receiver. Includes the AWS file so both wait for `floci-ready`. | `kbs-data`, `kbs-sources`, `kbs-aws`, `kbs-obs` |
| `orchestration/compose.knowledge-gateway.yaml` | API on 8000, MCP on 8001 | `kbs-data`, `kbs-obs`, `kbs-sources` |
| `orchestration/compose.demo-harness.yaml` | HR 8101, Sales 8102, Product 8103, Engineering 8104 | `kbs-sources`, `kbs-obs` |
| `orchestration/compose.notebooks.yaml` | Jupyter 8888 | `kbs-sources`, `kbs-obs`, `kbs-aws` |
| `orchestration/compose.observability.yaml` | Alloy, Loki, Prometheus 9090, Grafana 3000 | `kbs-obs` |
| `orchestration/compose.tools.yaml` | pgAdmin 5050, SQLite viewers 8111–8114 | `kbs-data` (pgAdmin only), `kbs-tools` |

`kbs-data` is `--internal`. Postgres and Neo4j have no published ports. pgAdmin is on `kbs-data` and on `kbs-tools`, and its UI is bound to `127.0.0.1:5050`. That is a dev-only bridge so the database port stays closed. The SQLite viewers mount the harness volumes read-only and are not on `kbs-data`.

The gateway joins `kbs-sources` so it can call the HR harness `GET /policies/{id}/acl` before returning a policy chunk. That is a trust-boundary exception: the gateway can reach the harness, and it still has no path to floci. Jupyter stays off `kbs-data` and reaches the gateway on `kbs-sources`.

## Startup

`make setup` does the whole start (see the README). By hand: copy `.env.example` to `.env`, then `make up` creates the networks and starts the stack. Terraform runs as the one-shot `terraform-apply` service in `compose.aws.yaml`, so no host install is needed. It copies the configuration, runs `init` and `apply` against `http://floci:4566`, and exits. The first run downloads images and the AWS provider. Neo4j takes about two minutes. Terraform creates `kbs-p0.fifo`, `kbs-p1.fifo`, `kbs-p2.fifo`, a FIFO dead-letter queue for each, `kbs-product-hooks` and its dead-letter queue, and the `kbs-raw-snapshots` bucket.

State is throwaway and lives inside the container. floci runs in memory mode, so a restart of that container drops the queues and the bucket, and empty state is the correct starting point. After a floci restart run `make tf-apply`; it re-runs the container. There is no `tf-destroy`. `.terraform.lock.hcl` is committed.

Postgres 18 stores the cluster under `/var/lib/postgresql`. A volume created by Postgres 16 cannot be opened. Remove it once: `docker volume rm kbs_pgdata`. There is no upgrade step.

`make tools` starts pgAdmin, the four SQLite viewers, Postgres, and the harness. Open pgAdmin at `http://127.0.0.1:5050` (no login; the `kbs` server is preloaded). Open each harness database at `http://127.0.0.1:8111` (HR), `8112` (Sales), `8113` (Product), and `8114` (Engineering). pgAdmin and the SQLite viewers are not scraped: they have no `/metrics` endpoint.

Scheduler schedules are not created. EventBridge Scheduler cannot run faster than once a minute, and a schedule needs an IAM role this stub does not invent. Polling is a loop in each connector. HR polls every 10 seconds. Engineering and Sales poll every 60 seconds. Product does not poll. It waits on `kbs-product-hooks`.

The three tier queues use `visibility_timeout_seconds` 60 and `maxReceiveCount` 5. The worker also copies a message to the dead-letter queue when the receive count exceeds 5, because floci's redrive was not observed.

`postgres-migrate` applies every file in `database/postgres/migrations/` on every start, in name order. `01-init.sh` runs only on an empty volume. The `vector` extension is moved into schema `kbs` because application connections set `search_path=kbs`.

Worker environment that is not in `.env`: `EMBEDDER=hash` and `RECONCILE_INDEX_SECONDS=60`. Passwords and `FAIL_OBJECT_ID` come from `.env`. `GATEWAY_DB_PASSWORD` is the `gateway_ro` password the worker uses for the read path inside `search`. Neo4j is the single user `neo4j`. That is a POC shortcut.

## What the processes do

The gateway lists the four source contracts and exits if the registry is invalid. It searches HR chunks. Each connector writes raw snapshots to `kbs-raw-snapshots` on floci, enqueues a change event, and records sync state in Postgres. The product connector reads `kbs-product-hooks` first. The ingestion worker drains the three tier queues, chunks and embeds each object, and writes Postgres and Neo4j. The four harnesses store rows in SQLite through SQLAlchemy. HR serves a seeded directory and policies. Sales, Product, and Engineering serve a curated set. Jupyter calls the harness admin APIs and can list the snapshot bucket. It is not scraped: it has no `/metrics` endpoint. pgAdmin and the SQLite viewers are the same kind of exception.

Alloy scrapes `/metrics` on `kbs-obs` and remote-writes to Prometheus. It also tails container logs through a read-only Docker socket and pushes them to Loki. That socket is a trust-boundary exception: it can see stdout from containers that are not on `kbs-obs`. Loki and Alloy publish no host ports. Grafana on port 3000 is how you read logs. Sign in as `admin`. The password is `GRAFANA_ADMIN_PASSWORD`, or `admin` when that variable is unset. Details are in [ADR 0002](../_docs/03--adrs/0002-observability.md).
