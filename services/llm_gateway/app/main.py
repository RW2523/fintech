"""llm-gateway service entrypoint (docs/06 §6)."""

from __future__ import annotations

from app.routes import router
from app.settings import settings
from cio_common.service import create_app

app = create_app("llm_gateway", version="0.1.0", routers=[router], settings=settings())
