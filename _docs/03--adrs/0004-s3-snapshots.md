# ADR 0004: S3 holds the raw snapshot

Status: accepted for the POC.

## Context

The worker must embed the bytes the connector fetched, including on a retry, and a delete must be able to prove the blob is gone. Postgres should not also be the blob store.

## Decision

The bucket `kbs-raw-snapshots` on floci holds one object per source object, at `raw/<source_id>/<object_type>/<object_id>.<ext>`. A change overwrites that key. Bucket versioning keeps the previous bytes. Object metadata carries `kbs-version`, `kbs-content-hash`, `kbs-native-acl`, `kbs-source-updated-at`, and `kbs-snapshot-at`. A put is skipped when the stored hash already matches. A delete removes every version, then a head confirms the key is gone. Postgres `kbs.source_objects` stores the key, hash, version, and ACL. The public access block is on. Terraform creates the bucket. floci keeps it in memory, so a restart drops it and `make tf-apply` is the recovery. Terraform state is throwaway for the same reason.

## Alternatives rejected

- The full payload on the queue. Covered in [ADR 0013](0013-claim-check-messages.md). The queue carries a pointer.
- A shared Compose volume. Fine for one machine, and not the production path. The connector and the worker would be coupled to a host path.
- Postgres large objects. Works, and it puts blob bytes on the same database that serves retrieval. Not evaluated as a bake-off. Rejected so a snapshot read does not compete with search.

## Consequences

- The connector writes the snapshot before it enqueues. A failed enqueue does not advance the watermark, so the next poll sends the pointer again.
- floci is not durable. This is a local stand-in, not evidence that production S3 was tested.

## Production path

The same bucket on AWS, with a remote Terraform state backend, KMS, and a bucket policy. Endpoint overrides go away.
