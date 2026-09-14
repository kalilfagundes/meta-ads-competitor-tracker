"""S3-compatible object storage (boto3).

Works against the Garage bundled with docker compose, AWS S3, Cloudflare R2 or any
S3 API. Endpoint, bucket and credentials come from the environment
(`tracker.config`, written to `.env` by the setup script). Object keys are the
`creatives.media_*_key` (see CONTEXT.md).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from .config import get_settings

logger = logging.getLogger(__name__)

_MISSING = ("404", "NoSuchKey", "NotFound", "NoSuchBucket")


@lru_cache(maxsize=1)
def _client():
    s = get_settings()
    has_keys = bool(s.s3_access_key_id)
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint or None,   # None = default AWS endpoint
        # No access key = boto3's own credential chain (e.g. an IAM role on AWS).
        aws_access_key_id=s.s3_access_key_id if has_keys else None,
        aws_secret_access_key=s.s3_secret_access_key if has_keys else None,
        region_name=s.s3_region or "auto",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if s.s3_force_path_style else "auto"},
            connect_timeout=10, retries={"max_attempts": 2},
        ),
    )


def _bucket() -> str:
    return get_settings().s3_bucket


def ensure_bucket() -> None:
    """Create the bucket if it doesn't exist (idempotent). Handy on a new bucket's 1st boot."""
    client, bucket = _client(), _bucket()
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)
        logger.info("bucket created: %s", bucket)


def upload_file(key: str, path, content_type: str | None = None) -> None:
    """Upload a file from disk (multipart for large files, never read into memory whole)."""
    extra = {"ContentType": content_type} if content_type else {}
    _client().upload_file(str(path), _bucket(), key, ExtraArgs=extra)
    logger.debug("upload %s (file, %s)", key, content_type or "?")


def upload_bytes(key: str, data: bytes, content_type: str | None = None) -> None:
    """Upload bytes to storage under the given key."""
    extra = {"ContentType": content_type} if content_type else {}
    _client().put_object(Bucket=_bucket(), Key=key, Body=data, **extra)
    logger.debug("upload %s (%d bytes, %s)", key, len(data), content_type or "?")


def key_exists(key: str) -> bool:
    """True if an object already exists at this key (idempotent re-runs)."""
    try:
        _client().head_object(Bucket=_bucket(), Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code", "") in _MISSING:
            return False
        raise


class RangeNotSatisfiable(Exception):
    """The requested byte range lies outside the object."""


@dataclass
class StoredObject:
    """An object being read from storage. `chunks()` streams the body and closes it."""
    body: object
    content_type: str | None
    content_length: int | None
    content_range: str | None      # set when a byte range was served

    def chunks(self, size: int = 64 * 1024):
        try:
            yield from self.body.iter_chunks(size)
        finally:
            self.body.close()

    def close(self) -> None:
        self.body.close()


def open_object(key: str, byte_range: str | None = None) -> StoredObject | None:
    """Open an object for streaming, optionally only `byte_range` (an HTTP Range
    header, e.g. "bytes=0-1023"). None if it doesn't exist."""
    extra = {"Range": byte_range} if byte_range else {}
    try:
        resp = _client().get_object(Bucket=_bucket(), Key=key, **extra)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _MISSING:
            return None
        if code == "InvalidRange":
            raise RangeNotSatisfiable(key) from exc
        raise
    return StoredObject(
        body=resp["Body"], content_type=resp.get("ContentType"),
        content_length=resp.get("ContentLength"),
        content_range=resp.get("ContentRange") if byte_range else None,
    )
