"""The device registry file: which MHS adapters exist and how to reach them.

One JSON file lists the adapters; the devices behind each one are whatever that adapter reports, so
adding hardware never edits this package. Location is ~/.qwen-mm-plugins/mhs-devices.json (beside the
shared config), overridable with QWEN_MM_MHS_DEVICES.

    {
      "adapters": [
        {"name": "lab-cam", "url": "http://192.168.1.20:8800", "timeout": 10,
         "auth": {"type": "bearer", "token_env": "LAB_CAM_TOKEN"}}
      ]
    }

QWEN_MM_MHS_* vars are capability-private and read through shared.env.get_env, so they resolve from
the environment or the shared config file without being part of that file's advertised catalog.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from shared.env import config_dir, get_env

DEVICES_FILE_NAME = "mhs-devices.json"

# Per-request ceiling for an adapter that answers slowly. Hardware can be slow (a camera exposing,
# an arm moving), so this is generous by HTTP standards; a device that needs longer should say so
# with its own "timeout" in the registry file.
DEFAULT_TIMEOUT = 10.0
MAX_TIMEOUT = 600.0

# Every path this host requests is <url><base_path>/devices... — the version prefix is part of the
# protocol, and an adapter behind a reverse proxy can relocate it per-adapter.
DEFAULT_BASE_PATH = "/mhs/v1"


class ConfigError(Exception):
    """The registry file is missing, unreadable, or does not describe usable adapters.

    Carries a message meant to be shown to the model verbatim: it says what to fix, not just what
    broke, because the model is the one that has to tell the user.
    """


@dataclass(frozen=True)
class Adapter:
    """One configured MHS adapter endpoint. Several devices may live behind it."""

    name: str
    url: str
    timeout: float = DEFAULT_TIMEOUT
    base_path: str = DEFAULT_BASE_PATH
    token_env: str | None = None

    def endpoint(self, path: str) -> str:
        """Absolute URL for a protocol path such as "/devices/cam-1/health"."""
        return f"{self.url}{self.base_path}{path}"

    def token(self) -> str | None:
        """Resolve the bearer token at call time, so rotating it needs no restart."""
        if not self.token_env:
            return None
        return get_env(self.token_env) or None


def devices_file() -> str:
    """Path to the registry file (QWEN_MM_MHS_DEVICES wins, else beside the shared config)."""
    override = get_env("QWEN_MM_MHS_DEVICES")
    if override:
        return os.path.expanduser(override)
    return os.path.join(config_dir(), DEVICES_FILE_NAME)


_EXAMPLE = json.dumps(
    {"adapters": [{"name": "lab-cam", "url": "http://192.168.1.20:8800"}]},
    indent=2,
)


def _missing_file_message(path: str) -> str:
    return (
        f"no MHS device registry at {path}.\n"
        "MHS adapters are run by the hardware they front; this host only needs to know where they "
        f"are. Create that file:\n{_EXAMPLE}\n"
        "Set QWEN_MM_MHS_DEVICES to use a different path. To try the tools without hardware, run "
        "the bundled mock adapter (skill/references/mock_adapter.py) and point an entry at it."
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def _parse_adapter(raw: object, index: int) -> Adapter:
    where = f"adapters[{index}]"
    _require(isinstance(raw, dict), f"{where} must be an object, got {type(raw).__name__}")
    name = raw.get("name")
    _require(isinstance(name, str) and name.strip() != "", f"{where}.name must be a non-empty string")
    name = name.strip()
    # Device ids are reported qualified as "<adapter>/<device_id>", so a name with a slash would
    # make that id ambiguous to parse back.
    _require("/" not in name, f"{where}.name {name!r} must not contain '/'")

    url = raw.get("url")
    _require(isinstance(url, str) and url.strip() != "", f"{where}.url must be a non-empty string")
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    _require(
        parts.scheme in ("http", "https"),
        f"{where}.url must be http:// or https://, got {raw.get('url')!r}",
    )
    _require(parts.netloc != "", f"{where}.url has no host: {raw.get('url')!r}")

    timeout = raw.get("timeout", DEFAULT_TIMEOUT)
    _require(
        isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and 0 < float(timeout) <= MAX_TIMEOUT,
        f"{where}.timeout must be a number in (0, {MAX_TIMEOUT}], got {timeout!r}",
    )

    base_path = raw.get("base_path", DEFAULT_BASE_PATH)
    _require(isinstance(base_path, str), f"{where}.base_path must be a string, got {base_path!r}")
    trimmed = base_path.strip().strip("/")
    base_path = f"/{trimmed}" if trimmed else ""

    token_env = _parse_auth(raw.get("auth"), where)
    return Adapter(name=name, url=url, timeout=float(timeout), base_path=base_path, token_env=token_env)


def _parse_auth(auth: object, where: str) -> str | None:
    """Only bearer-by-env-var is supported: a token in the registry file would be a secret at rest."""
    if auth is None:
        return None
    _require(isinstance(auth, dict), f"{where}.auth must be an object, got {type(auth).__name__}")
    kind = auth.get("type", "bearer")
    _require(kind == "bearer", f'{where}.auth.type must be "bearer", got {kind!r}')
    token_env = auth.get("token_env")
    _require(
        isinstance(token_env, str) and token_env.strip() != "",
        f"{where}.auth.token_env must name the environment variable holding the token "
        "(the token itself must not be stored in this file)",
    )
    return token_env.strip()


def load_adapters() -> list[Adapter]:
    """Parse and validate the registry file. Raises ConfigError with an actionable message."""
    path = devices_file()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        raise ConfigError(_missing_file_message(path)) from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from None

    _require(isinstance(raw, dict), f"{path} must hold a JSON object with an 'adapters' list")
    entries = raw.get("adapters")
    _require(isinstance(entries, list), f"{path} must hold an 'adapters' list")
    _require(len(entries) > 0, f"{path} lists no adapters. Add one:\n{_EXAMPLE}")

    adapters = [_parse_adapter(entry, i) for i, entry in enumerate(entries)]
    seen: set[str] = set()
    for adapter in adapters:
        _require(adapter.name not in seen, f"{path}: duplicate adapter name {adapter.name!r}")
        seen.add(adapter.name)
    return adapters
