# ADR 0023: A uv workspace, one image per deployable

Status: decided during the build.

## Context

The gateway, the worker, four connectors, and four harnesses share libraries and must not share a process. Dependencies have to be locked once, and a container build has to install one package.

## Decision

The repo is a uv workspace on Python 3.12. Each deployable and each library has its own `pyproject.toml`. `uv.lock` stays at the root. Python images share `docker/python-service.Dockerfile`. Compose passes the package name. The image exports that package from the lockfile, installs the third-party pins, then installs the workspace members the export names. It does not list those members. Libraries (`shared`, `connector-core`, `ingest-contract`, `knowledge-store`, `gateway-core`, `harness-common`) have no image. Feature images run as `nobody`. Harness images stay root because they write a SQLite file on a volume. The notebooks image installs the `notebooks` group instead of a package. There is no Lambda directory, because the function does not exist yet.

## Alternatives rejected

- pip and a per-service virtualenv. The lockfile would drift. The image installs the locked set for one package, and uv names the workspace members instead of a copied list of manifests.
- One installable package for the whole system. A connector image would carry the gateway, or the images would stop matching the process boundaries.
- Poetry or PDM. Not compared. uv is what the Dockerfiles and `make setup` call. A second package manager would not change the design.

## Consequences

- A new dependency is `uv add --package <name>`, and the lockfile is part of the change. A dependency added any other way will not be in the image.
- A new workspace member is a new `pyproject.toml` and a lockfile update. No image file lists the members.
- Root on the harness images is a local exception. It is not the pattern for a service that handles real data.
