"""OSS (Alibaba Object Storage) primitives shared by every capability.

Credentials, bucket construction, and content-addressed upload + signed URL. OSS is optional
everywhere: callers check ``is_upload_configured()`` and upload a local file only when they
explicitly need to hand an external service a URL (e.g. oversized media for a cloud API); the
default path everywhere is local/inline, so importing this module never requires the SDK.
The ``oss2`` SDK is an optional dependency (the ``oss`` extra) and is imported lazily.
"""

from __future__ import annotations

import hashlib
import logging
import os
import posixpath

from shared.env import get_env, get_int_env

log = logging.getLogger(__name__)

_MD5_CHUNK = 8 * 1024 * 1024


def credentials() -> tuple[str, str]:
    """(access_key_id, access_key_secret) from OSS_AK / OSS_SK."""
    return get_env("OSS_AK", ""), get_env("OSS_SK", "")


def to_public_endpoint(endpoint: str) -> str:
    """Convert an internal OSS endpoint to a public one."""
    return endpoint.replace("-internal", "")


def bucket(endpoint: str, bucket_name: str):
    """Return an oss2.Bucket. Raises RuntimeError when credentials are unset."""
    import oss2

    ak, sk = credentials()
    if not ak or not sk:
        raise RuntimeError("OSS credentials not set. Set OSS_AK/OSS_SK, OSS_ENDPOINT, OSS_BUCKET.")
    return oss2.Bucket(oss2.Auth(ak, sk), to_public_endpoint(endpoint), bucket_name)


def url_expiry() -> int:
    """Signed-URL TTL in seconds (OSS_URL_EXPIRY, default 7200)."""
    return get_int_env("OSS_URL_EXPIRY")


def is_upload_configured() -> bool:
    """True when uploading is actually possible: creds + endpoint + OSS_BUCKET **and** oss2 installed.

    Checking the SDK here (not just the env) keeps callers from picking the OSS path and then failing
    mid-request — a half-configured setup degrades to whatever inline fallback the caller has.
    """
    ak, sk = credentials()
    if not (ak and sk and get_env("OSS_ENDPOINT") and get_env("OSS_BUCKET")):
        return False
    try:
        import oss2  # noqa: F401
    except ImportError:
        log.warning("OSS_* is configured but the oss2 SDK is missing — install the 'oss' extra to use it")
        return False
    return True


def upload_and_sign(path: str, *, key_prefix: str = "", expires: int | None = None) -> str:
    """Upload a local file to OSS_BUCKET and return a signed HTTPS URL.

    The object key is content-addressed (``<key_prefix>/<md5><ext>``), so re-uploading the same bytes
    overwrites the same object instead of littering the bucket — and a repeated call on an unchanged
    file is effectively idempotent.
    """
    endpoint = get_env("OSS_ENDPOINT")
    bucket_name = get_env("OSS_BUCKET")
    if not endpoint or not bucket_name:
        raise RuntimeError("OSS upload needs OSS_ENDPOINT + OSS_BUCKET (plus OSS_AK/OSS_SK)")

    # Reject a bad TTL before uploading anything.
    expires = expires if expires is not None else url_expiry()
    digest = hashlib.md5()  # noqa: S324 — a content address, not a security digest
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_MD5_CHUNK), b""):
            digest.update(chunk)
    key = posixpath.join(key_prefix.strip("/"), f"{digest.hexdigest()}{os.path.splitext(path)[1]}")

    b = bucket(endpoint, bucket_name)
    with open(path, "rb") as fh:
        b.put_object(key, fh)
    url = b.sign_url("GET", key, expires, slash_safe=True)
    log.info("uploaded %.1f MB to oss://%s/%s", os.path.getsize(path) / 1e6, bucket_name, key)
    return url.replace("http://", "https://")
