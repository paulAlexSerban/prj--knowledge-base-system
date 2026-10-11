# Architecture Document: Knowledge Base Gateway

Status: outline only. The body is the next pass. Decisions already taken live in the [project brief](../00--project-brief/00-project-brief.md), the [spikes](../01--spikes/), and the [ADRs](../03--adrs/0001-storage.md).

This document answers the assignment's first deliverable. Each section below states what it must contain so the write-up stays tied to the POC and does not grow a second design.

## Overview

One paragraph: what the gateway does, the two demo questions, and the line between what is built and what is a production path. Point at the system-context diagram and the connector container diagram in the brief.

## Requirements

### Functional requirements

Ingest the four sources, keep indexes current from change events, answer the policy question from authorized chunks with citations, and answer the roadmap question with a graph path.

### Non-functional requirements

The production targets (volume, freshness tiers, 250 ms p95, user-delegated access) and the POC scale assumptions, copied from the brief rather than restated with new numbers.

## Executive summary

The design in a few sentences: event-driven ingestion, Postgres for documents and hybrid search, Neo4j for a small ontology, one service layer behind a REST API and an MCP server, authorization in the data path.

## Architecture

Components, how they interact, and the path of a document from source event to agent tool result. Include the mermaid flow from the brief and walk one create, one update, and one delete. Name the transformation at each hop (normalize, chunk, embed, upsert, authorize).

## Data connection

The connector interface, capability flags, and the four demo sources. Link [spike 01](../01--spikes/01--handling-data-from-distributed-sources-w-diff-formats-access-methods-n-ownership.md). State why federated query and a generic ELT tool were rejected.

## Knowledge representation

Hybrid retrieval, the ontology, which representation is authoritative for which question class, and what was left out (reranker, community summaries, fuzzy entity resolution). Link [spike 02](../01--spikes/02--transforming-data-into-knowledge-that-ai-agents-can-easily-interpret-and-use.md) and [ADR 0001](../03--adrs/0001-storage.md).

## Agent access

MCP tools, the REST API, JWT delegation, pre-filter plus post-check, row-level security, audit, and prompt-injection handling. Link [spike 03](../01--spikes/03--exposing-knowledge-to-ai-agents-though-consistent-access-layer.md).

## Self-maintenance

Events, idempotency, content-hash skip, tombstones, reconciliation, priority queues, retries, and the dead-letter queue. Link [spike 04](../01--spikes/04--self-maintaing-over-time-refelcting-additions-modifications-n-deletions-in-the-source-systems-without-manual-intervention.md).

## Technology choices

One row per component: choice, why, what was excluded and why. Cover FastAPI, Postgres with pgvector, Neo4j, SQS, S3, EventBridge Scheduler, floci, Terraform, OpenRouter with a local embedder, Alloy, Loki, Prometheus, and Grafana. Traces are deferred. Use the ADRs as the long form for storage, observability, and versioning.

## Trade-offs

The short list from the spikes: freshness versus embedding cost, two stores versus one, SQS versus a log, indexed ACL versus read-time check, deterministic edges versus extracted edges, and the 250 ms budget versus the HR re-check.

## Quality evaluation

The golden set (recall@k, MRR, abstention, conflicting sources, access-boundary tests), the dashboard panels in [ADR 0002](../03--adrs/0002-observability.md), and the action when a panel or a golden-set row fails (replay from the dead-letter queue, quarantine a connector, roll back `chunker_version` or `embedding_model`).

## Risks

Point at the brief's risk list. Do not add risks that the spikes have not already accepted.
