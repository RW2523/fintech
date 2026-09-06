"""Object storage for document files (docs/07 §1.1, docs/13 §2).

Files live in MinIO. Uploads are presigned so the bytes never pass through the
service, and page renders are cached beside the original. Presigned URLs expire
in ten minutes.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from typing import Any

from app.settings import settings

__all__ = ["DOCUMENTS_BUCKET", "PAGES_BUCKET", "PRESIGN_TTL", "Storage", "storage"]

DOCUMENTS_BUCKET = "documents"
PAGES_BUCKET = "document-pages"
#: docs/13 §2 — a presigned URL is short-lived.
PRESIGN_TTL = timedelta(minutes=10)

#: docs/07 §1.1 — what may be uploaded at all.
ALLOWED_MIME = {"application/pdf", "image/png", "image/jpeg"}
MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 20


@dataclass(frozen=True, slots=True)
class StoredObject:
    bucket: str
    key: str
    size: int
    etag: str


class Storage:
    """Thin MinIO wrapper. Falls back to a local directory when MinIO is absent."""

    def __init__(self, fallback_dir: Any = None) -> None:
        self._client: Any = None
        self._fallback = fallback_dir

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

    def ensure_buckets(self) -> None:
        for bucket in (DOCUMENTS_BUCKET, PAGES_BUCKET):
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)

    def put(
        self, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> StoredObject:
        result = self.client.put_object(
            bucket, key, io.BytesIO(data), length=len(data), content_type=content_type
        )
        return StoredObject(bucket=bucket, key=key, size=len(data), etag=result.etag)

    def get(self, bucket: str, key: str) -> bytes:
        response = self.client.get_object(bucket, key)
        try:
            return bytes(response.read())
        finally:
            response.close()
            response.release_conn()

    def presigned_put(self, bucket: str, key: str) -> str:
        return str(self.client.presigned_put_object(bucket, key, expires=PRESIGN_TTL))

    def presigned_get(self, bucket: str, key: str) -> str:
        return str(self.client.presigned_get_object(bucket, key, expires=PRESIGN_TTL))


@lru_cache(maxsize=1)
def storage() -> Storage:
    return Storage()
