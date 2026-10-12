# ADR 0012: SQS FIFO, one queue per freshness tier

Status: accepted for the POC. Decided while the ingestion pipeline was built.

## Context

Connectors and the worker have to survive a crash, keep two updates of one object in order, and stop a backfill from blocking a policy change. The assumed rate is about 1.2 events per second. That is an assumption from the brief, not a load test. Spike 04 rejects a log broker at that rate.

## Decision

Three FIFO queues on floci, `kbs-p0.fifo`, `kbs-p1.fifo`, and `kbs-p2.fifo`, each with a FIFO dead-letter queue. The message group is `source_id:object_id`, so two updates of one object stay ordered and a poison message does not block other objects. Visibility timeout is 60 seconds. Terraform sets `maxReceiveCount` to 5. floci's redrive was not observed, so the worker also copies the body to the dead-letter queue when `ApproximateReceiveCount` is greater than 5, then deletes it from the tier queue. `make dlq-replay` sends it back with a new deduplication id, because FIFO would drop the original id for five minutes. One worker drains P0, then P1, then P2. Nothing publishes to P2 yet. The product hook queue is separate ([ADR 0013](0013-claim-check-messages.md)).

## Alternatives rejected

- Kafka or Kinesis. Replay and a second consumer are real production needs. At this rate they are not the POC's need. SQS still shows workers, retries, and ordering, and it is what floci and the production path both speak.
- RabbitMQ. Not evaluated. It would be another broker to operate, with no production-path reason.
- One queue for every tier. A bulk backfill then blocks a policy update.

## Consequences

- The queue is at-least-once. The idempotent upsert is the exactly-once ([ADR 0003](0003-postgres-then-neo4j.md)).
- A local apply and one send/receive checked the API shape. That is not a test of ordering under load, and it is not a test of production SQS.
- FIFO throughput limits are accepted. They were not hit, and they were not measured.

## Production path

The same queues on AWS. Move to Kinesis or Kafka only when a second consumer needs replay without disturbing ingestion.
