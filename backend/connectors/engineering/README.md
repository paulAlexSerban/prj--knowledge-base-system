# Engineering connector

Compose service `connector-engineering`, metrics port 9102. It polls the engineering harness, writes one raw JSON object per epic to `kbs-raw-snapshots`, enqueues a change event, and records sync state in Postgres. Deletes show up when the reconciliation sweep reads `/ids`: the S3 object is removed and checked gone before the `delete` event.

The poll loop is `connector-core`. This image is the engineering client and nothing else.
