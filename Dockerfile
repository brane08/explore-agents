# Meta-agent orchestrator image (docs/UI-PLANE.md) — task console + SSE.
# Runs `python -m orchestrator` against a catalog tree baked into the image
# (skills/, mcp/, a2a/, templates/, agents/, models/, catalog.lock.yaml,
# trust.yaml, tags.yaml). OpenShift-friendly: no hardcoded UID, group-writable
# state dir so an arbitrary assigned UID (gid 0) can still write the sqlite db.

FROM python:3.11-slim AS base

RUN pip install --no-cache-dir uv

WORKDIR /app

# Dependency layer first for build cache reuse.
COPY pyproject.toml uv.lock ./
COPY tooling/lockbuild/pyproject.toml tooling/lockbuild/pyproject.toml
COPY tooling/certify/pyproject.toml tooling/certify/pyproject.toml
COPY tooling/orchestrator/pyproject.toml tooling/orchestrator/pyproject.toml
RUN uv sync --frozen --no-install-project --no-dev

# Source + catalog content.
COPY tooling/ tooling/
COPY skills/ skills/
COPY mcp/ mcp/
COPY a2a/ a2a/
COPY templates/ templates/
COPY agents/ agents/
COPY models/ models/
COPY tags.yaml trust.yaml catalog.lock.yaml ./

RUN uv sync --frozen --no-dev

# Writable state dir (sqlite db) for an arbitrary non-root UID under gid 0
# (OpenShift restricted SCC default).
RUN mkdir -p /app/state \
    && chgrp -R 0 /app/state \
    && chmod -R g=u /app/state
ENV ORCH_DB_PATH=/app/state/orchestrator.sqlite3
ENV ORCH_CATALOG_ROOT=/app
ENV ORCH_HOST=0.0.0.0
ENV ORCH_PORT=8010
ENV HOME=/app/state

EXPOSE 8010
USER 1000:0

ENTRYPOINT ["uv", "run", "python", "-m", "orchestrator"]
