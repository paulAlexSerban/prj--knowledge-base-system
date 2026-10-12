# Product connector

Compose service `connector-product`, metrics port 9104. It reads `kbs-product-hooks`, fetches the spec from the harness, writes `raw/product/spec/<id>.md`, and enqueues the change event on the P1 queue. A delete pointer removes the S3 object and checks it is gone before the `delete` event. A second pointer for an unchanged spec is a no-op.

On startup, and every `RECONCILE_SECONDS`, it reads `/ids`. A hook that never arrived, and a delete that never arrived, are caught there.

The production path for the receiver is API Gateway plus Lambda. This process is the part that would sit behind that queue.
