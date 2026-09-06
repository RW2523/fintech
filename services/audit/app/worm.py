"""Daily export of the audit trail to write-once storage (docs/04 §6).

The append-only trigger stops a stray query. It does not stop somebody with
the database password, and the whole point of an audit trail is that it holds
against the people who run the system. So each day's entries are exported to a
bucket with object lock and a retention period, where the storage itself
refuses to overwrite them.

The export records the chain head at the moment it ran. A later export whose
first `prev_hash` does not match the previous export's head means entries were
removed in between, and the gap is visible without reading a single entry.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from typing import Any

from app.settings import settings
from cio_common.hashing import sha256
from cio_common.ids import derived_id

__all__ = ["BUCKET", "RETENTION_DAYS", "ExportResult", "Worm", "worm"]

BUCKET = "audit-worm"

#: docs/04 §8 — audit is kept ten years.
RETENTION_DAYS = 3653


@dataclass(frozen=True, slots=True)
class ExportResult:
    export_id: str
    day: date
    first_seq: int
    last_seq: int
    entries: int
    object_key: str
    sha256: str
    head_hash: str
    locked: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "export_id": self.export_id,
            "day": self.day.isoformat(),
            "first_seq": self.first_seq,
            "last_seq": self.last_seq,
            "entries": self.entries,
            "object_key": self.object_key,
            "sha256": self.sha256,
            "head_hash": self.head_hash,
            "locked": self.locked,
        }


class Worm:
    """The write-once bucket. Object lock is requested, never assumed."""

    def __init__(self) -> None:
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            from minio import Minio

            config = settings()
            self._client = Minio(
                config.minio_endpoint,
                access_key=config.minio_root_user,
                secret_key=config.minio_root_password,
                secure=False,
            )
        return self._client

    def ensure_bucket(self) -> bool:
        """Create the bucket with object lock, and say whether lock is on.

        Object lock can only be set when a bucket is created. If the bucket
        already exists without it, that is reported rather than worked around:
        a bucket that says it is WORM and is not is worse than one that admits
        it is not.
        """
        if not self.client.bucket_exists(BUCKET):
            self.client.make_bucket(BUCKET, object_lock=True)
        try:
            self.client.get_object_lock_config(BUCKET)
        except Exception:
            return False
        return True

    def put(self, key: str, body: bytes, *, locked: bool = True) -> None:
        """Write the day's entries, with a retention period when the bucket
        will take one.

        A retention header on a bucket without object lock is rejected
        outright, so a bucket that lost its lock would fail every export and
        the archive would silently stop. Writing without the header instead
        keeps the copy and lets `ensure_bucket` report the truth about it.
        """
        retention = None
        if locked:
            from datetime import UTC, datetime

            from minio.commonconfig import GOVERNANCE
            from minio.retention import Retention

            retention = Retention(GOVERNANCE, datetime.now(UTC) + timedelta(days=RETENTION_DAYS))

        self.client.put_object(
            BUCKET,
            key,
            io.BytesIO(body),
            length=len(body),
            content_type="application/x-ndjson",
            retention=retention,
        )


@lru_cache(maxsize=1)
def worm() -> Worm:
    return Worm()


def object_key(day: date) -> str:
    """Partitioned by day, so a retention policy can act on a prefix."""
    return f"{day:%Y/%m/%d}/audit-{day:%Y%m%d}.ndjson"


def serialise(entries: list[dict[str, Any]]) -> bytes:
    """One JSON object per line, in sequence order.

    Newline-delimited rather than a single array so a reader can verify the
    chain by streaming, without holding a day of entries in memory.
    """
    return "".join(json.dumps(entry, sort_keys=True, default=str) + "\n" for entry in entries).encode()


def export_for(day: date, entries: list[dict[str, Any]], head_hash: str, locked: bool) -> ExportResult:
    body = serialise(entries)
    return ExportResult(
        export_id=derived_id("aud", "export", day.isoformat()),
        day=day,
        first_seq=entries[0]["seq"] if entries else 0,
        last_seq=entries[-1]["seq"] if entries else 0,
        entries=len(entries),
        object_key=object_key(day),
        sha256=sha256(body),
        head_hash=head_hash,
        locked=locked,
    )
