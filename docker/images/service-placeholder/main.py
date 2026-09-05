"""Placeholder service (T-003). Answers /health so compose healthchecks pass.

Replaced by the real FastAPI app for each service in T-008.
"""

from __future__ import annotations

import os

from fastapi import FastAPI

SERVICE_NAME = os.environ.get("SERVICE_NAME", "unknown")
SERVICE_PORT = int(os.environ.get("SERVICE_PORT", "8000"))

app = FastAPI(title=f"cio-{SERVICE_NAME} (placeholder)", version="0.0.0-placeholder")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME, "version": "0.0.0-placeholder"}


@app.get("/")
def root() -> dict[str, str]:
    return {"service": SERVICE_NAME, "note": "placeholder — implemented in T-008"}
