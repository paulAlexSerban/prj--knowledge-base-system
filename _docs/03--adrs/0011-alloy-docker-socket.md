# ADR 0011: Alloy reads the Docker socket, read-only

Status: accepted for the POC. This is a trust-boundary exception.

## Context

Alloy has to collect logs from processes that are not on the `kbs-obs` network, including Postgres, Neo4j, and floci. Those processes should not join the observability network just to be tailed.

## Decision

`compose.observability.yaml` mounts `/var/run/docker.sock` into Alloy read-only. Alloy itself joins `kbs-obs` only. It has no path of its own onto `kbs-data`. Log collection keeps only the feature services (the gateways, the webhook receiver, the ingestion worker, the four connectors, the four harnesses, and the chat page). A line with no matching service label is dropped before Loki. Probe paths are dropped as well. Postgres, Neo4j, and floci stdout therefore do not land in Loki.

## Alternatives rejected

- A logging sidecar on every service. More moving parts than one read-only mount, for the same lines.
- Putting every container on `kbs-obs`. That network would then reach the data stores. The socket is the smaller exception.
- Skipping those logs. The feature logs are the ones the boards use. The socket is how the filter runs. The dropped lines are the point of the filter, not an accident.

## Consequences

- The socket can read every container's stdout. Those lines must not contain store passwords or retrieved text. Compose does not put passwords in command arguments.
- A container outside the keep-list is invisible in Loki even when it is unhealthy. Its healthcheck and, if it is scraped, its metrics are the signal instead.
