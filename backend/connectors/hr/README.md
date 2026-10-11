# HR connector

Compose service `connector-hr`, metrics port 9100. It polls the HR harness, writes one raw object per source id to `kbs-raw-snapshots`, and records sync state in Postgres.

Sales, Product, and Engineering are not this process. Each of those connectors will be its own image when it is built. The sync engine is `connector-core`.
