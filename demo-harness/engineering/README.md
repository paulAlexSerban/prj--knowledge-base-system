# Engineering harness

Service `harness-engineering`, host port 8104, container port 8080. FastAPI and SQLite. Status: built.

The source stands in for a Jira-like epic tracker. An empty database is seeded with 25 curated epics at `2026-01-15T09:00:00Z`. Teams (`platform`, `payments`, `identity`, `data`, `product`) are the HR directory names. Product keys (`checkout`, `billing`, `identity`, `mobile`, `analytics`, `notifications`) are the keys Sales deals and Product specs reuse. Eight of the epics are unreleased and are the ones the Q3 high-ARR deals depend on.

Change signal: `updated_since`. There is no webhook and no delete feed. A delete shows up only as an id missing from `/ids`.

## Read API

| Endpoint | Purpose |
| -------- | ------- |
| `GET /discover` | Object type, capability, native ACL names, counts. |
| `GET /changes?updated_since=&page_token=&limit=` | Epics at or after the timestamp, oldest first, ordered by `(updated_at, epic_id)`. |
| `GET /ids` | Every live id and version. |
| `GET /epics`, `/epics/{id}` | Epic records. |
| `GET /epics/{id}/acl` | Live native ACL. |

Native ACL names are `project_member`, `eng_leadership`, and `roadmap_readers`. An epic carries `project_member` and `roadmap_readers`, except three leadership epics that stay `eng_leadership` only: `epic-identity-sso`, `epic-billing-usage`, and `epic-platform-cost`. An internal roadmap reader does not see those. An ACL change moves `updated_at` but not `version`. A status or title change increments `version`.

## Admin API

Nothing in the gateway calls these. They stand in for engineering staff.

| Endpoint | Effect |
| -------- | ------ |
| `POST /admin/epics` | Create an epic. |
| `PATCH /admin/epics/{id}` | Change title, status, product key, or team. |
| `PUT /admin/epics/{id}/acl` | Change who can read it. |
| `DELETE /admin/epics/{id}` | Hard-delete the epic. |
| `POST /admin/reset` | Return to the curated seed. |

## Run and test

```bash
uv run pytest demo-harness/engineering/tests
SQLITE_PATH=/tmp/engineering/harness.db PORT=8104 uv run python -m harness_engineering
```

Compose sets `SQLITE_PATH=/data/harness.db` on the `engineeringdata` volume. Metrics: `kbs_harness_up` and `kbs_harness_objects{object_type="epic"}` on `/metrics`.
