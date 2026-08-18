"""Shared pytest fixtures for Qwen-MM-Plugins tests.

Assets for the core media tools (read_image / read_video) are generated
synthetically so the suite stays hermetic — no binary fixtures committed for those.
(The renderer / visualize tests do use a few committed sample fixtures under
tests/assets/; those cases skip when an asset or optional dependency is missing.)
"""

import json
import os
import shutil
import subprocess
import sys
import threading

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

# Layout: src/ holds the shared library (mcp_framework, shared/) and src/capabilities/<name>/,
# each capability with its MCP server in a subdir whose name is a valid Python identifier
# (e.g. qwen_mm_plugins_core). Put src/ on the path (so `import mcp_framework` / `import shared` resolve)
# and add each capability dir so `import <pkg>` resolves from a checkout (an installed wheel maps
# the same names via pyproject package-dir).
_SRC = os.path.join(REPO_ROOT, "src")
sys.path.insert(0, _SRC)
_CAPABILITIES = os.path.join(_SRC, "capabilities")


def _discover_servers() -> dict:
    servers = {}
    for cap in sorted(os.listdir(_CAPABILITIES)):
        cap_dir = os.path.join(_CAPABILITIES, cap)
        if not os.path.isdir(cap_dir):
            continue
        for sub in sorted(os.listdir(cap_dir)):
            if os.path.isfile(os.path.join(cap_dir, sub, "__init__.py")):
                sys.path.insert(0, cap_dir)
                servers[sub] = os.path.join(cap_dir, sub)
    return servers


_SERVER_DIRS = _discover_servers()

# Directory to launch the vision server as a subprocess (`python3 src/capabilities/core/qwen_mm_plugins_core`),
# used by the protocol round-trip tests. `.get` (not `[]`) so a layout change skips those tests
# instead of raising at collection time and breaking every unrelated test file.
CORE_SERVER_DIR = _SERVER_DIRS.get("qwen_mm_plugins_core")

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def mcp_call(server_dir, action, env=None):
    """Launch the MCP server as a subprocess, initialize a client, run one async
    `action(session)`, and return its result. Shared by the protocol tests so the
    stdio-client boilerplate lives in one place."""
    import asyncio

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def _run():
        params = StdioServerParameters(command=sys.executable, args=[server_dir], env=env or dict(os.environ))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await action(session)

    return asyncio.run(_run())


class RecordingUpstream:
    """Minimal local HTTP upstream stub shared by proxy server/integration tests.

    Serves a canned completion and records every POST body it receives (so tests can
    assert the proxy replaced images with description text and leaked no base64).
    """

    def __init__(self):
        self.received: list[dict] = []
        self._server = None
        self.port = None
        self.content = "ok"

    def start(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        up = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                up.received.append(json.loads(self.rfile.read(length)))
                # ensure_ascii=False：真实上游返回原始 UTF-8 中文（非 \uXXXX 转义），
                # 才能复现 do_POST 用字符数当 content-length 导致的多字节截断 bug。
                payload = json.dumps({"choices": [{"message": {"content": up.content}}]}, ensure_ascii=False).encode(
                    "utf-8"
                )
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self._server.shutdown()


@pytest.fixture()
def upstream():
    """A running RecordingUpstream, torn down after the test."""
    u = RecordingUpstream().start()
    yield u
    u.stop()


@pytest.fixture(scope="session")
def repo_root() -> str:
    return REPO_ROOT


@pytest.fixture(scope="session")
def server_dir() -> str:
    """Path to run the vision MCP server as a subprocess (its __main__ self-registers)."""
    if not CORE_SERVER_DIR:
        pytest.skip("qwen_mm_plugins_core server package not found")
    return CORE_SERVER_DIR


@pytest.fixture(scope="session")
def sample_image(tmp_path_factory) -> str:
    """A 96×64 PNG: red left half, blue right half."""
    from PIL import Image

    path = tmp_path_factory.mktemp("media") / "sample.png"
    img = Image.new("RGB", (96, 64), (255, 0, 0))
    for x in range(48, 96):
        for y in range(64):
            img.putpixel((x, y), (0, 0, 255))
    img.save(path)
    return str(path)


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> str:
    """A 6s, 160×120, 10fps test-pattern MP4 (requires ffmpeg)."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not available")
    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=6:size=160x120:rate=10",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return str(path)


@pytest.fixture(scope="session")
def rotated_video(tmp_path_factory) -> str:
    """A 2s, 320×120 clip with a centered 100×100 white square, tagged rotation 90 — the shape a
    portrait phone video has on disk: sideways stored pixels plus a display-matrix flag."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not available")
    media = tmp_path_factory.mktemp("media")
    plain, path = media / "plain.mp4", media / "rotated.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:size=320x120:rate=10:duration=2,drawbox=x=110:y=10:w=100:h=100:color=white:t=fill",
            "-pix_fmt",
            "yuv420p",
            str(plain),
        ],
        check=True,
        capture_output=True,
    )
    # -display_rotation needs ffmpeg >= 6.0; skip rather than fail on an older build.
    tagged = subprocess.run(
        ["ffmpeg", "-y", "-display_rotation", "90", "-i", str(plain), "-c", "copy", str(path)],
        capture_output=True,
    )
    if tagged.returncode != 0:
        pytest.skip("ffmpeg cannot tag display rotation (needs -display_rotation, ffmpeg >= 6.0)")
    return str(path)


@pytest.fixture(scope="session")
def sample_media_av(tmp_path_factory) -> str:
    """A 3s MP4 with both tracks: video (testsrc 160×120 @ 10fps) + audio (440 Hz sine, AAC)."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not available")
    path = tmp_path_factory.mktemp("media") / "sample_av.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=160x120:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return str(path)
