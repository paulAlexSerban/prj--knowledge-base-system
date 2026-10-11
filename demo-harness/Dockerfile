FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
COPY demo-harness ./demo-harness

RUN uv sync --frozen --no-dev --only-group harness --no-install-project

# Stay root so the SQLite volume at /data is writable. This is a local stub.
CMD ["python", "demo-harness/common/app.py"]
