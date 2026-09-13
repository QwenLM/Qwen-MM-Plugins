"""DashScope-managed temporary OSS uploads for oversized model inputs.

The upload policy is tied to the model and API key used for inference. Uploaded objects are
temporary (currently retained by DashScope for about 48 hours) and are addressed by ``oss://`` URLs;
model calls that consume one must send ``X-DashScope-OssResourceResolve: enable``.
"""

from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from shared.env import get_env

DEFAULT_UPLOAD_TIMEOUT = 60
DEFAULT_UPLOAD_TRANSFER_TIMEOUT = 1800
MAX_TEMP_UPLOAD_BYTES = 1024**3
_OFFICIAL_DASHSCOPE_HOSTS = frozenset({"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"})
_REQUIRED_POLICY_FIELDS = (
    "upload_dir",
    "upload_host",
    "oss_access_key_id",
    "signature",
    "policy",
    "x_oss_object_acl",
    "x_oss_forbid_overwrite",
)


class TemporaryUploadError(RuntimeError):
    """DashScope could not create or consume a temporary OSS upload policy."""


def upload_policy_url(base_url: str) -> str | None:
    """Resolve the policy endpoint for an official DashScope endpoint or an explicit override."""
    configured = get_env("DASHSCOPE_UPLOAD_POLICY_URL")
    if configured:
        return configured
    parsed = urlsplit(base_url)
    if parsed.hostname not in _OFFICIAL_DASHSCOPE_HOSTS:
        return None
    return f"{parsed.scheme or 'https'}://{parsed.netloc}/api/v1/uploads"


def is_available(base_url: str, api_key: str) -> bool:
    """Whether this request has enough information to use DashScope temporary storage."""
    return bool(api_key and api_key != "EMPTY" and upload_policy_url(base_url))


def _response_error(response: Any) -> str:
    text = str(getattr(response, "text", "") or "").strip()
    return text[:1000] or "empty response"


def upload_temporary_file(
    path: str | Path,
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: int = DEFAULT_UPLOAD_TIMEOUT,
    transfer_timeout: int = DEFAULT_UPLOAD_TRANSFER_TIMEOUT,
    session: Any | None = None,
) -> str:
    """Upload ``path`` through DashScope's model-bound temporary OSS policy."""
    source = Path(path)
    size = source.stat().st_size
    if size > MAX_TEMP_UPLOAD_BYTES:
        raise TemporaryUploadError(
            f"{source.name} is {size / 1024**3:.2f} GiB, over DashScope's 1 GiB temporary upload limit"
        )
    policy_url = upload_policy_url(base_url)
    if not policy_url:
        raise TemporaryUploadError(
            "temporary OSS upload is only available for an official DashScope endpoint, or when "
            "DASHSCOPE_UPLOAD_POLICY_URL is configured"
        )
    if not api_key or api_key == "EMPTY":
        raise TemporaryUploadError("temporary OSS upload requires a DashScope API key")

    import requests

    own_session = session is None
    client = session or requests.Session()
    try:
        response = client.get(
            policy_url,
            headers={"Authorization": f"Bearer {api_key}"},
            params={"action": "getPolicy", "model": model},
            timeout=timeout,
        )
        if not response.ok:
            raise TemporaryUploadError(
                f"failed to obtain temporary OSS policy (HTTP {response.status_code}): {_response_error(response)}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise TemporaryUploadError("temporary OSS policy response is not valid JSON") from exc
        policy = body.get("data") if isinstance(body, dict) else None
        if not isinstance(policy, dict):
            raise TemporaryUploadError("temporary OSS policy response is missing the data object")
        missing = [field for field in _REQUIRED_POLICY_FIELDS if not policy.get(field)]
        if missing:
            raise TemporaryUploadError(f"temporary OSS policy is missing: {', '.join(missing)}")

        suffix = source.suffix.lower()
        object_name = f"{source.stem}-{uuid.uuid4().hex[:10]}{suffix}"
        object_key = f"{str(policy['upload_dir']).rstrip('/')}/{object_name}"
        content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        form = {
            "OSSAccessKeyId": policy["oss_access_key_id"],
            "Signature": policy["signature"],
            "policy": policy["policy"],
            "x-oss-object-acl": policy["x_oss_object_acl"],
            "x-oss-forbid-overwrite": policy["x_oss_forbid_overwrite"],
            "key": object_key,
            "success_action_status": "200",
        }
        with source.open("rb") as file_handle:
            # ``requests`` has no separate write timeout. During a multipart upload the socket can
            # retain the connect timeout until the request body has been sent, so a ``(60, 1800)``
            # connect/read tuple still aborts a slow large-file upload after about 60 seconds. Give
            # the complete POST the transfer timeout; the small policy request above keeps the
            # shorter timeout so endpoint/configuration failures still surface quickly.
            response = client.post(
                policy["upload_host"],
                data=form,
                files={"file": (object_name, file_handle, content_type)},
                timeout=max(timeout, transfer_timeout),
            )
        if response.status_code != 200:
            raise TemporaryUploadError(
                f"temporary OSS upload failed (HTTP {response.status_code}): {_response_error(response)}"
            )
        return f"oss://{object_key}"
    finally:
        if own_session:
            client.close()
