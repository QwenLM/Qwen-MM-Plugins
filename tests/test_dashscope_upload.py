import pytest

from shared import dashscope_upload


class _Response:
    def __init__(self, status_code, *, body=None, text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._body = body
        self.text = text

    def json(self):
        return self._body


class _Session:
    def __init__(self, policy):
        self.policy = policy
        self.get_call = None
        self.post_call = None

    def get(self, url, **kwargs):
        self.get_call = (url, kwargs)
        return _Response(200, body={"data": self.policy})

    def post(self, url, **kwargs):
        self.post_call = (url, kwargs)
        return _Response(200)


def _policy():
    return {
        "upload_dir": "dashscope-instant/model/session",
        "upload_host": "https://temporary.example.com",
        "oss_access_key_id": "temporary-ak",
        "signature": "signature",
        "policy": "policy",
        "x_oss_object_acl": "private",
        "x_oss_forbid_overwrite": "true",
    }


def test_upload_temporary_file_uses_model_bound_policy(tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    session = _Session(_policy())

    url = dashscope_upload.upload_temporary_file(
        source,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="secret",
        model="qwen3.5-omni-plus",
        session=session,
    )

    policy_url, get_kwargs = session.get_call
    assert policy_url == "https://dashscope.aliyuncs.com/api/v1/uploads"
    assert get_kwargs["headers"] == {"Authorization": "Bearer secret"}
    assert get_kwargs["params"] == {"action": "getPolicy", "model": "qwen3.5-omni-plus"}
    assert get_kwargs["timeout"] == dashscope_upload.DEFAULT_UPLOAD_TIMEOUT
    upload_url, post_kwargs = session.post_call
    assert upload_url == "https://temporary.example.com"
    assert post_kwargs["data"]["key"].startswith("dashscope-instant/model/session/clip-")
    assert post_kwargs["timeout"] == dashscope_upload.DEFAULT_UPLOAD_TRANSFER_TIMEOUT
    assert url == f"oss://{post_kwargs['data']['key']}"


def test_upload_temporary_file_honors_longer_explicit_timeout(tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    session = _Session(_policy())

    dashscope_upload.upload_temporary_file(
        source,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="secret",
        model="qwen3.5-omni-plus",
        timeout=2400,
        session=session,
    )

    assert session.get_call[1]["timeout"] == 2400
    assert session.post_call[1]["timeout"] == 2400


def test_custom_endpoint_needs_explicit_policy_url(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_UPLOAD_POLICY_URL", raising=False)
    assert not dashscope_upload.is_available("https://gateway.example.com/v1", "key")
    monkeypatch.setenv("DASHSCOPE_UPLOAD_POLICY_URL", "https://gateway.example.com/uploads")
    assert dashscope_upload.is_available("https://gateway.example.com/v1", "key")


def test_temporary_upload_rejects_files_over_one_gib(monkeypatch, tmp_path):
    source = tmp_path / "huge.mp4"
    source.write_bytes(b"x")
    monkeypatch.setattr(dashscope_upload, "MAX_TEMP_UPLOAD_BYTES", 0)

    with pytest.raises(dashscope_upload.TemporaryUploadError, match="1 GiB"):
        dashscope_upload.upload_temporary_file(
            source,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="secret",
            model="qwen3.5-omni-plus",
            session=_Session(_policy()),
        )
