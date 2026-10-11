# Notebooks

Jupyter for the HR harness. Status: built for HR. Sales, Product, and Engineering are not wired yet.

The harness notebooks call the harness over HTTP and read raw snapshots from S3. The gateway notebooks call the API, the MCP server, and Loki. None of them open SQLite, and the container is not on the `kbs-data` network, so it cannot reach Postgres or Neo4j. Jupyter has no `/metrics` endpoint and is not scraped.

Shared helpers live in [hr_demo.py](hr_demo.py) and [gateway_demo.py](gateway_demo.py). Connector metrics are optional. When `CONNECTOR_METRICS_URL` does not answer, the harness notebooks check S3 versions and still pass.

## Start

```bash
make up
make notebooks
```

Open `http://127.0.0.1:8888` and use `JUPYTER_TOKEN` from `.env` (the example value is `dev-only-notebook`).

Inside Compose, `HARNESS_HR_URL` is `http://harness-hr:8080`, `AWS_ENDPOINT_URL` is `http://floci:4566`, `CONNECTOR_METRICS_URL` is `http://connector-hr:9100/metrics`, `GATEWAY_API_URL` is `http://gateway-api:8000`, `GATEWAY_MCP_URL` is `http://gateway-mcp:8001/mcp`, and `LOKI_URL` is `http://loki:3100`. On the host the defaults are `http://127.0.0.1:8101`, `http://127.0.0.1:4566`, `http://127.0.0.1:9100/metrics`, `http://127.0.0.1:8000`, `http://127.0.0.1:8001/mcp`, and `http://127.0.0.1:3100`. Jupyter is on `kbs-aws`, `kbs-sources`, and `kbs-obs`. It is not on `kbs-data`. The gateway joins `kbs-sources`, which is how these notebooks reach it.

Run the notebooks in this order. `00` resets the harness, so do not run it while a CRUD notebook is in the middle of a wait.

1. [00-seed-hr.ipynb](00-seed-hr.ipynb) posts `POST /admin/reset`, then `POST /admin/seed`. The default is 200 people on top of the curated seed (48 people and 38 policies, including Germany parental leave). `SEED_COUNT=0` skips the extra rows. `SEED_COUNT=10000` is the scale load. `SEED_RESET=false` keeps existing rows. `SEED_KIND=policy` loads policies. `replace` stays false, so ids continue. The last cell checks `/discover` against `/ids`.
2. [01-inspect-hr.ipynb](01-inspect-hr.ipynb) samples `/changes`, checks that `hr_demo.FORBIDDEN` (`salary`, `compensation`, `ssn`, `national_id`) is absent, reads the Germany parental-leave policy, and waits until `kbs-raw-snapshots` matches `/ids`.
3. [10-policy-create-read.ipynb](10-policy-create-read.ipynb) through [13-policy-delete.ipynb](13-policy-delete.ipynb) create, edit, change the ACL of, and delete a policy. A duplicate create returns 409, a bad id returns 422, and a missing edit returns 404. An ACL-only change keeps the harness version and still writes a snapshot, because the snapshot hash covers the bytes and the ACL.
4. [20-person-create-read.ipynb](20-person-create-read.ipynb) through [22-person-acl-and-delete.ipynb](22-person-acl-and-delete.ipynb) do the same for a person. `manager_id` is the field a "who reports to whom" answer is read from. A person write bumps the version, including an ACL-only change.
5. [30-end-to-end-cycle.ipynb](30-end-to-end-cycle.ipynb) is the live demo: one policy through create, update, ACL change, and delete, then drift back to 0. `make e2e` is the same cycle in pytest and is not part of `make test`.
6. [40-gateway-identity.ipynb](40-gateway-identity.ipynb) mints `employee_eng`, `hr_admin`, and `outsider`, and checks that a missing token, a bad token, and an unknown persona fail.
7. [41-gateway-search-abac.ipynb](41-gateway-search-abac.ipynb) searches for Germany parental leave as those personas and reads the allow and deny lines from Loki.
8. [42-gateway-mcp.ipynb](42-gateway-mcp.ipynb) lists the four MCP tools and repeats one search. The MCP result matches REST.
9. [43-gateway-freshness.ipynb](43-gateway-freshness.ipynb) creates a policy, closes its ACL, and deletes it. Search and `find_related` follow.

Each CRUD notebook uses its own id (`pol-demo-*`, `emp-900001` to `emp-900003`) and deletes it at the end. The delete waits for the reconcile sweep (about a minute, up to `hr_demo.DELETE_WAIT`). The harness has no delete feed. Create, update, and ACL changes usually land on the next 10 second poll.

Environment variables for the seed notebook: `SEED_SOURCE`, `SEED_KIND`, `SEED_COUNT`, `SEED_VALUE`, `SEED_RESET`.

## Headless

```bash
HARNESS_HR_URL=http://127.0.0.1:8101 SEED_KIND=person SEED_COUNT=10000 \
  uv run jupyter nbconvert --to notebook --execute notebooks/00-seed-hr.ipynb \
  --output /tmp/seed-person.ipynb
```
