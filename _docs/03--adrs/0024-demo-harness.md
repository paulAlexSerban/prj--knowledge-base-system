# ADR 0024: A demo harness per source, FastAPI and SQLite

Status: decided during the build.

## Context

The connectors need something to call. The real HR directory, CRM, drive, and issue tracker are not in this repo, and pretending a notebook is those systems hides the network hop the connector actually uses.

## Decision

`demo-harness/` is four processes, one image each, on `harness-common`. Each is FastAPI plus SQLAlchemy on a SQLite file. HR stores people and can bulk-load policies with Faker, and it renders markdown and PDF. Sales stores deals. Engineering stores epics. Product stores spec metadata in SQLite and the markdown body on disk, and posts a webhook pointer on change. Scripts, notebooks, and connectors talk to them over HTTP only. They hold no gateway credentials. The images stay root so the volume is writable ([ADR 0023](0023-uv-workspace.md)).

## Alternatives rejected

- Fixtures loaded straight into Postgres. No connector, no ACL mapping, and no delete path. That skips the assignment.
- One harness with four routers. A smaller Compose file, and a bug in the shared app takes down every source. The four images match the four connectors.
- Calling a public sandbox of a real SaaS product. Accounts, rate limits, and data the repo cannot reset. Not used.

## Consequences

- The harnesses implement the capability the connector expects. They are cooperative. That is not evidence a real source is this easy ([ADR 0016](0016-connector-processes.md)).
- SQLite on a volume is the store. It is not a stand-in for the source's real database engine, and CDC was not attempted against it.
