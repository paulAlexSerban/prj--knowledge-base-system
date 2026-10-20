# SQS and dead-letter queues

Queues live on floci, created by Terraform. They are FIFO. Kafka was rejected at this volume (about 1.2 events per second in the brief). The design is [ADR 0012](../03--adrs/0012-sqs-fifo.md) and [ADR 0013](../03--adrs/0013-claim-check-messages.md).

The queue never carries the document. The connector writes S3 first, then sends a `ChangeEvent`: source, object, type, version or hash, `occurred_at`, and `s3_pointer`. A delete has no pointer, because the blob is already gone. The worker fetches the snapshot. That is claim-check.

## The queues

| Queue                    | Who writes                  | Who reads              | Dedup                                                      |
| ------------------------ | --------------------------- | ---------------------- | ---------------------------------------------------------- |
| `kbs-p0.fifo`            | HR; any ACL change          | Ingestion worker first | Content-based on, but every send still sets an explicit id |
| `kbs-p1.fifo`            | Sales, Engineering, Product | Worker if P0 is empty  | Same                                                       |
| `kbs-p2.fifo`            | Nobody yet                  | Worker last            | Same                                                       |
| `kbs-product-hooks.fifo` | Webhook receiver            | Product connector      | Off, so two edits of one spec both arrive                  |

Each of those has a matching `*-dlq.fifo`. Visibility timeout is 60 seconds, longer than one embed call, so a crashed worker's message becomes visible again. Terraform sets `maxReceiveCount` to 5.

P0 is HR body and ACL. P1 is the other three sources. P2 is documented as daily backfill. Nothing publishes to P2. One worker process drains in that order: long-poll P0, then a zero-wait receive on P1, then P2.

## Group and dedup

Message group is `source_id:object_id`. Two updates of one object stay ordered. A poison message does not block another object.

Dedup id is `source_id:object_id:event_type:content_hash` (version if there is no hash). An ACL-only change still moves the snapshot hash, so it is a new send. An identical retry inside the five-minute FIFO window is dropped. SQS ids longer than 128 characters are truncated with a hash (`fit_sqs_id`).

`traceparent` travels as a message attribute, not in the body.

## Happy path

1. Connector (or product connector after a hook) publishes with group and dedup ids.
2. Worker receives up to 10 messages. It parses `ChangeEvent`, writes Postgres then Neo4j, then deletes the message (ack).
3. A matching content hash skips embed and still merges the graph. A failed Neo4j write leaves the message unacked. The retry whose hash matches still writes the graph. The idempotent upsert is the exactly-once.

`FAIL_OBJECT_ID` is a demo switch. The worker rejects that object on purpose until it lands on the DLQ.

## Failure and the DLQ

A handler error does not ack. After 60 seconds SQS delivers the message again and bumps `ApproximateReceiveCount`.

Terraform attaches a redrive policy. floci's redrive was not observed, so the worker also copies the body to the tier DLQ when the receive count is greater than 5, then deletes it from the tier queue. If floci did apply redrive, the message is already gone before that count. Either way a poison message leaves the group.

An invalid JSON body is not acked either. It retries until the same receive-count path moves it. The product connector does the same for a bad hook: it does not ack, so the pointer stays and eventually hits `kbs-product-hooks-dlq.fifo`.

Depth gauges: `kbs_queue_depth` and `kbs_dlq_depth` on the tier queues, `kbs_hook_queue_depth` on the hook queue.

## Replay

`make dlq-replay` runs `tools/scripts/dlq_replay.py`. For each pair (DLQ, tier queue) it receives, sends to the tier queue, then deletes from the DLQ.

The new dedup id is `{original}:replay:{message_id}`. Reusing the original id would be accepted and then dropped for five minutes. The message group stays the same, so per-object order is unchanged. `traceparent` is forwarded.

Unset `FAIL_OBJECT_ID` before a replay, or the same object dies again.

## What it is not

- Not a log. There is no independent replay of history. The DLQ plus `make dlq-replay` is the recovery path.
- Not cross-object order. Only one object is ordered.
- Not the product hook as a change event. The receiver only enqueues a pointer. The product connector fetches the spec and then publishes on P1.
- Not Kafka. Move to a log only when a second consumer needs replay without disturbing ingestion.
