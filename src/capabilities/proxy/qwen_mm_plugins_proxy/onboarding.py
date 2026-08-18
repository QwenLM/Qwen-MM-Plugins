"""首次启用时的模型看图能力引导：按 harness 分组发现与确认。

身份 = (harness + 模型名 + 原配置变量名)；base_url 仅作展示上下文，不作身份 key(它随路由工具/接线易变)。
capability_confirmed 置位后后续全自动。交互键盘可注入(测试用)；非交互不静默通过。
"""

from __future__ import annotations

import fnmatch
import os
import re
import sys
from dataclasses import dataclass, field

from . import wiring
from .capability import BUILTIN_CAPABILITIES
from .config import save_config

# 捕获 (变量名, 模型名)：匹配 model / model.xxx / name 等键
_MODEL_ENTRY = re.compile(r"""(?i)(model\.\w+|model|name|"model")["']?[ \t]*[:=][ \t]*["']([\w@.\-]+)["']""")


@dataclass
class ModelEntry:
    model: str
    variable: str | None = None
    source_url: str | None = None


@dataclass
class ModelGroup:
    group: str  # harness 名 / relay / global
    path: str  # 来源配置文件
    entries: list = field(default_factory=list)
    source_url: str | None = None


def _extract_entries(path: str, source_url: str | None) -> list[ModelEntry]:
    try:
        txt = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    seen = set()
    out = []
    for var, model in _MODEL_ENTRY.findall(txt):
        model = model.strip()
        key = (var, model)
        if model and key not in seen:
            seen.add(key)
            out.append(ModelEntry(model=model, variable=var, source_url=source_url))
    return out


def scan_model_groups(cfg) -> list[ModelGroup]:
    """按 harness 分组：每个 harness 配置文件 + relay_templates + 已有 model_capabilities 未覆盖项。"""
    groups: list[ModelGroup] = []
    seen_models: set[str] = set()

    # 1) 各 harness 配置文件
    for name, h in wiring.HARNESS_CFG.items():
        p = wiring._path(wiring.HOME, name)
        if not os.path.exists(p):
            continue
        source_url = wiring.read_base_url(p, h)  # 仅展示上下文
        entries = _extract_entries(p, source_url)
        for ent in entries:
            seen_models.add(ent.model)
        groups.append(ModelGroup(group=name, path=p, entries=entries, source_url=source_url))

    # 2) relay_templates
    relay_entries = []
    for rname, spec in cfg.routing.relay_templates.items():
        for m in spec.get("models") or []:
            relay_entries.append(
                ModelEntry(model=m, variable=f"relay_templates.{rname}.models", source_url=spec.get("base_url"))
            )
            seen_models.add(m)
    if relay_entries:
        groups.append(
            ModelGroup(group="relay", path="proxy.json relay_templates", entries=relay_entries, source_url=None)
        )

    # 3) 已有 model_capabilities：支持旧扁平(map[str,str])与按组嵌套(map[group,map])两种
    legacy = []
    for m, v in cfg.model_capabilities.items():
        if isinstance(v, str) and m not in seen_models:
            legacy.append(ModelEntry(model=m, variable="model_capabilities"))
            seen_models.add(m)
        elif isinstance(v, dict):
            # 已是按组嵌套：若该组还没扫到，补成一个现有组
            if m not in {g.group for g in groups}:
                groups.append(
                    ModelGroup(
                        group=m,
                        path="proxy.json model_capabilities",
                        entries=[ModelEntry(mm, "model_capabilities") for mm in v],
                    )
                )
    if legacy:
        groups.append(ModelGroup(group="global", path="proxy.json model_capabilities", entries=legacy, source_url=None))
    return groups


def _default_cap(model: str) -> str:
    for pat, cap in BUILTIN_CAPABILITIES.items():
        if fnmatch.fnmatch(model, pat):
            return cap
    return "text_only"


def _read_key_from_tty():
    try:
        import msvcrt
    except ImportError:
        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    else:
        k = msvcrt.getwch()
        if k in ("\x00", "\xe0"):
            k2 = msvcrt.getwch()
            if k2 == "H":
                return "up"
            if k2 == "P":
                return "down"
            return "enter"
        if k in ("\r", "\n"):
            return "enter"
        if k == " ":
            return "space"
        if k.lower() == "q":
            return "q"
        if k in ("w",):
            return "up"
        if k in ("s",):
            return "down"
        return "enter"


