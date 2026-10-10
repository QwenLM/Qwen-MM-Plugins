"""Configuration regressions for the standalone video-memory builder, without SDK or network I/O."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_BUILD_DIR = Path(__file__).resolve().parents[1] / "src/capabilities/video-memory/skill/script/build_memory"
_PROBE = """
import json
from pathlib import Path
from types import SimpleNamespace

import build_graph as build
import env_config

calls = []
def encode(command, **kwargs):
    calls.append("encode")
    Path(command[-1]).write_bytes(b"x" * 60000)
    return SimpleNamespace(returncode=0)

class Bucket:
    def put_object(self, key, stream):
        calls.append("upload")
    def sign_url(self, method, key, ttl):
        calls.append(ttl)
        return "https://oss.example/clip.mp4"

build.subprocess.run = encode
build.oss2 = SimpleNamespace(Auth=lambda *a: None, Bucket=lambda *a: Bucket())
try:
    build.clip_and_upload_video("unused.mp4", 0, 10)
except env_config.ConfigurationError as error:
    print(json.dumps({"calls": calls, "error": str(error)}))
else:
    print(json.dumps({"calls": calls}))
"""


@pytest.mark.parametrize("source", ["environment", "config"])
@pytest.mark.parametrize(
    "value,expected",
    [
        (None, 7200),
        ("", 7200),
        ("   ", 7200),
        ("3600", 3600),
        (" 9000 ", 9000),
        ("invalid", None),
        ("1.5", None),
        ("15 MiB", None),
        ("0", None),
        ("-1", None),
        ("nan", None),
        ("inf", None),
    ],
)
def test_builder_validates_expiry_at_upload_before_any_work(tmp_path, source, value, expected):
    config = tmp_path / "config"
    config.write_text(f"OSS_URL_EXPIRY={value}\n" if source == "config" and value is not None else "")
    process_env = {k: v for k, v in os.environ.items() if k not in {"OSS_URL_EXPIRY", "PYTHONPATH"}}
    process_env["QWEN_MM_CONFIG"] = str(config)
    if source == "environment" and value is not None:
        process_env["OSS_URL_EXPIRY"] = value

    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=_BUILD_DIR,
        env=process_env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    outcome = json.loads(result.stdout.splitlines()[-1])
    if expected is None:
        assert outcome["calls"] == []
        assert "OSS_URL_EXPIRY" in outcome["error"]
        if value != "0":  # the documented default 7200 legitimately contains a zero
            assert value not in outcome["error"]
    else:
        assert outcome == {"calls": ["encode", "upload", expected]}
    assert result.stderr == ""


def test_builder_observes_expiry_edits_after_import(tmp_path):
    config = tmp_path / "config"
    config.write_text("OSS_URL_EXPIRY=3600\n")
    process_env = {k: v for k, v in os.environ.items() if k not in {"OSS_URL_EXPIRY", "PYTHONPATH"}}
    process_env["QWEN_MM_CONFIG"] = str(config)
    second_call = """
Path(env_config.config_file()).write_text("OSS_URL_EXPIRY=9000\\n")
build.clip_and_upload_video("unused.mp4", 0, 10)
print(json.dumps(calls))
"""
    result = subprocess.run(
        [sys.executable, "-c", _PROBE + second_call],
        cwd=_BUILD_DIR,
        env=process_env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert json.loads(result.stdout.splitlines()[-1]) == ["encode", "upload", 3600, "encode", "upload", 9000]


def _run_probe(tmp_path, code, contents=""):
    config = tmp_path / "config"
    config.write_text(contents)
    process_env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"PYTHONPATH"} and not k.startswith(("DASHSCOPE_", "OSS_", "QWEN_MM_"))
    }
    process_env["QWEN_MM_CONFIG"] = str(config)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_BUILD_DIR,
        env=process_env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(result.stdout.splitlines()[-1])


@pytest.mark.parametrize("install_succeeds", [True, False])
def test_launcher_installs_pydantic_before_reading_file_only_credentials(tmp_path, install_succeeds):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    installed = tmp_path / "pydantic-installed"
    log = tmp_path / "calls"
    python = fake_bin / "python"
    python.write_text(f"""#!{sys.executable}
import os, sys
from pathlib import Path
args = sys.argv[1:]
marker = Path({str(installed)!r})
if args[:2] == ["-m", "pip"]:
    assert "pydantic>=2.11,<3" in args
    Path({str(log)!r}).write_text("install")
    if {install_succeeds!r}:
        marker.touch()
    sys.exit(0 if {install_succeeds!r} else 1)
if args[0] == "-c":
    if "pydantic.__version__" in args[1]:
        sys.exit(0 if marker.exists() else 1)
    if args[1] in ("import cv2", "import numpy", "import dashscope"):
        sys.exit(0)
os.execv({sys.executable!r}, [{sys.executable!r}, *args])
""")
    python.chmod(0o755)
    config = tmp_path / "config"
    config.write_text("DASHSCOPE_API_KEY=private-file-only-key\n")
    process_env = {**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"], "QWEN_MM_CONFIG": str(config)}
    process_env.pop("DASHSCOPE_API_KEY", None)
    result = subprocess.run(
        ["bash", str(_BUILD_DIR / "build_memory.sh"), str(tmp_path / "missing.mp4"), "--no-asr"],
        env=process_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert log.read_text() == "install"
    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "private-file-only-key" not in output
    assert ("Video file not found" if install_succeeds else "configuration requires pydantic") in output


def test_builder_chat_settings_refresh_and_invalid_timeout_never_retries(tmp_path):
    outcome = _run_probe(
        tmp_path,
        """
