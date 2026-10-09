# Project Brief

## Context
A global company has its operational knowledge spread across many systems, owned by different departments across the globe and stored in very different shapes. Each department (Sales, Product, Engineering, Finance, People, etc) owns its own sources, which are updated continuously and independently: records are created, edited, or deleted every day, with no central coordination and no common schema. 

The company wants to build AI agents that automate process workflows across departments. Some examples: 
- A chatbot that lets employees query HR information (policies, procedures, benefits) in natural language. 
- An agent that connects information from Sales, Engineering and Product to produce data-driven roadmaps. 

None of these agents exist yet. To build them reliably, agents need a way to access knowledge that is accurate, current and authorized at the moment they act. Today there is no system that provides this.

![System Context](00-c4-l1--system-context.png)

## Explicit Requirements

- System should be able to handle data from distributed sources belonging to different departments, with different formats, access methods and ownership.
- System should transform that data into knowledge that AI agents can easily interpret and use.
- System should expose the knowledge to AI agents through a consistent access layer. 
- System should maintain itself over time, reflecting additions, modifications and deletions in the source systems without manual intervention.

## Implicit Requirements
- The platform should make the knowledge available to multiple future agents without requiring every team to implement its own connectors, document processing, permissions, retrieval, and synchronization logic.
- Reduce the effort required to discover and use organizational knowledge while making it safer and cheaper to introduce new AI-enabled workflows

## Assumptions
- scale and volume: 2000 active employees, aprox. 10mil objects (documents, records, emails, spreadsheets, etc.) across all departments (Sales, Product, Engineering, People/HR)
- daily delta volume is aprox. 100,000 modifications, creations, or deletions.
- freshness SLA:
    - High-Priority Security & HR Policy: Update propagation within < 30 seconds.
    - Operational Content (Jira, Confluence): Update propagation within < 5 minutes.
    - Bulk Historical Archives: Batch sync runs every 24 hours.
- security model: Zero-Trust Data Access. Downstream AI agents possess zero elevated privilege by default; every query dynamically inherits the initiating user’s exact identity context, entitlement scopes, and Access Control Lists (ACLs).
- query types:
    1. Unstructured Semantic Search: E.g., "What is our parental leave policy in Germany?"
    2. Structured Multi-Hop Traversal: E.g., "Which High-ARR Sales deals in Q3 depend on unreleased Engineering epics?"
- data heterogeneity:
    - unstructured: HR policies (PDF/DOCX in Google Drive), Product specs (Notion/Confluence), Sales call transcripts (Gong/Zoom).
    - semi-structured: Financial transactions (JSON APIs, NetSuite), Engineering work items (Jira REST API).
    - structured: Sales records (PostgreSQL/HubSpot CRM), People directory (BambooHR/Workday SQL databases).
- access control & authorization:
    - Strict identity propagation: Agents must inherit the precise context and permissions of the requesting user (User-Delegated Access Control).
    - Zero-Trust cross-departmental access: HR data contains PII/SPII (Comp, SSNs) requiring fine-grained Attribute-Based Access Control (ABAC).
- latency & performance targets:
    - unified agent retrieval query latency: <250 ms (p95) for context retrieval (excluding LLM generation time).

## Product principles

1. Authorization before relevance. A highly relevant document is never a valid result if the requester is not allowed to see it.
2. Source systems remain authoritative. The platform indexes and organizes knowledge; it does not silently replace source ownership or become the canonical system of record.
3. Freshness is measurable. Every indexed item has synchronization and source-version metadata, and freshness objectives can be monitored.
4. One access contract, multiple integrations. Agents consume a stable interface instead of connecting directly to every department's systems.
5. Deterministic controls, probabilistic intelligence. Use conventional code for permissions, synchronization, deletion, validation, and state transitions. Use LLMs where language understanding or semantic extraction genuinely adds value.

## Primary users and jobs to be done

| User / consumer               | Need                                                           | Expected outcome                                   |
| ----------------------------- | -------------------------------------------------------------- | -------------------------------------------------- |
| Employees                     | Ask questions about company policies and procedures            | Accurate answers with traceable sources            |
| Department owners             | Publish and maintain operational knowledge in existing systems | No duplicate manual knowledge management           |
| AI agents                     | Retrieve authorized facts from multiple departments            | Consistent, machine-readable context               |
| Platform engineers            | Add and maintain integrations                                  | Reusable connectors and observable synchronization |
| Security and governance teams | Enforce access restrictions and trace usage                    | Auditable, policy-compliant access                 |