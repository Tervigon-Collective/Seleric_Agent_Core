# Seleric Agent Core MCP (streamable-http on :8765)
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

COPY catalogue ./catalogue
COPY config.yaml ./config.yaml
COPY scripts ./scripts

RUN mkdir -p /data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    SELERIC_MCP_DB=/data/seleric_mcp.db

EXPOSE 8765

CMD ["seleric-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8765"]