import json
from pathlib import Path
from types import SimpleNamespace
import llm_client as client
import env_config

calls = []
def post(url, **kwargs):
    calls.append([url, kwargs["headers"]["Authorization"], json.loads(kwargs["data"])["model"], kwargs["timeout"]])
    return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {"content": "ok"}}]})
client.requests.post = post
client.time.sleep = lambda _: (_ for _ in ()).throw(AssertionError("must not retry configuration errors"))
client.call_vlm_curl("test")
path = Path(env_config.config_file())
path.write_text("DASHSCOPE_BASE_URL=https://next.example/v1\\nDASHSCOPE_API_KEY=next-key\\nQWEN_MM_API_VL_MODEL=next-model\\nQWEN_MM_CHAT_TIMEOUT=bad\\n")
try:
    client.call_vlm_curl("test")
except env_config.ConfigurationError:
    pass
else:
    raise AssertionError("invalid timeout was accepted")
path.write_text(path.read_text().replace("=bad", "=90"))
client.call_vlm_curl("test")
client.call_vlm_curl("test", model="explicit-model", api_key="explicit-key")
print(json.dumps(calls))
""",
    )
    assert outcome == [
        ["https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "Bearer ", "qwen3.7-plus", 1800],
        ["https://next.example/v1/chat/completions", "Bearer next-key", "next-model", 90],
        ["https://next.example/v1/chat/completions", "Bearer explicit-key", "explicit-model", 90],
    ]


def test_builder_oss_credentials_and_destination_refresh_after_import(tmp_path):
    outcome = _run_probe(
        tmp_path,
        _PROBE
        + """
settings = []
def bucket(auth, endpoint, name):
    settings.append([auth, endpoint, name])
    return SimpleNamespace(put_object=lambda key, stream: settings.append(key), sign_url=lambda *args: "https://oss.example/clip.mp4")
build.oss2 = SimpleNamespace(Auth=lambda *args: list(args), Bucket=bucket)
for suffix in ("first", "second"):
    Path(env_config.config_file()).write_text(f"OSS_AK=ak-{suffix}\\nOSS_SK=sk-{suffix}\\nOSS_ENDPOINT=https://{suffix}.example\\nOSS_BUCKET=bucket-{suffix}\\nOSS_VIDEO_CLIP_PREFIX=prefix-{suffix}\\n")
    build.clip_and_upload_video("unused.mp4", 0, 10)
print(json.dumps(settings))
""",
    )
    assert outcome[0] == [["ak-first", "sk-first"], "https://first.example", "bucket-first"]
    assert outcome[1].startswith("prefix-first/")
    assert outcome[2] == [["ak-second", "sk-second"], "https://second.example", "bucket-second"]
    assert outcome[3].startswith("prefix-second/")


def test_embedding_endpoint_refreshes_without_import_time_config_reads(tmp_path):
    outcome = _run_probe(
        tmp_path,
        """
import json
from pathlib import Path
from types import SimpleNamespace
import requests
import embeddings
import env_config

calls = []
def post(url, **kwargs):
    calls.append([url, kwargs["headers"]["Authorization"]])
    return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"output": {"embeddings": [{"embedding": [1.0, 0.0]}]}})
requests.post = post
path = Path(env_config.config_file())
for suffix in ("first", "second"):
    path.write_text(f"DASHSCOPE_BASE_URL=https://{suffix}.example/v1\\nDASHSCOPE_API_KEY={suffix}-key\\n")
    embeddings._embed_via_dashscope_native(["test"])
print(json.dumps(calls))
""",
        contents="malformed config line\n",
    )
    assert outcome == [
        [
            "https://first.example/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding",
            "Bearer first-key",
        ],
        [
            "https://second.example/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding",
            "Bearer second-key",
        ],
    ]


def test_builder_does_not_turn_config_errors_into_empty_subgraphs(tmp_path):
    outcome = _run_probe(
        tmp_path,
        """
import json
import build_graph as build
import env_config

calls = []
build.oss2 = None
def frames(*args, **kwargs):
    calls.append("frames")
    return [("data:image/jpeg;base64,eA==", "00:00:00")]
build.extract_frames_base64 = frames
build.call_vlm_curl.__globals__["requests"].post = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no HTTP request expected"))
macro = build.MacroEvent(macro_id="macro_000", label="test", time_range=[0, 10], summary="test")
try:
    build.step2_subgraph_extraction("unused.mp4", [macro], concurrency=1)
except env_config.ConfigurationError as error:
    print(json.dumps([calls, str(error)]))
else:
    raise AssertionError("invalid configuration was swallowed")
""",
        contents="QWEN_MM_CHAT_TIMEOUT=invalid\n",
    )
    assert outcome[0] == ["frames"]
    assert "QWEN_MM_CHAT_TIMEOUT" in outcome[1]
