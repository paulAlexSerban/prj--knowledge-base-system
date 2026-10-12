# ADR 0023: A uv workspace, one image per deployable

Status: decided during the build.

## Context

The gateway, the worker, four connectors, and four harnesses share libraries and must not share a process. Dependencies have to be locked once, and a container build has to install one package.

## Decision

The repo is a uv workspace on Python 3.12. Each deployable and each library has its own `pyproject.toml`. `uv.lock` stays at the root. A Docker image runs `uv sync --frozen --no-dev --no-default-groups --package <name>`. Libraries (`shared`, `connector-core`, `ingest-contract`, `knowledge-store`, `gateway-core`, `harness-common`) have no image. Feature images run as `nobody`. Harness images stay root because they write a SQLite file on a volume. There is no Lambda directory, because the function does not exist yet.

## Alternatives rejected

- pip and a per-service virtualenv. The lockfile would drift, and the workspace install is the command the Dockerfiles already run.
- One installable package for the whole system. A connector image would carry the gateway, or the images would stop matching the process boundaries.
- Poetry or PDM. Not compared. uv is what the Dockerfiles and `make setup` call. A second package manager would not change the design.

## Consequences

- A new dependency is `uv add --package <name>`, and the lockfile is part of the change. A dependency added any other way will not be in the image.
- Root on the harness images is a local exception. It is not the pattern for a service that handles real data.
