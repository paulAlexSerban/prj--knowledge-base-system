# HR harness

Service `harness-hr`, host port 8101, container port 8080. FastAPI, SQLAlchemy on SQLite, and policy files on disk. Status: built.

The source stands in for an HR system. An empty database is seeded on start with 48 Faker people and eleven curated policies (nine Markdown, two PDF, including Germany parental leave). Those rows carry the fixed timestamp `2026-01-15T09:00:00Z`. Larger loads use `POST /admin/seed`.

Change signal: `updated_since` plus `snapshot_diff`. There is no webhook and no delete feed. The connector polls `/changes` in-process because the source is tier P0, and the reconciliation sweep reads `/ids`.

## Read API (used by the connector)

| Endpoint | Purpose |
| -------- | ------- |
| `GET /discover` | Object types, capability flags, native ACL names, counts. |
| `GET /changes?updated_since=&page_token=&limit=` | People and policies at or after the timestamp, oldest first, ordered by `(updated_at, id)`. The bound is inclusive and the page token is a keyset, so no row is skipped at a page or timestamp boundary. Keep `server_time` as the next watermark after the last page. Dedup on `version` and `content_hash`. |
| `GET /ids` | Every live id and version. A delete is visible only as an id missing here. |
| `GET /policies`, `/policies/{id}` | Policy metadata. |
| `GET /policies/{id}/content` | Raw file: `text/markdown` or `application/pdf`. This is the object to snapshot to S3. |
| `GET /policies/{id}/acl`, `/people/{id}/acl` | Live native ACL. The gateway re-checks this at read time because HR is high sensitivity. |
| `GET /people`, `/people/{id}` | Directory records. |

Native ACL names are `hr_employee` and `hr_policy_admin`, mapped to platform attributes by [hr.yaml](../../infrastructure/config/sources/hr.yaml). Two policies are admin-only: `pol-disciplinary-procedure` and `pol-compensation-review`. Each policy has a `region` (`DE`, `US`, `UK`, or `GLOBAL`).

An ACL change moves `updated_at` but not `version` or `content_hash`. The HR connector hashes the raw bytes together with the sorted native ACL, so that change still rewrites the S3 object.

The connector reads `/changes`, `/policies/{id}/content`, `/people/{id}`, and `/ids`. It does not call `/admin`. People snapshots are the public JSON record. Policy snapshots are the file bytes. Keys are `raw/hr/<type>/<id>.<ext>` in `kbs-raw-snapshots`.

## Data minimization

People rows hold `salary` and `national_id`, as a real HR system would. No endpoint returns them, and a test checks every read path. The raw snapshots written to S3 therefore never contain them.

## Admin API (used by the mutation script)

These endpoints stand in for HR staff editing the source. Nothing in the gateway calls them.

| Endpoint | Effect |
| -------- | ------ |
| `PUT /admin/policies/{id}` with `{body, title}` | Rewrite the file. Version rises only if the bytes or title changed. |
| `PUT /admin/policies/{id}/acl` with `{native_acl}` | Change who can read it. |
| `POST /admin/policies`, `DELETE /admin/policies/{id}` | Create or hard-delete a policy and its file. |
| `PATCH /admin/people/{id}`, `POST /admin/people`, `DELETE /admin/people/{id}` | Edit, create, or delete a person. |
| `POST /admin/seed` with `{kind, count, seed, replace}` | Generate `person` or `policy` rows with Faker and insert them. `count` is 1 to 50,000. Ids continue after the highest existing suffix (`emp-NNNNNN`, `pol-gen-NNNNNN`). `replace: true` deletes that kind first. Returns counts and `elapsed_seconds`. |
| `POST /admin/reset` | Return to the curated seed so the demo can be repeated. |

## Load 10,000 rows

The seed notebook is the supported way. By default it resets the harness and adds 200 people; set `SEED_COUNT=10000` for this load. See [notebooks/README.md](../../notebooks/README.md). The same call from the host:

```bash
curl -s -X POST http://127.0.0.1:8101/admin/seed \
  -H 'content-type: application/json' \
  -d '{"kind":"person","count":10000,"seed":1}'
```

Use `"kind":"policy"` for policies. About 20 percent are PDF and about 5 percent are `hr_policy_admin`. The same `seed` value produces the same names and bodies. A second call does not collide: numbering starts after the current maximum.

## Shared keys

Team names (`people`, `platform`, `payments`, `identity`, `data`, `product`, `sales-emea`, `sales-amer`) are the shared keys the Sales, Product, and Engineering harnesses should reuse when they name an owning team.

## Run and test

```bash
uv run pytest demo-harness/hr/tests
SQLITE_PATH=/tmp/hr/harness.db PORT=8101 uv run python -m harness_hr
```

Compose sets `SQLITE_PATH=/data/harness.db` and `FILES_DIR=/data/policies` on the `hrdata` volume. Metrics: `kbs_harness_up` and `kbs_harness_objects{object_type}` on `/metrics`.

## Layout

`store.py` is the SQLAlchemy layer (models, keyset queries, batched inserts). `app.py` is the API. `generate.py` builds Faker people and policies. `catalog.py` loads the default 48 people plus the curated files in `policies/*.md`. `pdf.py` writes dependency-free PDFs. Policies of format `pdf` are stored as PDFs rendered from the Markdown source. The process entry point is `python -m harness_hr`.
