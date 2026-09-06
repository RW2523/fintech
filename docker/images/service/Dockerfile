# One image definition for every domain service (docs/02 §3). arm64.
# Build context is the repository root; SERVICE selects which app to run.
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

# tesseract and poppler are the document pipeline's readers: extraction anchors
# every value to an OCR word span, and without them a field has no bounding box
# and cannot be shown to an officer on the page (docs/07 §1.3, docs/09 §3.7).
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates tini \
      tesseract-ocr tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /uvx /usr/local/bin/

WORKDIR /srv

# Dependencies first: this layer is shared by all eighteen services and only
# rebuilds when the lock file changes.
COPY pyproject.toml uv.lock ./
COPY libs ./libs
# --all-extras, not a per-service list: the modelling, dataframe and document
# stacks are optional in the workspace, and a service that imports one without
# it installed fails at import time with the routers silently missing while
# /health still answers. One resolved layer shared by all eighteen services is
# cheaper than eighteen chances to get the list wrong.
RUN uv sync --frozen --no-dev --all-extras

# Trained models and the code that loads them. `ml` is not an installed
# package, so the services that score import it from here; the rest ignore it.
# Artifacts are part of the image on purpose: a service must not depend on a
# volume to know what model it is serving.
COPY ml ./ml

# The agent runtime's guardrails and prompt assembly live in `ai/`, which is
# not an installed package either, for the same reason `ml/` is not.
COPY ai ./ai

# Policy packs are versioned data the policy and committee services read at
# runtime. They belong in the image for the same reason model artifacts do:
# a service must not depend on a mounted volume to know which rules it runs.
COPY policy_packs ./policy_packs

# Measured thresholds the longitudinal engine reads at runtime (docs/07 §4.3).
COPY config ./config

ARG SERVICE
ENV SERVICE_NAME=${SERVICE}
COPY services/${SERVICE}/app ./app
COPY services/${SERVICE}/alembic* ./

EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${SERVICE_PORT:-8000}"]
