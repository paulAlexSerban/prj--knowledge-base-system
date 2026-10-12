# ADR 0022: floci, Terraform, and one Compose file per trust area

Status: decided during the build.

## Context

The POC has to show queues, a bucket, and infrastructure as code, on a laptop, without an AWS account. It also has to show that the data stores are not on the same network as the harness.

## Decision

floci (`floci/floci`, storage mode `memory`) stands in for SQS and S3, published only on `127.0.0.1:4566`. Terraform in `infrastructure/provisioning/terraform/` creates the tier queues, their dead-letter queues, the product hook queue, and the snapshot bucket, with endpoint overrides and the usual test credentials. `terraform-apply` is a one-shot container. State is throwaway because floci forgets everything on restart. Empty state is the correct start. `make tf-apply` is the recovery.

Compose is one file per trust area, included from `compose.yaml`. `make up` creates `kbs-data` with `--internal`. The other networks are `kbs-sources`, `kbs-aws`, `kbs-obs`, and `kbs-tools`. Postgres and Neo4j publish no host ports. Service names are globally unique.

## Alternatives rejected

- LocalStack. Not compared. floci is what this repo applied, and a send and receive on `kbs-p0.fifo` succeeded. That is not a fidelity test. See the risks in the brief.
- Kubernetes, kind, or a cluster. More control plane than four connectors and a queue. The production path is ECS on Fargate, not a local cluster.
- Compose without Terraform. Faster to start, and the queue and bucket definitions would live only in a script. The production path keeps the Terraform.

## Consequences

- A floci restart drops the queues and the bucket. The demo is wrong until `make tf-apply`.
- Test credentials in the provider block are for the emulator. They are not a cloud account, and they must not be copied into one.
- Scheduler is intentionally absent from this Terraform ([ADR 0015](0015-scheduling.md)).
