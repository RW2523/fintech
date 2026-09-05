"""llm_gateway service entrypoint.

Endpoints arrive in the task that implements this service; the scaffolding
gives it health, version, correlation headers, error envelopes and tracing.
"""

from __future__ import annotations

from app.settings import settings
from cio_common.service import create_app

app = create_app("llm_gateway", version="0.1.0", settings=settings())
