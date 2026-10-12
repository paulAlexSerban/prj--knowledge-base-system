# Product harness

Service `harness-product`, host port 8103, container port 8080. FastAPI, SQLAlchemy on SQLite, and markdown files. Status: built.

The source stands in for a spec store. An empty database is seeded with 12 curated specs at `2026-01-15T09:00:00Z`. Product keys match Sales and Engineering. The bodies name the epics they describe. Specs stay prose: the graph does not get a spec node.

Change signal: `webhook`. An admin create, content edit, or delete posts `{source_id, object_id, event_type}` to `WEBHOOK_URL`. The post is best-effort. A refused connection does not fail the write. There is no `/changes` listing. `/ids` is the backstop for a lost create or edit, because the version moves when the title or body changes. Native ACL names are `product_org` and `roadmap_readers`. Every seeded spec carries both, so `product_lead` can read the prose. Specs are not on the question 2 path.

## Read API

| Endpoint | Purpose |
| -------- | ------- |
| `GET /discover` | Object type, capability, native ACL names, counts. |
| `GET /ids` | Every live id and version. |
| `GET /specs`, `/specs/{id}` | Spec metadata. The body is not in this payload. |
| `GET /specs/{id}/content` | Raw markdown. This is the object to snapshot. |
| `GET /specs/{id}/acl` | Live native ACL. |

## Admin API

Nothing in the gateway calls these. They stand in for product staff.

| Endpoint | Effect |
| -------- | ------ |
| `POST /admin/specs` | Create a spec and post `create`. |
| `PUT /admin/specs/{id}` | Rewrite the file. Version rises only if the title or body changed. Posts `update` when it did. |
| `PUT /admin/specs/{id}/acl` | Accept `product_org` and `roadmap_readers`. Reject anything else. Repeating the current ACL does not move the version. |
| `DELETE /admin/specs/{id}` | Hard-delete the spec and its file, then post `delete`. |
| `POST /admin/reset` | Return to the curated seed and post `create` for each spec. |

## Run and test

```bash
uv run pytest demo-harness/product/tests
SQLITE_PATH=/tmp/product/harness.db FILES_DIR=/tmp/product/specs PORT=8103 \
  WEBHOOK_URL=http://127.0.0.1:8090/webhooks/product uv run python -m harness_product
```

Compose sets `SQLITE_PATH=/data/harness.db` and `FILES_DIR=/data/specs` on the `productdata` volume, and points `WEBHOOK_URL` at the webhook receiver. Metrics: `kbs_harness_up` and `kbs_harness_objects{object_type="spec"}` on `/metrics`.
