"""Shared plumbing for every Credit Intelligence OS service.

Modules
-------
``settings``  environment configuration
``ids``       prefixed ULID identifiers
``hashing``   canonical JSON, digests, hash chains, HMAC signatures
``errors``    the documented error codes and their exceptions
``db``        async engine, sessions, per-service schema names
``outbox``    transactional outbox and its dispatcher
``auth``      demo JWTs, roles, ABAC scopes
``otel``      OpenTelemetry bootstrap
``http``      service-to-service client with correlation
"""

from cio_common.errors import (
    CioError,
    Conflict,
    Forbidden,
    KillSwitchActive,
    LlmUnavailable,
    NotFound,
    PolicyBlocked,
    TokenInvalid,
    ValidationFailed,
)
from cio_common.hashing import GENESIS_HASH, canonical_json, chain_hash, sha256, verify_chain
from cio_common.ids import is_id, new_id
from cio_common.settings import Settings, get_settings

__version__ = "0.1.0"

__all__ = [
    "GENESIS_HASH",
    "CioError",
    "Conflict",
    "Forbidden",
    "KillSwitchActive",
    "LlmUnavailable",
    "NotFound",
    "PolicyBlocked",
    "Settings",
    "TokenInvalid",
    "ValidationFailed",
    "__version__",
    "canonical_json",
    "chain_hash",
    "get_settings",
    "is_id",
    "new_id",
    "sha256",
    "verify_chain",
]