def confirm_models(groups, key_source=None, out=None) -> dict | None:
    """按组分组的交互确认；返回 {group: {model: vision|text_only}}；取消返回 None。"""
    out = out or sys.stdout
    key_source = key_source or _read_key_from_tty
    items = []  # (group, ModelEntry)
    for g in groups:
        for ent in g.entries:
            items.append((g, ent))
    if not items:
        return {}
    vision = {(id(g), ent.model): (_default_cap(ent.model) == "vision") for g, ent in items}
    idx = 0
    n = len(items)
    last_group = None
    while True:
        out.write("\n" + "=" * 62 + "\n")
        out.write("首次启用路由：请确认各模型看图能力（默认纯文本，最安全）。\n")
        out.write("上/下(w/s) 选择　空格 切支持图片　回车 完成　q 取消\n")
        out.write("-" * 62 + "\n")
        for i, (g, ent) in enumerate(items):
            if g is not last_group:
                hdr = "[" + g.group + "]"
                if g.path:
                    hdr += "  " + g.path
                if g.source_url:
                    hdr += "  base_url=" + g.source_url
                out.write(hdr + "\n")
                last_group = g
            mark = "[x] 支持图片" if vision.get((id(g), ent.model)) else "[ ] 纯文本 "
            v = ("(" + ent.variable + ") ") if ent.variable else ""
            cur = " <" if i == idx else ""
            out.write("    %s  %s%s%s\n" % (mark, v, ent.model, cur))
        key = key_source()
        if key == "up":
            idx = (idx - 1) % n
        elif key == "down":
            idx = (idx + 1) % n
        elif key == "space":
            g, ent = items[idx]
            vision[(id(g), ent.model)] = not vision.get((id(g), ent.model))
        elif key == "q":
            return None
        elif key == "enter":
            break
        last_group = None  # 强制下轮重绘组头
    result: dict = {}
    for g, ent in items:
        result.setdefault(g.group, {})[ent.model] = "vision" if vision.get((id(g), ent.model)) else "text_only"
    return result


def _merge(cfg, result) -> None:
    for group, mm in result.items():
        bucket = cfg.model_capabilities.get(group)
        if not isinstance(bucket, dict):
            bucket = {}
            cfg.model_capabilities[group] = bucket
        for m, cap in mm.items():
            bucket[m] = cap


def _stored_models(cfg) -> set:
    out = set()
    for v in cfg.model_capabilities.values():
        if isinstance(v, dict):
            out.update(v.keys())
        elif isinstance(v, str):
            out.add(v)
    return out


def _confirm_groups(groups, key_source, out) -> dict | None:
    """对给定 groups 交互确认；返回 {group:{model:cap}} 或 None(取消)。"""
    total = sum(len(g.entries) for g in groups)
    if total == 0:
        return {}
    return confirm_models(groups, key_source=key_source, out=out)


def run_onboarding(cfg, key_source=None, out=None) -> bool:
    """每次 start 调用：首次全量确认；后续仅对「新出现的模型」增量确认(已存的自动跳过)。"""
    groups = scan_model_groups(cfg)
    out = out or sys.stdout
    if not cfg.routing.capability_confirmed:
        if sum(len(g.entries) for g in groups) == 0:
            out.write("首次启用路由：未发现候选模型，按回车确认空映射(q 取消)\n")
            if (key_source or _read_key_from_tty)() == "q":
                return False
        else:
            result = _confirm_groups(groups, key_source, out)
            if result is None:
                return False
            _merge(cfg, result)
        cfg.routing.capability_confirmed = True
        save_config(cfg)
        return True
    # 已确认过 -> 增量：只确认之前没存过的模型
    stored = _stored_models(cfg)
    new_groups = [
        ModelGroup(g.group, g.path, [e for e in g.entries if e.model not in stored], g.source_url) for g in groups
    ]
    new_groups = [g for g in new_groups if g.entries]
    if not new_groups:
        return True  # 无新模型，静默继续
    out.write("检测到新模型，请确认其看图能力(其余沿用已存配置)：\n")
    result = _confirm_groups(new_groups, key_source, out)
    if result is None:
        return False
    _merge(cfg, result)
    save_config(cfg)
    return True


def edit_all(cfg, key_source=None, out=None) -> bool:
    """显式入口：重新完整确认所有扫描到的模型(含已有)，供随时改配置。qwen-mm-plugins-proxy models。"""
    groups = scan_model_groups(cfg)
    out = out or sys.stdout
    result = _confirm_groups(groups, key_source, out)
    if result is None:
        return False
    _merge(cfg, result)
    cfg.routing.capability_confirmed = True
    save_config(cfg)
    return True


def models_scan_report(cfg, out=None) -> None:
    out = out or sys.stdout
    out.write("候选模型（按 harness 分组，base_url 仅为展示上下文、非身份 key）：\n")
    for g in scan_model_groups(cfg):
        out.write(
            "[" + g.group + "]  " + (g.path or "") + ("  base_url=" + g.source_url if g.source_url else "") + "\n"
        )
        for ent in g.entries:
            v = ("  (" + ent.variable + ")") if ent.variable else ""
            out.write("    %-28s <- %s%s\n" % (ent.model, _default_cap(ent.model), v))
