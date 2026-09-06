# One image definition for every domain service (docs/02 §3). arm64.
# Build context is the repository root; SERVICE selects which app to run.
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /uvx /usr/local/bin/

WORKDIR /srv

# Dependencies first: this layer is shared by all eighteen services and only
# rebuilds when the lock file changes.
COPY pyproject.toml uv.lock ./
COPY libs ./libs
RUN uv sync --frozen --no-dev

# Trained models and the code that loads them. `ml` is not an installed
# package, so the services that score import it from here; the rest ignore it.
# Artifacts are part of the image on purpose: a service must not depend on a
# volume to know what model it is serving.
COPY ml ./ml

ARG SERVICE
ENV SERVICE_NAME=${SERVICE}
COPY services/${SERVICE}/app ./app
COPY services/${SERVICE}/alembic* ./

EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${SERVICE_PORT:-8000}"]
