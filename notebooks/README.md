# Notebooks

Jupyter for loading and inspecting the demo sources. Status: built for HR. Sales, Product, and Engineering are not wired yet.

The notebook calls the harness over HTTP. It does not open SQLite, and the container is not on the `kbs-data` network, so it cannot reach Postgres or Neo4j. Jupyter has no `/metrics` endpoint and is not scraped.

## Seed 10,000 rows

Start the harness, then Jupyter:

```bash
make up
make notebooks
```

Open `http://127.0.0.1:8888` and use `JUPYTER_TOKEN` from `.env` (the example value is `dev-only-notebook`).

[00-seed-source.ipynb](00-seed-source.ipynb) posts to `POST /admin/seed`. The parameter cell defaults to HR, `person`, and `COUNT` of 10000. Set `KIND` to `policy` for policies. The service generates the rows with Faker (seed `SEED`) and inserts them. The last cell checks `/discover`, `/ids`, and a full walk of `/changes`.

Environment variables override the cell when you run it headless: `SEED_SOURCE`, `SEED_KIND`, `SEED_COUNT`, `SEED_VALUE`, `SEED_RESET`. Inside Compose the base URL is `HARNESS_HR_URL=http://harness-hr:8080`. On the host it defaults to `http://127.0.0.1:8101`.

`POST /admin/reset` on the HR harness returns the curated seed (48 people and the eleven hand-written policies, including Germany parental leave). A seed with `replace: true` drops only that kind. The notebook leaves `replace` false and continues ids.

[01-inspect-source.ipynb](01-inspect-source.ipynb) samples the change feed and checks that `salary`, `compensation`, `ssn`, and `national_id` are absent.

[02-inspect-snapshots.ipynb](02-inspect-snapshots.ipynb) lists the raw snapshots in `kbs-raw-snapshots` and checks the count against `/ids`. Jupyter is on the `kbs-aws` network so it can reach floci. It is not on `kbs-data`.

## Headless

```bash
HARNESS_HR_URL=http://127.0.0.1:8101 SEED_KIND=person SEED_COUNT=10000 \
  uv run jupyter nbconvert --to notebook --execute notebooks/00-seed-source.ipynb \
  --output /tmp/seed-person.ipynb
```
