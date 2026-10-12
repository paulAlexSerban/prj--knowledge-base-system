# Sales harness

Service `harness-sales`, host port 8102, container port 8080. FastAPI and SQLAlchemy on SQLite. Status: built.

The source stands in for a CRM. An empty database is seeded with 40 deals at `2026-01-15T09:00:00Z`. Account name and id sit on the deal. There is no account object. `epic_id` is the engineering epic the deal depends on, and it may be empty. Product keys match the engineering harness.

High ARR in this demo means 200,000 or more. Eight deals are high-ARR, in `2026-Q3`, and point at an unreleased epic: `deal-northwind-q3`, `deal-contoso-q3`, `deal-fabrikam-q3`, `deal-adventure-q3`, `deal-wideworld-q3`, `deal-litware-q3`, `deal-alpine-q3`, and `deal-tailspin-q3`. `deal-woodgrove-q3` is high-ARR in Q3 but the epic is released. `deal-proseware-q3` has no epic. `deal-coho-q2` and `deal-lucerne-q4` are the wrong quarter. `deal-graphic-q3` is under the ARR bar.

Change signal: `cursor_feed`. `GET /changes?cursor=` returns the log after that cursor. `next_cursor` is the resume point. Deletes are `op: delete` on the feed. `/ids` is the reconciliation backstop. The cursor is a sequence number, not a timestamp, and it is not reset when the log is cleared.

## Read API

| Endpoint | Purpose |
| -------- | ------- |
| `GET /discover` | Object type, capability, native ACL names, counts. |
| `GET /changes?cursor=&limit=` | Upserts and deletes after the cursor, oldest first. |
| `GET /ids` | Every live id and version. |
| `GET /deals`, `/deals/{id}` | Deal records. |
| `GET /deals/{id}/acl` | Live native ACL. |

Native ACL names are `account_team`, `sales_leadership`, and `roadmap_readers`. A deal carries `account_team` and `roadmap_readers`, except the leadership deals `deal-contoso-q3`, `deal-tailspin-q3`, and `deal-coho-q2`, which stay `sales_leadership` only. Those two Q3 leadership deals are invisible to `product_lead`. An ACL change does not bump `version`. A field change does.

## Data minimization

Each deal stores `commission_rate`. No endpoint returns it.

## Admin API

Nothing in the gateway calls these.

| Endpoint | Effect |
| -------- | ------ |
| `POST /admin/deals` | Create a deal. |
| `PATCH /admin/deals/{id}` | Change amount, stage, quarter, epic, or owner. |
| `PUT /admin/deals/{id}/acl` | Change who can read it. |
| `DELETE /admin/deals/{id}` | Hard-delete the deal and append a delete event. |
| `POST /admin/reset` | Return to the curated seed and append those upserts after the previous cursor. |

## Run and test

```bash
uv run pytest demo-harness/sales/tests
SQLITE_PATH=/tmp/sales/harness.db PORT=8102 uv run python -m harness_sales
```

Compose sets `SQLITE_PATH=/data/harness.db` on the `salesdata` volume. Metrics: `kbs_harness_up` and `kbs_harness_objects{object_type="deal"}` on `/metrics`.
