# ADR 0013: The queue carries a pointer, not the document

Status: accepted for the POC. Decided while the change-event model was built.

## Context

The worker has to see the same bytes the connector fetched, including on a retry. The message also has to stay small, and a delete has to be valid after the blob is already gone.

## Decision

The connector writes the S3 snapshot, then sends a `ChangeEvent`: `source_id`, `object_id`, `event_type`, `version`, `content_hash`, `occurred_at`, and `s3_pointer`. A delete is valid without a pointer, because the connector has already removed the object. Every other event must name the key. The FIFO deduplication id includes the content hash, so an ACL-only change is a new send and an identical retry is not. The worker fetches the snapshot. It does not trust a body stuffed into the message, because there is not one.

Terraform also sets `content_based_deduplication` on the three tier queues. Every send from this code passes an explicit id, so that flag does not decide those messages. A sender that omitted the id would dedupe on the body instead. The product hook queue (`kbs-product-hooks.fifo`) turns content-based deduplication off, so two edits of one spec both arrive. The connector no-ops a pointer whose version and ACL already match.

## Alternatives rejected

- The document bytes in the message. SQS caps a body at 256 KB, a retry would depend on the message still holding the bytes, and a delete would carry a payload that should already be gone.
- A database outbox polled by the worker, with no queue. Loses the tier split and the dead-letter queue. Not built.

## Consequences

- A failed publish after a successful put leaves the previous sync row in place. The next poll sends the pointer again.
- The hook queue and the tier queues are different contracts. A hook is not a change event.
