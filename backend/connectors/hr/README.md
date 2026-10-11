# HR connector

Compose service `connector-hr`, metrics port 9100. It polls the HR harness, writes one raw object per source id to `kbs-raw-snapshots`, enqueues a change event, and records sync state in Postgres. The enqueue happens after the S3 put and before the state row. A delete removes the S3 object and checks it is gone before the `delete` event.

Sales, Product, and Engineering are not this process. Each of those connectors will be its own image when it is built. The sync engine is `connector-core`.
