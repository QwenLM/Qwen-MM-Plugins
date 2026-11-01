"""Portable report artifacts and delivery validation."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from shared.env import get_env

from .common import InputError, local_file, run

ASSETS = Path(__file__).parent / "assets"
MANIFEST = "omnidr-project.json"
TEMPLATE_FILES = {
    "index.html": "index.html",
    "styles.css": "styles.css",
    "app.js": "js/app.js",
    "link.js": "js/link.js",
    "data.schema.js": "js/data.schema.js",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frame_metadata_paths(image: Path) -> list[Path]:
    """Preferred and legacy records; retain both source/time bindings when present."""
    image = image.resolve()
    legacy = image.with_suffix(image.suffix + ".json")
    for root in image.parents:
        if (root / MANIFEST).is_file():
            record = root / "_work/frame-provenance" / legacy.relative_to(root)
            if not record.resolve().is_relative_to(root):
                raise InputError("Frame provenance must stay inside its report directory.")
            return [record, legacy]
    return [legacy]


def frame_metadata_path(image: Path) -> Path:
    paths = frame_metadata_paths(image)
    return next((path for path in paths if path.exists() or path.is_symlink()), paths[0])


def probe(path: str | Path) -> dict:
    result = run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
    data = json.loads(result.stdout)
    duration = float(data.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise InputError("Media must have a positive finite duration.")
    return {"duration": duration, "streams": data.get("streams", [])}


def prepare(video_path: str, output_dir: str, title: str, language: str, theme: str) -> dict:
    source = local_file(video_path)
    destination = Path(output_dir).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise InputError("Report directory already exists. Inspect it to resume, or choose a new directory.")
    info = probe(source)
    if not any(s.get("codec_type") == "video" for s in info["streams"]):
        raise InputError("The report source must contain a video stream.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="omnidr-prepare-", dir=destination.parent) as work:
        stage = Path(work) / "report"
        for folder in ["js", "media", "img", "anchors", "_assets/videos", "_assets/images", "_assets/docs", "_work"]:
            (stage / folder).mkdir(parents=True, exist_ok=True)
        for asset, relative in TEMPLATE_FILES.items():
            shutil.copyfile(ASSETS / asset, stage / relative)
        # H.264/AAC already plays in browsers; retain its streams and just add faststart.
        compatible = all(
            s.get("codec_type") not in {"video", "audio"} or s.get("codec_name") in {"h264", "aac", "mp3"}
            for s in info["streams"]
        )
        if compatible:
            codec = ["-c", "copy"]
        else:
            encoders = run(["ffmpeg", "-hide_banner", "-encoders"]).stdout
            if "libx264" in encoders:
                codec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
            elif "libopenh264" in encoders:
                codec = ["-c:v", "libopenh264", "-b:v", "4M"]
            else:
                raise InputError(
                    "Browser playback requires H.264. Install an ffmpeg build with libx264 or libopenh264."
                )
            codec += ["-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-pix_fmt", "yuv420p", "-c:a", "aac"]
        run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                *codec,
                "-movflags",
                "+faststart",
                str(stage / "media/source.mp4"),
            ],
            timeout=1800,
        )
        data = {
            "meta": {"theme": theme, "category": "", "lang": language},
            "summary": "",
            "video": {"src": "media/source.mp4", "title": title, "duration": info["duration"], "poster": ""},
            "segments": [],
            "anchors": [],
            "research": [],
            "insights": [],
        }
        (stage / "js/data.js").write_text("window.DATA = " + json.dumps(data, ensure_ascii=False, indent=2) + ";\n")
        manifest = {
            "schema_version": 1,
            "source_sha256": sha256(source),
            "media_sha256": sha256(stage / "media/source.mp4"),
            "duration": info["duration"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        (stage / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
        from .policy import register_aliases

        register_aliases(manifest["source_sha256"], manifest["media_sha256"])
        (stage / "TOOLS_USED.md").write_text(
            "# Research evidence\n\nRecord sources opened, video ranges inspected, "
            "anchor-to-source links, verification results and remaining gaps.\n"
        )
        stage.rename(destination)
    return {
        "status": "draft",
        "project_dir": str(destination),
        "duration": info["duration"],
        "data_path": str(destination / "js/data.js"),
        "schema_path": str(destination / "js/data.schema.js"),
        "next_step": "Inspect the video, save evidence, and fill data.js. The empty draft is not a completed report.",
    }


def load_project(project_dir: str) -> tuple[Path, dict, dict]:
    root = Path(project_dir).expanduser().resolve()
    json_name, offset = MANIFEST, 0
    try:
        code = (root / json_name).read_text(encoding="utf-8")
        manifest = json.loads(code)
        json_name = "js/data.js"
        code = (root / json_name).read_text(encoding="utf-8")
        match = re.fullmatch(r"\s*window\.DATA\s*=\s*(.*?)\s*;?\s*", code, flags=re.S)
        if not match:
            raise ValueError()
        offset = match.start(1)
        data = json.loads(match[1])
        if not isinstance(data, dict) or manifest.get("schema_version") != 1:
            raise ValueError()
    except json.JSONDecodeError as error:
        at = offset + error.pos
        line, column = code.count("\n", 0, at) + 1, at - code.rfind("\n", 0, at)
        raise InputError(
            f"{json_name}: invalid JSON at line {line}, column {column}; serialize with json.dumps/JSON.stringify."
        ) from None
    except (OSError, ValueError, AttributeError):
        raise InputError("Not an OmniDR project, or data.js is not window.DATA = <JSON>;.") from None
    return root, manifest, data


def contained_file(root: Path, relative: str) -> Path | None:
    path = Path(relative)
    if path.is_absolute() or "\\" in relative:
        return None
    resolved = (root / path).resolve()
    return resolved if resolved.is_relative_to(root) and resolved.is_file() and resolved.stat().st_size else None


def credential_marker(content: str) -> bool:
    """Detect key markers, allowing public multiword URL slugs."""
    if (
        type(content) is str
        and "sk-" not in content
        and "-----BEGIN " not in content
        and "# Netscape HTTP Cookie File" not in content
    ):
        return False
    urls = r"https?://[^/\s<>\"'?#]+(?P<path>/[^\s<>\"'?#]*)?(?:[?#][^\s<>\"']*)?"
    markers = r"\bsk-[A-Za-z0-9_-]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|# Netscape HTTP Cookie File"
    for match in re.finditer(markers, content):
        words = match[0][3:].split("-")
        descriptive = (
            match[0].startswith("sk-")
            and words[0] not in {"proj", "svcacct"}
            and len(words) >= 4
            and all(re.fullmatch(r"[a-z0-9]{1,12}", word) for word in words)
            and sum(word.isalpha() and len(word) >= 2 for word in words) >= 3
        )
        if descriptive and any(
            url.start("path") <= match.start() < match.end() <= url.end("path") for url in re.finditer(urls, content)
        ):
            continue
        return True
    return False


def static_svg(content: str) -> bool:
    """Require a standalone SVG document without active or external content."""
    if re.search(
        r"<!DOCTYPE|<!ENTITY|<\s*(?:script|foreignObject)|\bon\w+\s*=|(?:href|src)\s*=|@import", content, re.I
    ):
        return False
    if any(
        not target.strip(" \t\r\n\"'").startswith("#")
        for target in re.findall(r"url\s*\((.*?)\)", content, re.I | re.S)
    ):
        return False
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return False
    return root.tag == "{http://www.w3.org/2000/svg}svg" and all(
        node.tag.startswith("{http://www.w3.org/2000/svg}") for node in root.iter()
    )


def privacy_errors(root: Path) -> list[str]:
    """Reject credentials, private endpoints and cache/model files."""
    errors = []
    secret = get_env("DASHSCOPE_API_KEY")
    secret_values = [secret] if secret and len(secret) >= 8 else []
    connection = get_env("DASHSCOPE_BASE_URL") or ""
    host = urlsplit(connection).hostname or ""
    private_host = host if host and host not in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"} else ""
    for path in root.rglob("*"):
        if path.is_symlink():
            errors.append("Report contains a symlink; deliver self-contained regular files.")
            continue
        if not path.is_file():
            continue
        name = path.name.lower()
        if any(
            part.lower() in {"cookies", "whisper-cache", ".git", ".ssh"} for part in path.relative_to(root).parts
        ) or (
            name.startswith(".env")
            or "cookie" in name
            or name.endswith((".safetensors", ".bin", ".pt", ".pth", ".gguf", ".pem", ".key"))
        ):
            errors.append("Report contains a credential/cache/model file; remove it before sharing.")
        if path.stat().st_size > 20 * 1024 * 1024:
            if path.suffix.lower() in {
                ".mp4",
                ".webm",
                ".mkv",
                ".mov",
                ".mp3",
                ".wav",
                ".m4a",
                ".pdf",
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
                ".gif",
                ".woff",
                ".woff2",
            }:
                continue
            errors.append("A text artifact exceeds the privacy scan limit; reduce or remove it before delivery.")
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if any(value in content for value in secret_values) or (private_host and private_host in content):
            errors.append("Report contains a configured secret or private model endpoint; redact it before sharing.")
        if credential_marker(content):
            errors.append("Report contains credential-like content; remove it before sharing.")
    return sorted(set(errors))


def validate(project_dir: str, stage: str = "delivery") -> dict:
    root, manifest, data = load_project(project_dir)
    if stage not in {"structure", "delivery"}:
        raise InputError("Validation stage must be structure or delivery.")
    errors = privacy_errors(root)
    for asset, relative in TEMPLATE_FILES.items():
        actual = contained_file(root, relative)
        if actual is None or sha256(actual) != sha256(ASSETS / asset):
            errors.append(f"Restore the bundled renderer file: {relative}.")
    media = contained_file(root, "media/source.mp4")
    if media is None or sha256(media) != manifest.get("media_sha256"):
        errors.append("Source media is missing or changed since project initialization.")
    if not str(data.get("summary") or "").strip() or not data.get("segments") or not data.get("research"):
        errors.append("Fill the summary, video segments and externally researched sources before delivery.")
    duration = manifest.get("duration", 0)
    if data.get("video", {}).get("src") != "media/source.mp4" or data.get("video", {}).get("duration") != duration:
        errors.append("Keep the source video path and duration consistent with the project manifest.")
    covered_until = 0
    for segment in data.get("segments", []):
        start = segment.get("start") if isinstance(segment, dict) else None
        end = segment.get("end") if isinstance(segment, dict) else None
        if (
            not isinstance(start, (float, int))
            or not isinstance(end, (float, int))
            or not (0 <= start < end <= duration + 0.1)
        ):
            errors.append("A segment is outside the source video timeline.")
        else:
            if start > covered_until + 1:
                errors.append("Video segments leave a gap; inspect and document the missing interval.")
            covered_until = max(covered_until, end)
        if not isinstance(segment, dict) or not segment.get("caption"):
            errors.append("Each video segment needs a substantive caption.")
    if covered_until < duration - 1:
        errors.append("Video segments do not cover the end of the source.")
    source_ids = set()
    source_urls = set()
    for source in data.get("research", []):
        if not isinstance(source, dict):
            errors.append("Research entries must be objects.")
            continue
        parsed = urlsplit(str(source.get("url", "")))
        if source.get("id") in source_ids or source.get("url") in source_urls:
            errors.append("Research source IDs and canonical URLs must be unique.")
        source_ids.add(source.get("id"))
        source_urls.add(source.get("url"))
        if not str(source.get("summary") or "").strip():
            errors.append("Every research source needs a finding distilled from the opened source.")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            errors.append("Each source needs a canonical HTTP(S) URL without credentials.")
        if parsed.hostname in {"example.com", "example.org", "example.net"} or "replace-me" in parsed.path:
            errors.append("Replace placeholder sources with sources actually opened.")
        if any(re.search(r"key|token|auth|signature|cookie", key, re.I) for key, _ in parse_qsl(parsed.query)):
            errors.append("A citation URL contains a credential-like query parameter; use its canonical public URL.")
        evidence = source.get("media")
        if not isinstance(evidence, str) or contained_file(root, evidence) is None:
            errors.append("Every research source needs a saved local copy in its media field.")
        if source.get("type") == "video":
            perception = source.get("perception")
            observation_path = contained_file(root, perception) if isinstance(perception, str) else None
            if observation_path is None:
                errors.append("Every related video needs saved audiovisual observations in its perception field.")
            else:
                try:
                    observation = json.loads(observation_path.read_text())
                    video = contained_file(root, evidence) if isinstance(evidence, str) else None
                    if observation.get("status") == "read" and observation.get("source_sha256") == sha256(video):
                        continue
                    if (
                        observation.get("status") != "observed"
                        or observation.get("mode") not in {"frames", "video"}
                        or not observation.get("text")
                    ):
                        raise ValueError()
                    if video is None or observation.get("source_sha256") != sha256(video):
                        raise ValueError()
                except (OSError, ValueError, AttributeError):
                    errors.append(
                        "Related-video observations must contain a successful visual pass for the saved video's hash."
                    )

    # Validate evidence paths before the Node validator opens them.
    def check_paths(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if (
                    key in {"src", "keyframe", "poster", "background", "media", "thumb", "local", "frame", "perception"}
                    and isinstance(item, str)
                    and item
                ):
                    if urlsplit(item).scheme or item.startswith("//") or contained_file(root, item) is None:
                        errors.append(
                            "All report media and evidence paths must refer to existing files inside the report."
                        )
                if key == "svg" and isinstance(item, str) and not static_svg(item):
                    errors.append("SVG blocks must be static and self-contained.")
                check_paths(item)
        elif isinstance(value, list):
            for item in value:
                check_paths(item)

    check_paths(data)
    if errors:
        return {"status": "incomplete", "errors": sorted(set(errors))}
    if stage == "delivery":
        from .audit import delivery_errors

        errors.extend(delivery_errors(root, manifest, data))
        if errors:
            return {
                "status": "incomplete",
                "validation_stage": stage,
                "delivery_ready": False,
                "errors": sorted(set(errors)),
            }
    import subprocess

    from shared.syscmd import find_tool

    result = subprocess.run(
        [find_tool("node"), str(ASSETS / "validate_data.mjs"), str(root / "js/data.js")],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    return {
        "status": ("validated" if stage == "delivery" else "structure_valid")
        if result.returncode == 0
        else "incomplete",
        "validation_stage": stage,
        "delivery_ready": stage == "delivery" and result.returncode == 0,
        "structural_validation": result.returncode == 0,
        "details": (result.stdout + result.stderr)[-16000:],
        "segments": len(data["segments"]),
        "sources": len(data["research"]),
        "review_required": "Check the rendered page, video seeking, source independence, and whether quotes support claims. "
        "Mechanical validation does not establish factual correctness or research completeness.",
    }
