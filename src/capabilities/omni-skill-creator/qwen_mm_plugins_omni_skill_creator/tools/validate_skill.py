"""MCP tool: L1 structural validation for generated skills (22 checks, 6 categories).

Categories: frontmatter and naming, provenance, security, markdown links, asset naming,
asset manifest. Harness-agnostic — it checks the Agent Skill format, not one agent's runtime.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, Field

from ._media_utils import MAX_AUDIO_CLIP_SECONDS, MAX_CLIP_SECONDS


class ValidateSkillArgs(BaseModel):
    skill_dir: str = Field()


TOOL: dict[str, Any] = {"name": "validate_skill", "args": ValidateSkillArgs}

# --- Constants ----------------------------------------------------------------

_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MD_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")

FRAME_NAME_RE = re.compile(r"^\d{2}m\d{2}s(_[a-z0-9][a-z0-9-]*)?\.png$")
CLIP_NAME_RE = re.compile(r"^\d{2}m\d{2}s_\d{2}m\d{2}s\.mp4$")
AUDIO_NAME_RE = re.compile(r"^\d{2}m\d{2}s_\d{2}m\d{2}s\.mp3$")

# The MMmSSs prefix asserts "this came out of the video at that time", so it is reserved for
# video-derived assets and checked against the recorded timestamp (see #30).
TIME_PREFIX_RE = re.compile(r"^(\d{2})m(\d{2})s")
# `generated` covers code/render output (e.g. a gallery rendered from the skill's own helpers).
# It exists so an agent has an honest way to ship such an image instead of relabelling it a frame.
VIDEO_DERIVED_ASSET_TYPES = {"frame", "clip", "audio"}
VALID_ASSET_TYPES = VIDEO_DERIVED_ASSET_TYPES | {"generated"}
# The filename prefix has one-second resolution, so allow rounding slack when comparing.
ASSET_TIME_TOLERANCE_SEC = 1.5

# Agent Skill frontmatter keys. `allowed-tools` is deliberately absent: a generated skill must not
# grant itself tools, which #12 enforces as a security rule.
SPEC_ALLOWED_KEYS = {"name", "description", "license", "metadata", "compatibility"}
PROVENANCE_KEYS = {"source_type", "source_path", "source_sha256", "extraction_date", "status"}
ALLOWED_FRONTMATTER_KEYS = SPEC_ALLOWED_KEYS | PROVENANCE_KEYS

FORBIDDEN_FRONTMATTER_KEYS = {"allowedtools", "hooks", "model"}
FORBIDDEN_BODY_TOKENS = ("allowedTools", "allowed-tools")

VALID_SOURCE_TYPES = {"video", "demo", "document", "manual"}
VALID_STATUSES = {"source-grounded", "execution-verified"}

MANIFEST_REQUIRED_ASSET_FIELDS = {"id", "type", "path", "when_to_use", "text_description"}

MAX_SKILL_LINES = 500


# --- Helpers ------------------------------------------------------------------


def _resolve_skill_dir(raw: str) -> Path:
    if not raw or not raw.strip():
        raise ValueError("`skill_dir` must be a non-empty directory path.")
    d = Path(raw).expanduser()
    if not d.is_absolute():
        d = Path.cwd() / d
    d = d.resolve()
    if not d.is_dir():
        raise ValueError(f"Skill directory not found: {d}")
    return d


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _extract_local_links(text: str) -> list[str]:
    links: list[str] = []
    for target in _MD_LINK_RE.findall(text):
        target = target.split("#", 1)[0].strip()
        if not target:
            continue
        low = target.lower()
        if low.startswith(("http://", "https://", "mailto:", "data:")):
            continue
        links.append(target)
    return links


# --- Core validation ----------------------------------------------------------


def _validate(skill_dir: Path) -> dict:
    # Imported lazily: build_registry imports every tool module, so a top-level pyyaml import would
    # make it a hard requirement just to construct the registry.
    import yaml

    errors: list[str] = []
    warnings: list[str] = []
    stats: dict[str, int] = {}

    # ======================================================================
    # Category 1: Frontmatter structure (#1-6)
    # ======================================================================

    # #1 SKILL.md exists
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        errors.append("#1 Missing required file: SKILL.md")
        return {"status": "fail", "errors": errors, "warnings": warnings, "summary": stats}

    content = skill_md.read_text(errors="replace")
    lines = content.splitlines()
    stats["skill_md_lines"] = len(lines)

    # #2 frontmatter parse
    if not content.startswith("---"):
        errors.append("#2 SKILL.md has no YAML frontmatter (must start with ---).")
        fm = {}
    else:
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            errors.append("#2 SKILL.md frontmatter is unterminated (missing closing ---).")
            fm = {}
        else:
            try:
                fm = yaml.safe_load(match.group(1))
                if not isinstance(fm, dict):
                    errors.append("#2 Frontmatter must be a YAML dictionary.")
                    fm = {}
            except yaml.YAMLError as e:
                errors.append(f"#2 Invalid YAML in frontmatter: {e}")
                fm = {}

    # #3 name: required, kebab-case, ≤64
    name = fm.get("name", "")
    if not name:
        errors.append("#3 Frontmatter missing required field `name`.")
    elif not isinstance(name, str):
        errors.append(f"#3 `name` must be a string, got {type(name).__name__}.")
    else:
        name = name.strip()
        if not _KEBAB_RE.match(name):
            errors.append(f"#3 `name` '{name}' must be kebab-case (lowercase, digits, single hyphens).")
        if len(name) > 64:
            errors.append(f"#3 `name` is {len(name)} chars, max 64.")

    # #4 description: required, ≤1024, no <>
    desc = fm.get("description", "")
    if not desc:
        errors.append("#4 Frontmatter missing required field `description`.")
    elif not isinstance(desc, str):
        errors.append(f"#4 `description` must be a string, got {type(desc).__name__}.")
    else:
        desc = desc.strip()
        if len(desc) > 1024:
            errors.append(f"#4 `description` is {len(desc)} chars, max 1024.")
        if "<" in desc or ">" in desc:
            errors.append("#4 `description` must not contain angle brackets (< or >).")

    # #5 frontmatter key whitelist
    unexpected = set(fm.keys()) - ALLOWED_FRONTMATTER_KEYS
    if unexpected:
        errors.append(f"#5 Unexpected frontmatter key(s): {', '.join(sorted(unexpected))}.")

    # #6 compatibility: optional, ≤500
    compat = fm.get("compatibility", "")
    if compat:
        if not isinstance(compat, str):
            errors.append(f"#6 `compatibility` must be a string, got {type(compat).__name__}.")
        elif len(compat) > 500:
            errors.append(f"#6 `compatibility` is {len(compat)} chars, max 500.")

    # ======================================================================
    # Category 2: Provenance fields (#7-11)
    # ======================================================================

    # #7 source_type
    src_type = fm.get("source_type", "")
    if not src_type:
        errors.append("#7 Frontmatter missing required field `source_type`.")
    elif src_type not in VALID_SOURCE_TYPES:
        errors.append(f"#7 `source_type` '{src_type}' not in {VALID_SOURCE_TYPES}.")

    # #8 source_path
    src_path = fm.get("source_path", "")
    if not src_path:
        errors.append("#8 Frontmatter missing required field `source_path`.")

    # #9 status
    status = fm.get("status", "")
    if not status:
        errors.append("#9 Frontmatter missing required field `status`.")
    elif status not in VALID_STATUSES:
        errors.append(f"#9 `status` '{status}' not in {VALID_STATUSES}.")

    # #10 source_sha256 (optional)
    sha = fm.get("source_sha256")
    if sha is not None and isinstance(sha, str) and not _SHA256_RE.match(sha):
        warnings.append(f"#10 `source_sha256` '{sha[:16]}...' doesn't match hex-64 format.")

    # #11 extraction_date (optional)
    ext_date = fm.get("extraction_date")
    if ext_date is not None:
        date_str = str(ext_date).strip()
        if not _ISO_DATE_RE.match(date_str):
            warnings.append(f"#11 `extraction_date` '{date_str}' doesn't match YYYY-MM-DD.")

    # ======================================================================
    # Category 3: Security checks (#12-13)
    # ======================================================================

    # #12 forbidden frontmatter keys
    forbidden_found = FORBIDDEN_FRONTMATTER_KEYS.intersection(k.lower().replace("_", "").replace("-", "") for k in fm)
    if forbidden_found:
        errors.append(f"#12 Forbidden frontmatter key(s): {', '.join(sorted(forbidden_found))}.")

    # #13 forbidden body tokens
    body_start = content.find("---", 3)
    body = content[body_start + 3 :] if body_start > 0 else content
    for token in FORBIDDEN_BODY_TOKENS:
        if token in body:
            errors.append(f"#13 SKILL.md body contains forbidden token `{token}`.")

    # ======================================================================
    # Category 4: Markdown link resolution (#14-15)
    # ======================================================================

    md_files = sorted(skill_dir.rglob("*.md"))
    stats["markdown_files"] = len(md_files)

    for md_file in md_files:
        if not md_file.is_file():
            continue
        text = md_file.read_text(errors="replace")
        for target in _extract_local_links(text):
            resolved = (md_file.parent / target).resolve()
            # #14 phantom link
            if not resolved.exists():
                errors.append(f"#14 Phantom link in {_rel(md_file, skill_dir)}: `{target}` does not exist.")
            # #15 escape check
            elif skill_dir not in resolved.parents and resolved != skill_dir:
                warnings.append(f"#15 Link in {_rel(md_file, skill_dir)} escapes skill dir: `{target}`.")

    # ======================================================================
    # Category 5: Asset management (#16-20)
    # ======================================================================

    assets_dir = skill_dir / "assets"
    manifest_path = skill_dir / "asset_manifest.json"
    manifest_assets: list[dict] = []

    has_assets_dir = assets_dir.is_dir()
    has_manifest = manifest_path.is_file()

    # #29 empty asset subdirectory (advisory) — pre-created assets/{frames,clips,audio} left empty
    # just clutter the shipped skill; create on demand instead.
    if has_assets_dir:
        for sub in ("frames", "clips", "audio"):
            d = assets_dir / sub
            if d.is_dir() and not any(d.iterdir()):
                warnings.append(
                    f"#29 Empty asset dir `assets/{sub}/` — don't pre-create asset subdirs; "
                    f"create on demand, or remove the empty one before shipping."
                )

    # #31 a video-derived skill that ships no visual asset at all (advisory). The perception
    # record is the tell that a video was the source; 20 of 68 skills in testing shipped with no
    # `assets/` at all, which for a screen-recorded tutorial is usually an omission rather than a
    # choice. Advisory, because a genuinely text-expressible method is allowed to carry none.
    if not has_assets_dir:
        build_dir = skill_dir / ".build"
        from_video = any((build_dir / name).is_file() for name in ("video_events.md", "video_events.jsonl"))
        if from_video:
            warnings.append(
                "#31 Learned from a video (`.build/video_events.md` present) but ships no "
                "`assets/` — a screen-recorded method that needs no frame at all is rare; "
                "if text really carries everything, say so deliberately."
            )

    # #16 manifest existence
    if has_assets_dir and not has_manifest:
        errors.append("#16 `assets/` directory exists but `asset_manifest.json` is missing.")

    if has_manifest:
        try:
            manifest = json.loads(manifest_path.read_text(errors="replace"))
        except json.JSONDecodeError as e:
            errors.append(f"#16 `asset_manifest.json` is not valid JSON: {e}")
            manifest = {}
        else:
            if not isinstance(manifest, dict):
                errors.append("#16 `asset_manifest.json` must be a JSON object.")
                manifest = {}

        manifest_assets = manifest.get("assets", [])
        if not isinstance(manifest_assets, list):
            errors.append("#17 `asset_manifest.json` `assets` must be an array.")
            manifest_assets = []

        manifest_paths: set[str] = set()
        for i, asset in enumerate(manifest_assets):
            if not isinstance(asset, dict):
                errors.append(f"#17 asset[{i}] must be an object.")
                continue
            # #17 required fields
            missing = MANIFEST_REQUIRED_ASSET_FIELDS - set(asset.keys())
            if missing:
                errors.append(f"#17 asset[{i}] missing field(s): {', '.join(sorted(missing))}.")

            asset_path_str = asset.get("path", "")
            if asset_path_str:
                manifest_paths.add(asset_path_str)
                # #18 file existence
                resolved_asset = (skill_dir / asset_path_str).resolve()
                if not resolved_asset.is_file():
                    errors.append(f"#18 Asset path `{asset_path_str}` does not exist.")

                # #19 naming convention
                asset_name = Path(asset_path_str).name
                asset_type = asset.get("type", "")
                if asset_type == "frame" and not FRAME_NAME_RE.match(asset_name):
                    errors.append(f"#19 Bad frame name: `{asset_path_str}` (expected MMmSSs.png).")
                elif asset_type == "clip" and not CLIP_NAME_RE.match(asset_name):
                    errors.append(f"#19 Bad clip name: `{asset_path_str}` (expected MMmSSs_MMmSSs.mp4).")
                elif asset_type == "audio" and not AUDIO_NAME_RE.match(asset_name):
                    errors.append(f"#19 Bad audio name: `{asset_path_str}` (expected MMmSSs_MMmSSs.mp3).")

                if asset_type and asset_type not in VALID_ASSET_TYPES:
                    errors.append(
                        f"#17 asset[{i}] unknown type `{asset_type}` (allowed: {', '.join(sorted(VALID_ASSET_TYPES))})."
                    )

                # #30 provenance. The MMmSSs prefix is a claim about where the asset came from, so
                # it has to be backed by a recorded timestamp. Naming alone used to be the only
                # gate, which actively rewarded faking it: an agent rendered a gallery from its own
                # helper code, renamed it `00m07s_...`, declared `type: "frame"` with a null
                # timestamp, and scored 1.0 (testing). `generated` is the honest alternative.
                ts = asset.get("timestamp", asset.get("source_timestamp"))
                prefix_m = TIME_PREFIX_RE.match(asset_name)
                if asset_type in VIDEO_DERIVED_ASSET_TYPES:
                    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
                        errors.append(
                            f"#30 asset[{i}] `{asset_path_str}` is type `{asset_type}` but has no "
                            f"numeric `timestamp`; a video-derived asset must record the source "
                            f"time it was taken from. If code or a render produced it, declare "
                            f'`type: "generated"` instead of naming it like a frame.'
                        )
                    elif prefix_m:
                        named = int(prefix_m.group(1)) * 60 + int(prefix_m.group(2))
                        if abs(named - float(ts)) > ASSET_TIME_TOLERANCE_SEC:
                            errors.append(
                                f"#30 asset[{i}] `{asset_path_str}` name claims {named}s but "
                                f"`timestamp` is {float(ts):g}s — the filename prefix must match "
                                f"the source time."
                            )
                elif asset_type == "generated" and prefix_m:
                    errors.append(
                        f"#30 asset[{i}] `{asset_path_str}` is type `generated` but uses the "
                        f"MMmSSs prefix reserved for video-derived assets — rename it without a "
                        f"timestamp so it cannot be mistaken for a real frame."
                    )

                # #28 asset over-length (advisory) — bundled assets should stay minimal-sufficient.
                # Duration is encoded in the name (MMmSSs_MMmSSs); soft-warn past a comfortable span.
                span_m = re.match(r"^(\d{2})m(\d{2})s_(\d{2})m(\d{2})s\.(?:mp4|mp3)$", asset_name)
                if span_m:
                    span = (int(span_m.group(3)) * 60 + int(span_m.group(4))) - (
                        int(span_m.group(1)) * 60 + int(span_m.group(2))
                    )
                    soft = {"clip": MAX_CLIP_SECONDS, "audio": MAX_AUDIO_CLIP_SECONDS}.get(asset_type)
                    if soft is not None and span > soft:
                        warnings.append(
                            f"#28 {asset_type} `{asset_path_str}` spans {span}s (> {soft:g}s) — bundled "
                            f"assets should be minimal-sufficient; confirm the full span is needed, else trim."
                        )

        stats["assets_checked"] = len(manifest_assets)

        # #20 orphan asset detection
        if has_assets_dir:
            for real_file in sorted(assets_dir.rglob("*")):
                if not real_file.is_file():
                    continue
                rel_path = _rel(real_file, skill_dir)
                if rel_path not in manifest_paths:
                    warnings.append(f"#20 Orphan asset not in manifest: `{rel_path}`.")

    # ======================================================================
    # Category 6: Overall quality (#21-22)
    # ======================================================================

    # #21 line count
    if len(lines) > MAX_SKILL_LINES:
        warnings.append(f"#21 SKILL.md is {len(lines)} lines (recommended < {MAX_SKILL_LINES}).")

    # #22 evals.json
    evals_json = skill_dir / "evals" / "evals.json"
    if not evals_json.is_file():
        warnings.append("#22 `evals/evals.json` not found (needed for L3 eval).")
    stats["files_checked"] = len(md_files) + (1 if has_manifest else 0)

    # ======================================================================
    # Category 7: L2 Asset Pre-checks (#25-#27)
    # ======================================================================

    if manifest_assets:
        # #25 necessity_rationale completeness
        for i, asset in enumerate(manifest_assets):
            if not isinstance(asset, dict):
                continue
            rationale = asset.get("necessity_rationale")
            if not rationale or (isinstance(rationale, str) and not rationale.strip()):
                asset_id = asset.get("id", f"asset[{i}]")
                warnings.append(f"#25 Asset `{asset_id}` missing or empty `necessity_rationale`.")

        # #26 index-manifest consistency
        index_path = skill_dir / "asset_index.json"
        if index_path.is_file():
            try:
                index_data = json.loads(index_path.read_text(errors="replace"))
            except json.JSONDecodeError:
                warnings.append("#26 `asset_index.json` is not valid JSON.")
                index_data = {}

            if isinstance(index_data, dict):
                index_assets = index_data.get("assets", [])
                if isinstance(index_assets, list):
                    manifest_ids = {a.get("id") for a in manifest_assets if isinstance(a, dict) and a.get("id")}
                    index_ids = {a.get("id") for a in index_assets if isinstance(a, dict) and a.get("id")}
                    only_in_manifest = manifest_ids - index_ids
                    only_in_index = index_ids - manifest_ids
                    if only_in_manifest:
                        warnings.append(
                            f"#26 Assets in manifest but not in index: {', '.join(sorted(only_in_manifest))}."
                        )
                    if only_in_index:
                        warnings.append(f"#26 Assets in index but not in manifest: {', '.join(sorted(only_in_index))}.")

                    idx_to_manifest_field = {
                        "description": "text_description",
                        "when_to_use": "when_to_use",
                    }
                    manifest_map = {a["id"]: a for a in manifest_assets if isinstance(a, dict) and a.get("id")}
                    for idx_asset in index_assets:
                        if not isinstance(idx_asset, dict):
                            continue
                        aid = idx_asset.get("id")
                        if not aid or aid not in manifest_map:
                            continue
                        m_asset = manifest_map[aid]
                        for idx_field, m_field in idx_to_manifest_field.items():
                            idx_val = idx_asset.get(idx_field, "")
                            m_val = m_asset.get(m_field, "")
                            if idx_val and m_val and idx_val != m_val:
                                warnings.append(
                                    f"#26 Asset `{aid}` field `{idx_field}` differs between index and manifest."
                                )

        # #27 orphan asset detection (SKILL.md references)
        for asset in manifest_assets:
            if not isinstance(asset, dict):
                continue
            asset_id = asset.get("id", "")
            if not asset_id:
                continue
            # 作者引用资产的自然写法是相对路径或文件名(`assets/frames/00m58s_x.png`),不是
            # manifest id。只认 id 会把正确引用误报成孤儿 —— 实测(testing) 31 条 warning 里
            # 14 条是这个假阳性,而假警告会教 agent 无视 #27,连真孤儿也一起放过。
            path = asset.get("path") or ""
            refs = [r for r in (asset_id, path, PurePosixPath(path).name if path else "") if r]
            if not any(r in body for r in refs):
                warnings.append(f"#27 Asset `{asset_id}` is in manifest but not referenced in SKILL.md body.")

    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "warnings": warnings,
        "summary": stats,
    }


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    r"""Run L1 structural validation on a skill directory. Checks frontmatter, provenance, security, links,
    assets, and quality.

    Args:
        skill_dir: Path to the skill directory to validate.
    """
    skill_dir = _resolve_skill_dir(arguments["skill_dir"])
    result = _validate(skill_dir)
    return [{"type": "text", "text": json.dumps(result, indent=2)}]
