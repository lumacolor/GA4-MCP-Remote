# Remote GA4 MCP — Uvicorn workers=1 (tech §16)
FROM python:3.12-slim-bookworm AS base
WORKDIR /app
RUN useradd -m -u 10001 appuser
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

FROM base AS builder
# Dependencies come from requirements.lock (generated from uv.lock), not from the
# open ranges in pyproject.toml. Without this the image resolves fresh on every
# build, so rebuilding the same commit could produce a different image -- which
# defeats per-customer rollback by version tag.
COPY requirements.lock ./
RUN pip install --upgrade pip && pip install --require-hashes -r requirements.lock
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps .

FROM base AS runtime
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/ga4-remote-mcp /usr/local/bin/ga4-remote-mcp
# Apache-2.0 §4(d): retain LICENSE and NOTICE in distributed artifacts.
COPY LICENSE NOTICE /app/
USER appuser
EXPOSE 8080
ENV GA4MCP_PORT=8080
CMD ["ga4-remote-mcp"]
