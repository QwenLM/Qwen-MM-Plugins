"""start/stop 自动接线与回滚：把三处 harness 的 base_url 指到本代理(第一跳，有备份)，并在 stop 还原。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from .config import RelayConfig, save_config

BAK_SUFFIX = ".qwen-mm-proxy.bak"
# 测试可 monkeypatch 此值以隔离（不触碰真实 ~）。
HOME = os.path.expanduser("~")


@dataclass(frozen=True)
class _Harness:
    kind: str  # json | toml | env
    rel_path: str
    key: str


HARNESS_CFG: dict[str, _Harness] = {
    # qwen-code 实际配置在 ~/.qwen/settings.json 的 model.baseUrl（不是旧路径 ~/.qwen-code/.env）
    "claude": _Harness("json", (".claude", "settings.json"), "env.ANTHROPIC_BASE_URL"),
    "codex": _Harness("toml", (".codex", "config.toml"), "base_url"),
    "qwen-code": _Harness("json", (".qwen", "settings.json"), "model.baseUrl"),
}


def _path(home: str, harness: str) -> str:
    rel = HARNESS_CFG[harness].rel_path
    return os.path.join(home, *rel) if isinstance(rel, tuple) else os.path.join(home, rel)


def _bak_path(p: str) -> str:
    return p + BAK_SUFFIX


def read_base_url(path: str, h: _Harness) -> str | None:
    try:
        if h.kind == "json":
            d = json.load(open(path, encoding="utf-8"))
            node = d
            for part in h.key.split("."):
                node = node.get(part) if isinstance(node, dict) else None
                if node is None:
                    break
            return node if isinstance(node, str) else None
        if h.kind == "env":
            for line in open(path, encoding="utf-8"):
                m = re.match(rf"^{h.key}=(.*)$", line.strip())
                if m:
                    return m.group(1).strip()
            return None
        # toml
        m = re.search(r'base_url\s*=\s*"([^"]*)"', open(path, encoding="utf-8").read())
        return m.group(1) if m else None
    except (OSError, json.JSONDecodeError):
        return None


def write_base_url(path: str, h: _Harness, url: str) -> bool:
    tmp = path + ".tmp"
    try:
        if h.kind == "json":
            d = json.load(open(path, encoding="utf-8"))
            parts = h.key.split(".")
            node = d
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = url
            data = json.dumps(d, ensure_ascii=False, indent=2) + "\n"
        elif h.kind == "env":
            lines = []
            hit = False
            for line in open(path, encoding="utf-8"):
                if re.match(rf"^{h.key}=", line.strip()):
                    lines.append(f"{h.key}={url}\n")
                    hit = True
                else:
                    lines.append(line)
            if not hit:
                lines.append(f"{h.key}={url}\n")
            data = "".join(lines)
        else:  # toml
            raw = open(path, encoding="utf-8").read()
            if re.search(r'base_url\s*=\s*"[^"]*"', raw):
                data = re.sub(r'base_url\s*=\s*"[^"]*"', f'base_url = "{url}"', raw)
            else:
                data = raw + f'\nbase_url = "{url}"\n'
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
        return True
    except (OSError, json.JSONDecodeError):
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        return False


def wiring_backup_and_rewrite(cfg) -> list[str]:
    """为启用的 harness 备份(不重复)并把 base_url 指到本代理；返回改动描述。"""
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    home = HOME
    changed: list[str] = []
    for name in cfg.routing.harnesses:
        h = HARNESS_CFG[name]
        p = _path(home, name)
        if not os.path.exists(p):
            changed.append(f"{name}: no config file, skipped")
            continue
        bak = _bak_path(p)
        if not os.path.exists(bak):
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                import shutil

                shutil.copyfile(p, bak)
            except OSError:
                pass
        ok = write_base_url(p, h, proxy_url)
        changed.append(f"{name}: base_url -> {proxy_url} ({'ok' if ok else 'FAIL'})")
    return changed


def wiring_restore(cfg) -> list[str]:
    """仅在当前 base_url 指向本代理时，从备份还原；返回改动描述。"""
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    home = HOME
    restored: list[str] = []
    for name in cfg.routing.harnesses:
        h = HARNESS_CFG[name]
        p = _path(home, name)
        bak = _bak_path(p)
        if not os.path.exists(bak):
            continue
        cur = read_base_url(p, h)
        if cur is not None and not cur.startswith(proxy_url):
            restored.append(f"{name}: 当前 base_url={cur!r} 非本代理，未还原(保留备份)")
            continue
        try:
            import shutil

            shutil.copyfile(bak, p)
            os.unlink(bak)
            restored.append(f"{name}: restored")
        except OSError as exc:
            restored.append(f"{name}: restore FAIL {exc}")
    return restored


def relays_activate(cfg) -> list[str]:
    """把 routing.relay_templates 合入 relays(name 去重，冲突不覆盖)，记录 activated_relays 并落盘。"""
    msgs: list[str] = []
    for name, spec in cfg.routing.relay_templates.items():
        existing = next((r for r in cfg.relays if r.name == name), None)
        if existing is not None:
            msgs.append(f"relay {name}: 已存在，跳过")
            continue
        try:
            cfg.relays.append(RelayConfig(name=name, **spec))
        except TypeError as exc:  # 模板字段非法
            msgs.append(f"relay {name}: 模板非法，跳过（{exc}）")
            continue
        if name not in cfg.routing.activated_relays:
            cfg.routing.activated_relays.append(name)
        msgs.append(f"relay {name} 已激活")
    save_config(cfg)
    return msgs


def relays_restore(cfg) -> list[str]:
    """移除 start 激活的 relay(仅移除与模板匹配者)，清空 activated_relays 并落盘。"""
    msgs: list[str] = []
    templates = cfg.routing.relay_templates
    remaining = []
    for r in cfg.relays:
        if (
            r.name in cfg.routing.activated_relays
            and r.name in templates
            and r.__dict__ == RelayConfig(name=r.name, **templates[r.name]).__dict__
        ):
            msgs.append(f"relay {r.name} 已还原移除")
        else:
            remaining.append(r)
    cfg.relays = remaining
    cfg.routing.activated_relays = []
    save_config(cfg)
    return msgs


def wiring_report(cfg) -> list[dict]:
    """三处 harness 当前 base_url 归属。"""
    proxy_url = f"http://127.0.0.1:{cfg.bind_port}"
    home = HOME
    out = []
    for name in HARNESS_CFG:
        p = _path(home, name)
        cur = read_base_url(p, HARNESS_CFG[name]) if os.path.exists(p) else None
        out.append(
            {
                "harness": name,
                "path": p,
                "base_url": cur,
                "wired": bool(cur and cur.startswith(proxy_url)),
                "has_backup": os.path.exists(_bak_path(p)),
            }
        )
    return out
