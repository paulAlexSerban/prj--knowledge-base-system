# ADR 0015: Poll in process now, Scheduler as a production path

Status: accepted for the POC. The scheduler is not built.

## Context

HR's freshness target is 30 seconds. EventBridge Scheduler cannot fire faster than once a minute, and a schedule needs an IAM role this Terraform does not create. P1's target is five minutes. Reconciliation has to run without a person.

## Decision

What runs is an in-process loop. HR polls every `P0_POLL_SECONDS` (10 in Compose). Sales and Engineering poll every `POLL_SECONDS` (60). Product does not poll. It consumes `kbs-product-hooks`. The ingestion worker reconciles the index on a timer, `RECONCILE_INDEX_SECONDS` (60), not from a P2 message. Terraform does not create schedules. The comment in `main.tf` says they are intentionally absent.

EventBridge Scheduler is the production path for P1 and P2 polls and for the reconciliation tick. P0 stays in the connector, because of the one-minute minimum. The P2 queue exists. Nothing publishes to it.

## Alternatives rejected

- Scheduler for P0. It cannot meet the 30-second tier.
- A full reindex on a schedule. It re-embeds unchanged content or misses the tier. The rebuild command is `make reindex`, and only for a chunker or model change ([ADR 0006](0006-chunk-versioning.md)).
- Wiring Scheduler against floci in the POC. floci lists Scheduler. A schedule that nothing runs is a diagram, not a decision. It was left unwired on purpose.

## Consequences

- A connector process that is down does not poll. There is no external ticker to notice, other than the lag metric.
- Calling the production path "the scheduler" in a diagram is only honest when the box says it is not wired.
