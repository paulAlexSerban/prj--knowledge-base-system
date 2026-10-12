# Sales connector

Compose service `connector-sales`, metrics port 9103. It reads the sales cursor feed, writes one raw JSON object per deal to `kbs-raw-snapshots`, and enqueues a change event. A delete on the feed removes the S3 object and checks it is gone before the `delete` event. The stored watermark is the cursor, not a timestamp.

The poll loop is `connector-core`. This image is the sales client and nothing else.
