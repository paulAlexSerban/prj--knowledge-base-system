# syntax=docker/dockerfile:1
# One recipe for every Python image. uv reads uv.lock and names the workspace
# members this package needs, so the file does not list them. Compose passes
# PACKAGE, or ONLY_GROUP for the notebooks image.
#
# uv 0.11.2 has --no-emit-workspace and --no-editable, not --only-emit-workspace.
# One --no-editable export is split into third-party pins and workspace paths.

FROM python:3.12-slim-bookworm AS base

COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Only the manifests. A later source edit does not change this stage's output,
# so the third-party install stays cached.
FROM base AS manifests
RUN --mount=type=bind,source=.,target=/src,ro \
    python - <<'PY'
import shutil
from pathlib import Path

src = Path("/src")
dst = Path("/export")
names = {"pyproject.toml", "uv.lock"}
for path in src.rglob("*"):
    if path.name not in names or not path.is_file():
        continue
    target = dst / path.relative_to(src)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
PY

FROM base AS export
ARG PACKAGE=
ARG ONLY_GROUP=
COPY --from=manifests /export /app
RUN set -eu; \
    if [ -n "$ONLY_GROUP" ] && [ -n "$PACKAGE" ]; then \
      echo "Set PACKAGE or ONLY_GROUP, not both" >&2; \
      exit 1; \
    fi; \
    if [ -n "$ONLY_GROUP" ]; then \
      uv export --frozen --no-dev --no-default-groups --only-group "$ONLY_GROUP" \
        --no-editable --no-hashes --no-header --no-annotate -o /tmp/all.txt; \
    elif [ -n "$PACKAGE" ]; then \
      uv export --frozen --no-dev --no-default-groups --package "$PACKAGE" \
        --no-editable --no-hashes --no-header --no-annotate -o /tmp/all.txt; \
    else \
      echo "PACKAGE or ONLY_GROUP is required" >&2; \
      exit 1; \
    fi; \
    if grep -q '^-e ' /tmp/all.txt; then \
      echo "uv export emitted editable installs; expected path lines" >&2; \
      exit 1; \
    fi; \
    mkdir -p /out; \
    grep '^\./' /tmp/all.txt | sed 's|^\./||' > /out/members.txt || true; \
    grep -v '^\./' /tmp/all.txt > /out/requirements.txt || true; \
    if [ -n "$PACKAGE" ] && [ ! -s /out/members.txt ]; then \
      echo "export named no workspace members for $PACKAGE" >&2; \
      exit 1; \
    fi

# The member trees named by the export, and nothing else.
FROM base AS sources
COPY --from=export /out/members.txt /tmp/members.txt
RUN --mount=type=bind,source=.,target=/src,ro \
    set -eu; \
    mkdir -p /out/src; \
    while IFS= read -r member; do \
      [ -n "$member" ] || continue; \
      mkdir -p "/out/src/$(dirname "$member")"; \
      cp -a "/src/$member" "/out/src/$member"; \
    done < /tmp/members.txt

# Wheels, not editable installs. uv pip install -e rejects a member on its own,
# because tool.uv.sources points at the workspace root. Build from a tree that
# still has every member manifest, then install the wheels with --no-deps.
FROM base AS wheels
COPY --from=export /out/members.txt /tmp/members.txt
COPY --from=manifests /export /build
COPY --from=sources /out/src /build
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eu; \
    mkdir -p /out/wheels; \
    if [ -s /tmp/members.txt ]; then \
      while IFS= read -r member; do \
        [ -n "$member" ] || continue; \
        uv build --wheel --out-dir /out/wheels "/build/$member"; \
      done < /tmp/members.txt; \
    fi

FROM base AS runtime
ARG APP_USER=nobody
ARG BAKE_FASTEMBED=0
ARG ENTRYPOINT_MODULE=
ARG APP_WORKDIR=/app

COPY --from=export /out/requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /app/.venv \
    && uv pip install --python /app/.venv/bin/python -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY --from=wheels /out/wheels /tmp/wheels
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eu; \
    if find /tmp/wheels -name '*.whl' -print -quit | grep -q .; then \
      uv pip install --python /app/.venv/bin/python --no-deps /tmp/wheels/*.whl; \
    fi; \
    rm -rf /tmp/wheels

COPY --from=sources /out/src /app

# Bake the embedding model into the image so the demo never downloads at run time.
# Offline mode is set after the download. "0" leaves Hugging Face downloads enabled.
ENV FASTEMBED_CACHE_PATH=/opt/fastembed
RUN if [ "$BAKE_FASTEMBED" = "1" ]; then \
      python -c "from knowledge_store.embedder import FASTEMBED_ID; from fastembed import TextEmbedding; TextEmbedding(FASTEMBED_ID)" \
      && chmod -R a+rX /opt/fastembed; \
    fi
ENV HF_HUB_OFFLINE=${BAKE_FASTEMBED}

ENV ENTRYPOINT_MODULE=${ENTRYPOINT_MODULE}
WORKDIR ${APP_WORKDIR}
USER ${APP_USER}
CMD ["sh", "-c", "exec python -m \"$ENTRYPOINT_MODULE\""]
