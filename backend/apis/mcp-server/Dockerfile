FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
COPY shared/pyproject.toml shared/pyproject.toml
COPY backend/libs/ingest-contract/pyproject.toml backend/libs/ingest-contract/pyproject.toml
COPY backend/libs/knowledge-store/pyproject.toml backend/libs/knowledge-store/pyproject.toml
COPY backend/libs/gateway-core/pyproject.toml backend/libs/gateway-core/pyproject.toml
COPY backend/libs/connector-core/pyproject.toml backend/libs/connector-core/pyproject.toml
COPY backend/apis/knowledge-api/pyproject.toml backend/apis/knowledge-api/pyproject.toml
COPY backend/apis/mcp-server/pyproject.toml backend/apis/mcp-server/pyproject.toml
COPY backend/apis/webhook-receiver/pyproject.toml backend/apis/webhook-receiver/pyproject.toml
COPY backend/workers/ingestion-worker/pyproject.toml backend/workers/ingestion-worker/pyproject.toml
COPY backend/connectors/hr/pyproject.toml backend/connectors/hr/pyproject.toml
COPY demo-harness/common/pyproject.toml demo-harness/common/pyproject.toml
COPY demo-harness/hr/pyproject.toml demo-harness/hr/pyproject.toml
COPY demo-harness/sales/pyproject.toml demo-harness/sales/pyproject.toml
COPY demo-harness/product/pyproject.toml demo-harness/product/pyproject.toml
COPY demo-harness/engineering/pyproject.toml demo-harness/engineering/pyproject.toml

COPY shared shared
COPY backend/libs/ingest-contract backend/libs/ingest-contract
COPY backend/libs/knowledge-store backend/libs/knowledge-store
COPY backend/libs/gateway-core backend/libs/gateway-core
COPY backend/apis/mcp-server backend/apis/mcp-server

RUN uv sync --frozen --no-dev --no-default-groups --package mcp-server

USER nobody
CMD ["python", "-m", "mcp_server"]
