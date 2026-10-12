# ADR 0016: One connector process per source, on one Python interface

Status: accepted for the POC. Decided while the connectors were split out of a single worker.

## Context

The graded problem is ACL normalization, tombstones, and a freshness contract per source. The four sources do not share an API. Extraction has to stay out of retrieval.

## Decision

`connector-core` defines `SourceConnector`: `discover`, `list_changes`, `fetch`, `get_acl`, `list_ids`, and `snapshot_body`. There is no `normalize` method. The snapshot is the raw body plus metadata, and the ingestion worker chunks it. Each source is its own process and its own image (`connector-hr`, `connector-sales`, `connector-product`, `connector-engineering`), built on that library. The "plugin" is the interface plus the YAML contract ([ADR 0017](0017-source-contracts.md)). It is not setuptools entry points, and it is not a process that loads connectors at runtime.

## Alternatives rejected

- In-process plugins, one worker importing every connector. A smaller deploy, and a fault in one source takes down the others. Rejected when the backend was split into one image per process.
- Airbyte or Fivetran. They load a warehouse. They do not own ACL normalization, tombstones, or per-source freshness, which is the work this POC is graded on.
- Federated query at read time. Fresh, and wrong for agents: latency, rate limits, and partial failure become the user's query. A live ACL re-check stays for HR only ([ADR 0018](0018-authorization.md)).
- Change data capture from the source databases. The platform does not own them. A mock SQLite file is not evidence that HubSpot or Drive offers a log.

## Consequences

- Four images to build for four sources. That is the point at this size, and it will get tiresome at forty. Forty is not this POC.
- The demo adapters are cooperative. A real Jira or Drive connector will spend its budget on pagination, auth refresh, and rate limits. The interface is what transfers. The adapters are not evidence that those connectors are cheap.
