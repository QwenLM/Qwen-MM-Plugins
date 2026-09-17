#!/usr/bin/env python3
import argparse
import base64
import concurrent.futures
import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

from shared.env import get_env

from .image_providers import canonical_image_provider_name, create_image_provider
from .model_config import load_model_config, pipeline_model_overrides
from .tools.omni_call import run_call as run_omni_call
from .video_providers import canonical_video_provider_name, create_video_provider

CAPABILITY_DIR = Path(__file__).resolve().parents[2]
SKILL_DIR = CAPABILITY_DIR / "skill" / "music-to-mv"
VIDEO_WORKFLOW_DIR = SKILL_DIR / "workflows" / "video-generation"
DEFAULT_CONFIG_PATH = VIDEO_WORKFLOW_DIR / "assets" / "api-config.example.json"
DEFAULT_SHOT_QC_PROMPT_PATH = VIDEO_WORKFLOW_DIR / "references" / "shot-quality-control.md"

DEFAULT_CONFIG = {
    "image_providers": {
        "qwen_image_3": {
            "base_url": "https://dashscope.aliyuncs.com",
            "api_key_env": "DASHSCOPE_API_KEY",
            "model": "qwen-image-3.0-pro",
            "size": "2048*1152",
            "n": 1,
            "prompt_extend": False,
            "prompt_extend_mode": "direct",
            "enable_thinking": False,
            "watermark": False,
            "workers": 2,
            "max_attempts": 3,
        },
        "seedream": {
            "base_url": "https://ark.cn-beijing.volces.com",
            "api_key_env": "ARK_API_KEY",
            "model": "doubao-seedream-4-5-251128",
            "size": "2K",
            "character_size": "2K",
            "watermark": False,
            "workers": 2,
            "max_attempts": 3,
        },
    },
    "providers": {
        "wan3": {
            "base_url": "https://dashscope.aliyuncs.com",
            "api_key_env": "DASHSCOPE_API_KEY",
            "model": "wan3.0-video",
            "prompt_extend": False,
            "audio": False,
            "watermark": False,
            "reference_audio_mode": "dashscope_oss",
            "dashscope_cli": "dashscope",
            "scheduler": {"submit_rpm": 5, "submit_rpm_steps": [5, 3, 2, 1], "query_rpm": 50, "min_repoll_sec": 15},
        },
        "seedance": {
            "base_url": "https://ark.cn-beijing.volces.com",
            "api_key_env": "ARK_API_KEY",
            "model": "doubao-seedance-2-5-260628",
            "generate_audio": False,
            "watermark": False,
            "official_identity_assets": {},
            "scheduler": {
                "submit_rpm": 60,
                "submit_rpm_steps": [60, 30, 15, 5],
                "query_rpm": 60,
                "min_repoll_sec": 15,
            },
        },
    },
    "image": {"provider": "qwen_image_3", "size": "2048*1152", "base_workers": 2, "max_attempts": 3},
    "video": {
        "provider": "wan3",
        "resolution": "720P",
        "ratio": "adaptive",
        "submit_rpm": 5,
        "submit_rpm_steps": [5, 3, 2, 1],
        "adaptive_submit_rpm": True,
        "rate_error_window": 10,
        "rate_error_ratio": 0.4,
        "rate_error_burst": 3,
        "query_rpm": 50,
        "max_inflight": 30,
        "submit_workers": 12,
        "download_workers": 12,
        "min_repoll_sec": 15,
        "rate_safety_factor": 1.0,
        "max_submit_attempts": 1,
        "max_submit_requeues": 120,
        "max_submit_error_requeues": 20,
        "max_generation_requeues": 3,
    },
    "quality_control": {
        "enabled": False,
        "strategy": "reject_major",
        "max_rounds": 3,
        "workers": 8,
        "launch_interval_sec": 1.5,
        "max_review_attempts": 5,
        "temperature": 0.01,
        "max_tokens": 8192,
        "fps": 2.0,
        "max_pixels": 200704,
        "prompt_path": None,
        "extra_requirements": "",
    },
    "subtitles": {
        "enabled": True,
        "source_srt": None,
        "source_evidence": None,
        "font": None,
        "preset": "slow",
        "crf": 18,
    },
    "assembly": {
        "width": 1280,
        "height": 544,
        "fps": 24,
        "crf": 18,
        "audio_bitrate": "256k",
        "boundary_blend_frames": 4,
        "output_name": "final_mv.mp4",
    },
}

QC_RATINGS = ("fully_compliant", "minor_issues", "major_issues")
QC_RATING_RANK = {rating: index for index, rating in enumerate(QC_RATINGS)}


def normalize_qc_result(raw_result, segment):
    """Normalize an Omni review and conservatively fill every expected editorial shot."""
    raw_result = raw_result if isinstance(raw_result, dict) else {}
    supplied = raw_result.get("shot_reviews")
    supplied = supplied if isinstance(supplied, list) else []
    by_index = {}
    for review in supplied:
        if not isinstance(review, dict):
            continue
        try:
            by_index[int(review.get("global_index"))] = review
        except (TypeError, ValueError):
            continue

    window_by_index = {
        int(window["sub_global_index"]): window.get("t", [])
        for window in segment.get("shot_windows", [])
        if window.get("sub_global_index") is not None
    }
    normalized = []
    for subshot in segment.get("sub_shots", []):
        global_index = int(subshot["global_index"])
        review = by_index.get(global_index, {})
        rating = review.get("rating")
        missing = rating not in QC_RATING_RANK
        if missing:
            rating = "major_issues"
        issues = (
            [item for item in review.get("issues", []) if isinstance(item, dict)]
            if isinstance(review.get("issues"), list)
            else []
        )
        issue_severities = {item.get("severity") for item in issues}
        if "major" in issue_severities:
            rating = "major_issues"
        elif "minor" in issue_severities and rating == "fully_compliant":
            rating = "minor_issues"
        if missing:
            issues = [
                {
                    "criterion": "review_completeness",
                    "severity": "major",
                    "expected": f"A review for shot {global_index}",
                    "observed": "The Omni response omitted the shot or used an invalid rating",
                    "timestamp_sec": None,
                },
                *issues,
            ]
        normalized.append(
            {
                "global_index": global_index,
                "local_time": window_by_index.get(global_index, review.get("local_time")),
                "rating": rating,
                "summary": str(review.get("summary", "")).strip(),
                "visible_evidence": review.get("visible_evidence", []),
                "issues": issues,
            }
        )

    counts = {rating: sum(1 for item in normalized if item["rating"] == rating) for rating in QC_RATINGS}
    issue_counts = {
        severity: sum(1 for item in normalized for issue in item["issues"] if issue.get("severity") == severity)
        for severity in ("major", "minor")
    }
    worst_rating = max((item["rating"] for item in normalized), key=QC_RATING_RANK.get, default="major_issues")
    return {
        "schema": "music2mv/shot-quality-control",
        "segment_index": int(segment["index"]),
        "segment_rating": worst_rating,
        "rating_counts": counts,
        "issue_counts": issue_counts,
        "shot_reviews": normalized,
        "summary": str(raw_result.get("summary", "")).strip(),
    }


def qc_candidate_sort_key(candidate):
    """Lower is better, using only categorical ratings and explicit issue counts."""
    counts = candidate["review"]["rating_counts"]
    issues = candidate["review"].get("issue_counts", {})
    return (
        counts["major_issues"],
        int(issues.get("major", 0)),
        counts["minor_issues"],
        int(issues.get("minor", 0)),
        int(candidate["round"]),
    )


def qc_candidate_accepted(candidate, strategy):
    if strategy == "accept_all":
        return True
    if strategy == "reject_major":
        return candidate["review"]["rating_counts"]["major_issues"] == 0
    raise ValueError(f"unsupported quality_control.strategy: {strategy}")


def one_shot_request_contract_errors(board):
    """Return violations of the one-editorial-shot-per-provider-request invariant."""

    def same_number(left, right):
        try:
            return abs(float(left) - float(right)) <= 0.02
        except (TypeError, ValueError):
            return False

    errors = []
    for position, segment in enumerate(board.get("segments", [])):
        path = f"segments[{position}]"
        subshots = segment.get("sub_shots")
        windows = segment.get("shot_windows")
        if not isinstance(subshots, list) or len(subshots) != 1:
            errors.append(f"{path}.sub_shots must contain exactly one editorial shot")
            continue
        if segment.get("assembly_mode") != "single_take_i2v":
            errors.append(f"{path}.assembly_mode must be single_take_i2v")
        if not isinstance(windows, list) or len(windows) != 1:
            errors.append(f"{path}.shot_windows must contain exactly one full-duration window")
            continue
        shot = subshots[0]
        window = windows[0]
        duration = segment.get("duration_sec")
        times = window.get("t")
        if (
            not isinstance(times, list)
            or len(times) != 2
            or not same_number(times[0], 0.0)
            or not same_number(times[1], duration)
        ):
            errors.append(f"{path}.shot_windows[0].t must equal [0, segment.duration_sec]")
        if window.get("sub_global_index") != shot.get("global_index"):
            errors.append(f"{path}.shot_windows[0] must reference its only editorial shot")
        for field in ("start_sec", "end_sec", "duration_sec"):
            if not same_number(segment.get(field), shot.get(field)):
                errors.append(f"{path}.{field} must equal {path}.sub_shots[0].{field}")
        shot_scene = shot.get("scene_id")
        if list(segment.get("scene_ids", [])) != ([shot_scene] if shot_scene else []):
            errors.append(f"{path}.scene_ids must contain only the shot scene")
        if list(segment.get("cast_present", [])) != list(shot.get("cast_present", [])):
            errors.append(f"{path}.cast_present must equal the shot cast")
    return errors


class AdaptiveSubmitRate:
    def __init__(self, steps, window_size=10, error_ratio=0.4, error_burst=3, initial_rpm=None):
        normalized = sorted({float(value) for value in steps if float(value) > 0}, reverse=True)
        if not normalized:
            raise ValueError("submit_rpm_steps must contain a positive RPM")
        self.steps = normalized
        self.index = 0
        if initial_rpm is not None:
            self.index = min(range(len(self.steps)), key=lambda item: abs(self.steps[item] - float(initial_rpm)))
        self.window = deque(maxlen=max(1, int(window_size)))
        self.error_ratio = float(error_ratio)
        self.error_burst = max(1, int(error_burst))
        self.error_streak = 0

    @property
    def rpm(self):
        return self.steps[self.index]

    def observe(self, rate_limited):
        self.window.append(bool(rate_limited))
        self.error_streak = self.error_streak + 1 if rate_limited else 0
        widespread = len(self.window) == self.window.maxlen and sum(self.window) / len(self.window) >= self.error_ratio
        if self.index < len(self.steps) - 1 and (self.error_streak >= self.error_burst or widespread):
            previous = self.rpm
            self.index += 1
            self.window.clear()
            self.error_streak = 0
            return {"from_rpm": previous, "to_rpm": self.rpm, "reason": "widespread_rate_limit"}
        return None


def deep_merge(base, override):
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def error_message(response):
    return ((response.get("error") or {}).get("message")) or response.get("message") or ""


def response_status(response):
    return (response.get("_http") or {}).get("status") or response.get("status_code") or 0


def is_rate_limit_response(response):
    status = response_status(response)
    message = error_message(response).upper()
    return status == 429 or "RPM" in message or "RATE LIMIT" in message


def retry_delay(response, attempt, minimum=1.0):
    retry_after = (response.get("_http") or {}).get("retry_after")
    if retry_after:
        try:
            return max(minimum, float(retry_after))
        except ValueError:
            pass
    return max(minimum, min(65.0, 2.0 ** min(attempt, 6)))


class Pipeline:
    def __init__(
        self,
        storyboard_path,
        audio_path,
        output_dir,
        config_path=None,
        lyrics_srt=None,
        model_config_path=None,
    ):
        self.storyboard_path = Path(storyboard_path).resolve()
        self.audio_path = Path(audio_path).resolve()
        self.output_dir = Path(output_dir).resolve()
        if not self.storyboard_path.exists():
            raise FileNotFoundError(self.storyboard_path)
        if not self.audio_path.exists():
            raise FileNotFoundError(self.audio_path)
        model_config, resolved_model_config_path = load_model_config(model_config_path)
        config = load_json(config_path) if config_path else {}
        quality_control_override = config.get("quality_control") if isinstance(config, dict) else None
        self.qc_enabled_source = (
            "run_config"
            if isinstance(quality_control_override, dict) and "enabled" in quality_control_override
            else "built_in_default"
        )
        self.model_config_path = resolved_model_config_path
        self.omni_config = copy.deepcopy(model_config.get("omni") or {})
        self.config = deep_merge(DEFAULT_CONFIG, pipeline_model_overrides(model_config))
        self.config = deep_merge(self.config, config)
        self.board = load_json(self.storyboard_path)
        self.compatibility_translations = []
        for segment in self.board.get("segments", []):
            subshots = segment.get("sub_shots", [])
            if not segment.get("scene_ids"):
                segment["scene_ids"] = list(
                    dict.fromkeys(subshot.get("scene_id") for subshot in subshots if subshot.get("scene_id"))
                )
                self.compatibility_translations.append(
                    f"segments[{segment.get('index')}].scene_ids derived from sub_shots"
                )
            if not segment.get("cast_present"):
                segment["cast_present"] = list(
                    dict.fromkeys(cast_id for subshot in subshots for cast_id in subshot.get("cast_present", []))
                )
                self.compatibility_translations.append(
                    f"segments[{segment.get('index')}].cast_present derived from sub_shots"
                )
            for subshot in subshots:
                sync = subshot.get("audio_sync", {})
                if "lyric" not in sync and "lyrics" in sync:
                    sync["lyric"] = sync["lyrics"]
                    self.compatibility_translations.append(
                        f"sub_shots[{subshot.get('global_index')}].audio_sync.lyrics aliased to lyric"
                    )
        request_contract_errors = one_shot_request_contract_errors(self.board)
        if request_contract_errors:
            raise ValueError(
                "Storyboard violates one editorial shot per provider request: " + "; ".join(request_contract_errors)
            )
        self.image_config = self.config["image"]
        self.video_config = self.config["video"]
        self.qc_config = deep_merge(self.omni_config, self.config["quality_control"])
        self.subtitle_config = self.config["subtitles"]
        self.assembly_config = self.config["assembly"]
        self.lyrics_srt_override = Path(lyrics_srt).resolve() if lyrics_srt else None
        requested_image_provider = self.image_config.get("provider", "qwen_image_3")
        self.image_provider_name = canonical_image_provider_name(requested_image_provider)
        image_provider_config = copy.deepcopy(
            (self.config.get("image_providers") or {}).get(self.image_provider_name, {})
        )
        image_workspace_env = image_provider_config.get("workspace_id_env")
        if image_workspace_env and not image_provider_config.get("workspace_id"):
            image_provider_config["workspace_id"] = get_env(image_workspace_env, "") or ""
        self.image_provider = create_image_provider(self.image_provider_name, image_provider_config)
        self.image_api_key = get_env(self.image_provider.api_key_env, "") or ""
        requested_video_provider = self.video_config.get("provider", "wan3")
        self.video_provider_name = canonical_video_provider_name(requested_video_provider)
        video_provider_config = copy.deepcopy((self.config.get("providers") or {}).get(self.video_provider_name, {}))
        workspace_env = video_provider_config.get("workspace_id_env")
        if workspace_env and not video_provider_config.get("workspace_id"):
            video_provider_config["workspace_id"] = get_env(workspace_env, "") or ""
        self.video_provider = create_video_provider(self.video_provider_name, video_provider_config)
        self.video_config = deep_merge(self.video_config, video_provider_config.get("scheduler", {}))
        self.video_api_key = get_env(self.video_provider.api_key_env, "") or ""
        self.state_path = self.output_dir / "state.json"
        self.manifest_path = self.output_dir / "generation_manifest.json"
        self.state_lock = threading.RLock()
        self.scene_map = {item["id"]: item for item in self.board.get("scenes", [])}
        self.cast_map = {item["id"]: item for item in self.board.get("cast", [])}
        direction = self.board.get("creative_direction", {})
        self.visual_unit_map = {item["id"]: item for item in direction.get("visual_units", []) if item.get("id")}
        self.development_stage_map = {
            item["id"]: item
            for item in direction.get("whole_film_treatment", {}).get("development_path", [])
            if item.get("id")
        }
        self.segment_map = {int(item["index"]): item for item in self.board.get("segments", [])}
        self.subshot_map = {
            int(subshot["global_index"]): subshot
            for segment in self.board.get("segments", [])
            for subshot in segment.get("sub_shots", [])
        }
        self.ensure_dirs()
        self.state = self.load_state()
        self.state.setdefault("assets", {})
        self.state["assets"].pop("scenes", None)
        for category in ("characters", "provider_identities", "wardrobe", "keyframes"):
            self.state["assets"].setdefault(category, {})
        self.state.setdefault("segments", {})
        # Full provider payloads are already persisted under requests/videos.
        # Keeping data-URL identity images in every segment record multiplies
        # state.json by the number of shots and makes each atomic save costly.
        for segment_record in self.state["segments"].values():
            segment_record.pop("api_request", None)
        self.state.setdefault("assembly", {})
        self.state.setdefault("models", {})
        self.state["models"]["image"] = None if self.video_provider.name == "seedance" else self.image_provider.model
        self.state["models"]["video"] = self.video_provider.model
        self.state["models"]["quality_control"] = (
            self.qc_config.get("model") or get_env("QWEN_MM_API_OMNI_MODEL", "qwen3.5-omni-plus")
            if self.qc_config.get("enabled")
            else None
        )
        self.state["image_provider"] = {
            "name": self.image_provider.name,
            "model": self.image_provider.model,
            "schema_version": self.image_provider.schema_version,
        }
        self.state.setdefault("image_providers", {})[self.image_provider.name] = copy.deepcopy(
            self.state["image_provider"]
        )
        self.state["video_provider"] = {
            "name": self.video_provider.name,
            "model": self.video_provider.model,
            "schema_version": self.video_provider.schema_version,
        }
        self.state.setdefault("video_providers", {})[self.video_provider.name] = copy.deepcopy(
            self.state["video_provider"]
        )
        self.state["input_compatibility_translations"] = self.compatibility_translations
        scheduler_state = self.state.setdefault("scheduler", {})
        previous_scheduler_provider = scheduler_state.get("provider")
        if previous_scheduler_provider and previous_scheduler_provider != self.video_provider.name:
            self.state.setdefault("scheduler_history", []).append(copy.deepcopy(scheduler_state))
            scheduler_state = {"provider": self.video_provider.name, "events": []}
            self.state["scheduler"] = scheduler_state
        scheduler_state["provider"] = self.video_provider.name
        configured_steps = self.video_config.get("submit_rpm_steps") or [self.video_config.get("submit_rpm", 30)]
        if float(self.video_config.get("submit_rpm", 30)) not in {float(value) for value in configured_steps}:
            configured_steps = [self.video_config.get("submit_rpm", 30), *configured_steps]
        self.submit_rate = AdaptiveSubmitRate(
            configured_steps,
            window_size=self.video_config.get("rate_error_window", 10),
            error_ratio=self.video_config.get("rate_error_ratio", 0.4),
            error_burst=self.video_config.get("rate_error_burst", 3),
            initial_rpm=scheduler_state.get("current_submit_rpm", self.video_config.get("submit_rpm", 30)),
        )
        scheduler_state.setdefault("events", [])
        scheduler_state["current_submit_rpm"] = self.submit_rate.rpm

    def ensure_dirs(self):
        for relative in [
            "generated/characters",
            "audio_segments",
            "video_segments/raw",
            "video_segments/normalized",
            "video_segments/candidates",
            "quality_control/media",
            "subtitles",
            "requests/images",
            "requests/videos",
            "responses/images",
            "responses/videos",
            "reports/quality_control",
            "logs/rejected",
            "reports",
        ]:
            (self.output_dir / relative).mkdir(parents=True, exist_ok=True)

    def load_state(self):
        if self.state_path.exists():
            return load_json(self.state_path)
        return {
            "created_at": timestamp(),
            "inputs": {
                "storyboard_json": str(self.storyboard_path),
                "source_audio": str(self.audio_path),
            },
            "models": {
                "image": self.image_provider.model,
                "video": self.video_provider.model,
            },
            "assets": {"characters": {}, "wardrobe": {}, "keyframes": {}},
            "segments": {},
            "assembly": {},
        }

    def save_state(self):
        with self.state_lock:
            self.state["updated_at"] = timestamp()
            atomic_json(self.state_path, self.state)
            atomic_json(self.manifest_path, self.state)

    def relative(self, path):
        return str(Path(path).resolve().relative_to(self.output_dir))

    def request(
        self,
        method,
        url,
        payload=None,
        timeout=300,
        api_key=None,
        api_key_env=None,
        extra_headers=None,
    ):
        selected_key = api_key or ""
        selected_env = api_key_env or "API key"
        if not selected_key:
            raise RuntimeError(f"{selected_env} is not configured")
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {selected_key}")
        request.add_header("Content-Type", "application/json")
        for key, value in (extra_headers or {}).items():
            request.add_header(key, value)
        for attempt in range(1, 6):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    body = response.read().decode("utf-8")
                    return response.status, json.loads(body), dict(response.headers)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError:
                    parsed = {"message": body}
                retry_after = exc.headers.get("Retry-After")
                if retry_after:
                    parsed.setdefault("_http", {})["retry_after"] = retry_after
                return exc.code, parsed, dict(exc.headers)
            except urllib.error.URLError:
                if attempt == 5:
                    raise
                time.sleep(attempt * 5)

    def download(self, url, output_path):
        output_path = Path(output_path)
        temporary = output_path.with_suffix(output_path.suffix + ".part")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        last_error = None
        curl_command = [
            "curl",
            "-L",
            "--fail",
            "--retry",
            "5",
            "--retry-all-errors",
            "--connect-timeout",
            "30",
            "--max-time",
            "1800",
            "--speed-time",
            "60",
            "--speed-limit",
            "1024",
            "--silent",
            "--show-error",
        ]
        if temporary.exists() and temporary.stat().st_size:
            curl_command.extend(["--continue-at", "-"])
        curl_command.extend([url, "-o", str(temporary)])
        try:
            subprocess.run(curl_command, check=True)
            os.replace(temporary, output_path)
            return
        except Exception as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
        for attempt in range(1, 6):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(request, timeout=300) as response, open(temporary, "wb") as handle:
                    shutil.copyfileobj(response, handle)
                os.replace(temporary, output_path)
                return
            except Exception as exc:
                last_error = exc
                temporary.unlink(missing_ok=True)
                time.sleep(attempt * 3)
        raise RuntimeError(f"Unable to download {url}: {last_error}")

    def record_asset(self, category, asset_id, record):
        with self.state_lock:
            self.state["assets"][category][asset_id] = record
            self.save_state()

    def selected_segments(self, indexes=None):
        if indexes is None:
            return sorted(self.segment_map.values(), key=lambda item: int(item["index"]))
        return [self.segment_map[index] for index in indexes]

    def style_summary(self):
        style = self.board.get("style_bible", {})
        notes = " ".join(style.get("reproduction_notes", []))
        return (
            f"整体视觉：{style.get('overall_visual_style', '')}。"
            f"色彩：{style.get('color_palette', '')}。"
            f"摄影质感：{style.get('film_look', '')}。"
            f"氛围：{style.get('mood', '')}。"
            f"构图规则：{style.get('composition_grammar', '')}。"
            f"摄影机规则：{style.get('camera_grammar', '')}。"
            f"光线规则：{style.get('lighting_grammar', '')}。"
            f"材质语言：{style.get('material_language', '')}。"
            f"制作设计：{style.get('production_design', '')}。"
            f"服装规则：{style.get('wardrobe_rules', '')}。{notes}"
        )

    def character_asset_style_summary(self):
        """Return only style fields that cannot introduce other cast or locations."""
        style = self.board.get("style_bible", {})
        return f"摄影质感：{style.get('film_look', '')}。"

    def creative_context_text(self, subshot):
        treatment = self.board.get("creative_direction", {}).get("whole_film_treatment", {})
        unit = self.visual_unit_map.get(subshot.get("visual_unit_id"), {})
        stage = self.development_stage_map.get(unit.get("development_stage_id"), {})
        motifs = treatment.get("recurring_motifs", [])
        parts = []
        for label, value in (
            ("全片创意核心", treatment.get("core_premise")),
            ("贯穿发展线", treatment.get("driving_thread")),
            ("视觉世界关系", treatment.get("visual_world_logic")),
        ):
            if value:
                parts.append(f"{label}：{value}")
        stage_values = [value for value in (stage.get("visible_state"), stage.get("development")) if value]
        if stage_values:
            parts.append("当前发展阶段：" + "；".join(str(value) for value in stage_values))
        if unit.get("visual_intent"):
            parts.append(f"当前视觉单元意图：{unit['visual_intent']}")
        if motifs:
            parts.append("可见母题及其阶段性变化：" + json.dumps(motifs, ensure_ascii=False))
        return "。".join(parts) + ("。" if parts else "")

    def image_config_for_category(self, category):
        config = dict(self.image_config)
        key = {"characters": "character_size"}.get(category)
        if key:
            override = self.image_provider.config.get(key) or config.get(key)
            if override:
                config["size_override"] = override
        negative_key = {"characters": "character_negative_prompt"}.get(category)
        if negative_key:
            override = self.image_provider.config.get(negative_key) or config.get(negative_key)
            if override:
                config["negative_prompt_override"] = override
        return config

    def observe_submit_rate(self, rate_limited, segment_index):
        if not self.video_config.get("adaptive_submit_rpm", True):
            return None
        event = self.submit_rate.observe(rate_limited)
        scheduler = self.state.setdefault("scheduler", {})
        scheduler["current_submit_rpm"] = self.submit_rate.rpm
        if event:
            event.update({"segment_index": segment_index, "at": timestamp()})
            scheduler.setdefault("events", []).append(event)
        self.save_state()
        return event

    def validate(self):
        self.validate_video_provider_storyboard()
        report = self.output_dir / "reports" / "execution_storyboard_validation.json"
        subprocess.run(
            [
                sys.executable,
                str(VIDEO_WORKFLOW_DIR / "scripts" / "validate_execution_storyboard.py"),
                str(self.storyboard_path),
                "--report",
                str(report),
            ],
            check=True,
        )
        return report

    def validate_video_provider_storyboard(self, indexes=None):
        errors = one_shot_request_contract_errors(self.board)
        errors.extend(self.video_provider.storyboard_errors(self.selected_segments(indexes)))
        report = self.output_dir / "reports" / "video_provider_validation.json"
        atomic_json(
            report,
            {
                "provider": self.video_provider.name,
                "model": self.video_provider.model,
                "valid": not errors,
                "errors": errors,
            },
        )
        if errors:
            raise RuntimeError("Video-provider validation failed: " + "; ".join(errors))
        return report

    def plan(self, indexes=None):
        self.validate_video_provider_storyboard(indexes)
        segments = self.selected_segments(indexes)
        scene_ids = {scene_id for segment in segments for scene_id in segment.get("scene_ids", [])}
        cast_ids = {cast_id for segment in segments for cast_id in segment.get("cast_present", [])}
        return {
            "storyboard": str(self.storyboard_path),
            "audio": str(self.audio_path),
            "output_dir": str(self.output_dir),
            "scenes": len(scene_ids),
            "cast": len(cast_ids),
            "development_stages": len(self.development_stage_map),
            "visual_units": len(self.visual_unit_map),
            "segments": len(segments),
            "image_provider": None if self.video_provider.name == "seedance" else self.image_provider.name,
            "identity_source": "ark_image_asset" if self.video_provider.name == "seedance" else "generated_portrait",
            "official_identity_assets": (
                self.video_provider.config.get("official_identity_assets", {})
                if self.video_provider.name == "seedance"
                else {}
            ),
            "video_provider": self.video_provider.name,
            "video_mode": "one_editorial_shot_per_request",
            "automation_boundaries": {
                "reference_storage": "identity portraits only; scenes remain per-shot text",
                "image_rate_control": "worker concurrency with 429 backoff; not a global RPM limiter",
                "visual_qc": "optional Omni semantic review under the configured local policy",
                "resume_scope": "recorded state in the same output directory",
            },
            "models": self.state["models"],
            "model_config_path": str(self.model_config_path) if self.model_config_path else None,
            "semantic_quality_control": {
                "enabled": bool(self.qc_config.get("enabled", False)),
                "source": self.qc_enabled_source,
                "strategy": self.qc_config.get("strategy") if self.qc_config.get("enabled") else None,
                "max_rounds": int(self.qc_config.get("max_rounds", 3)) if self.qc_config.get("enabled") else 0,
                "additional_omni_calls": bool(self.qc_config.get("enabled", False)),
                "video_regeneration_possible": bool(
                    self.qc_config.get("enabled", False) and self.qc_config.get("strategy") == "reject_major"
                ),
            },
        }

    def character_prompt(self, character):
        text = " ".join(str(character.get(key, "")) for key in ("role", "identity", "portrait_t2i_prompt")).lower()
        minor = any(term in text for term in ("child", "minor", "teen", "儿童", "孩子", "未成年", "少年", "少女"))
        nonhuman = any(
            term in text for term in ("robot", "creature", "nonhuman", "机器人", "非人类", "生物角色", "无脸", "头套")
        )
        if minor:
            subject_rule = "这是完全虚构、由AI生成且不对应任何现实人物的角色，严格保持剧本规定的未成年年龄。"
        elif nonhuman:
            subject_rule = (
                "这是完全虚构、由AI生成且不对应任何现实人物的非真人或遮面角色，严格保持剧本规定的头部与身体设计。"
            )
        else:
            subject_rule = "这是完全虚构、由AI生成且不对应任何现实人物、演员、名人、公众人物或私人个体的成年角色。"
        return (
            subject_rule + f"{character.get('portrait_t2i_prompt', '')}\n"
            f"身份定义：{character.get('identity', '')}\n"
            f"身份锚点：{json.dumps(character.get('identity_anchor', {}), ensure_ascii=False)}\n"
            f"{self.character_asset_style_summary()}\n"
            "真人电影级照片写实单人身份定妆照。输出一张单幅照片，不是三联画、拼贴、联络表、分镜板或多视图布局。"
            "画面中恰好一名剧本角色，头肩肖像，主体居中，素色无纹理背景，双眼与完整五官或剧本规定的头部设计清晰，"
            "年龄、脸部骨骼、眼距、鼻形、嘴形、肤色、发型和体型稳定准确；真实自然皮肤纹理与细小瑕疵。"
            "正面或轻微三分之四角度，中性表情与自然站姿，无剧情动作、遮脸、额外人物、文字或水印。"
            "不是插画、动漫、卡通、赛璐璐或2D绘画。"
        )

    def generate_image(self, category, asset_id, prompt, output_path, source_fields, upstream=None, image_urls=None):
        image_urls = list(image_urls or [])
        output_path = self.image_provider.output_path(output_path)
        image_config = self.image_config_for_category(category)
        signature_data = {
            **self.image_provider.signature_parameters(image_config),
            "prompt": prompt,
            "image_urls": image_urls,
            "source_fields": source_fields,
            "upstream_assets": upstream or [],
        }
        execution_signature = hashlib.sha256(
            json.dumps(signature_data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        existing = self.state["assets"][category].get(asset_id)
        reusable = bool(
            existing
            and existing.get("status") == "succeeded"
            and existing.get("output_path")
            and Path(existing.get("output_path", "")).exists()
            and existing.get("execution_signature") == execution_signature
        )
        if reusable:
            return existing
        request_path = self.output_dir / "requests/images" / f"{category}_{asset_id.replace(':', '_')}.json"
        response_path = self.output_dir / "responses/images" / f"{category}_{asset_id.replace(':', '_')}.json"
        payload = self.image_provider.build_payload(prompt, image_urls, image_config)
        atomic_json(request_path, payload)
        record = {
            "id": asset_id,
            "category": category,
            "source_fields": source_fields,
            "upstream_assets": upstream or [],
            "prompt": prompt,
            "api_request": payload,
            "provider": self.image_provider.name,
            "model": self.image_provider.model,
            "execution_signature": execution_signature,
            "request_path": self.relative(request_path),
            "response_path": self.relative(response_path),
            "output_path": str(output_path),
            "status": "running",
            "attempts": 0,
        }
        self.record_asset(category, asset_id, record)
        last_error = None
        for attempt in range(1, self.image_provider.max_attempts(image_config) + 1):
            record["attempts"] = attempt
            try:
                status, response, _ = self.request(
                    "POST",
                    self.image_provider.submit_url(),
                    payload,
                    timeout=300,
                    api_key=self.image_api_key,
                    api_key_env=self.image_provider.api_key_env,
                    extra_headers=self.image_provider.submit_headers(payload),
                )
                response.setdefault("_http", {})["status"] = status
                atomic_json(response_path, response)
                urls = self.image_provider.image_urls(response)
                url = urls[0] if urls else ""
                if status == 200 and url:
                    self.download(url, output_path)
                    record.update(
                        {
                            "status": "succeeded",
                            "url": url,
                            "completed_at": timestamp(),
                            "completed_epoch": time.time(),
                        }
                    )
                    self.record_asset(category, asset_id, record)
                    return record
                last_error = response
                if status == 429:
                    time.sleep(retry_delay(response, attempt))
                    continue
            except Exception as exc:
                last_error = repr(exc)
            time.sleep(min(30, attempt * 4))
        record.update({"status": "failed", "error": last_error})
        self.record_asset(category, asset_id, record)
        raise RuntimeError(f"Image generation failed for {category}:{asset_id}: {last_error}")

    def generate_base_assets(self, indexes=None):
        segments = self.selected_segments(indexes)
        cast_ids = sorted({cast_id for segment in segments for cast_id in segment.get("cast_present", [])})

        if self.video_provider.name == "seedance":
            self.validate_video_provider_storyboard(indexes)
            for cast_id in cast_ids:
                uri = self.video_provider.identity_reference_url({"cast_id": cast_id}, None)
                self.record_asset(
                    "provider_identities",
                    cast_id,
                    {
                        "cast_id": cast_id,
                        "provider": "seedance",
                        "source": "ark_image_asset",
                        "asset_uri": uri,
                        "status": "configured",
                    },
                )
            return

        def character_job(cast_id):
            character = self.cast_map[cast_id]
            return self.generate_image(
                "characters",
                cast_id,
                self.character_prompt(character),
                self.output_dir / "generated/characters" / f"{cast_id}_identity.jpg",
                [f"cast[{cast_id}].portrait_t2i_prompt", f"cast[{cast_id}].identity", "style_bible"],
            )

        workers = self.image_provider.workers(self.image_config)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(character_job, cast_id) for cast_id in cast_ids]
            for future in concurrent.futures.as_completed(futures):
                future.result()

    def generate_keyframes(self, indexes=None):
        report = self.output_dir / "reports" / "keyframes_skipped.json"
        atomic_json(
            report,
            {
                "status": "skipped",
                "reason": "execution uses identity references and per-shot scene text; per-shot human keyframes are unnecessary",
                "segments": [int(segment["index"]) for segment in self.selected_segments(indexes)],
            },
        )
        return report

    def camera_text(self, camera):
        return (
            f"景别{camera.get('shot_size', '')}，机位{camera.get('angle', '')}，"
            f"运镜{camera.get('movement', '')}：{camera.get('movement_detail', '')}；"
            f"主体与摄影机关系：{camera.get('subject_vs_camera', '')}。"
        )

    def expressive_action_text(self, subshot):
        action_design = subshot.get("action_design", {})
        action_steps = action_design.get("action_steps", [])
        details = []
        if action_steps:
            details.append("连续动作阶段：" + " → ".join(str(item) for item in action_steps))
        estimated = action_design.get("estimated_action_duration_sec")
        if isinstance(estimated, (int, float)):
            details.append(f"主动表演持续约{float(estimated):.2f}秒，不得用静止、普通走路或等待填充")
        if subshot.get("shot_type") == "dance":
            movement = subshot.get("movement_design", {})
            style_parts = [movement.get("dance_style"), movement.get("movement_quality")]
            details.append("舞蹈风格与动作质感：" + "；".join(str(item) for item in style_parts if item))
            movement_stages = movement.get("action_stages", [])
            if movement_stages:
                details.append("舞蹈动作阶段：" + " → ".join(str(item) for item in movement_stages))
            accent_actions = movement.get("accent_actions", [])
            if accent_actions:
                details.append("卡点动作：" + "；".join(str(item) for item in accent_actions))
            if movement.get("body_channels"):
                details.append("身体通道：" + str(movement["body_channels"]))
            if movement.get("camera_protection"):
                details.append("镜头必须保护：" + str(movement["camera_protection"]))
            details.append("普通走路、单纯摆手、长时间定格不能代替舞蹈；落点只作短暂标点")
        elif subshot.get("shot_type") == "performance":
            performance = subshot.get("performance_design", {})
            details.append(
                "表演设计："
                + "；".join(
                    str(item)
                    for item in (
                        performance.get("delivery_mode"),
                        performance.get("visible_performance"),
                        performance.get("intensity_change"),
                    )
                    if item
                )
            )
        return "。".join(item for item in details if item)

    def segment_identity_assets(self, segment):
        assets = []
        for cast_id in segment.get("cast_present", []):
            if self.video_provider.name == "seedance":
                uri = self.video_provider.identity_reference_url({"cast_id": cast_id}, None)
                assets.append(
                    {
                        "cast_id": cast_id,
                        "role": self.cast_map[cast_id].get("role", cast_id),
                        "asset_uri": uri,
                        "source": "ark_image_asset",
                        "source_hash": hashlib.sha256(uri.encode("utf-8")).hexdigest(),
                        "transport": "asset_uri",
                    }
                )
                continue
            record = self.state["assets"]["characters"].get(cast_id)
            if not (record and record.get("status") == "succeeded"):
                continue
            output_path = record.get("output_path")
            local_path = Path(output_path) if output_path else None
            if local_path and local_path.is_file():
                source_hash = hashlib.sha256(local_path.read_bytes()).hexdigest()
                transport = "inline_data_url"
            elif record.get("url"):
                source_hash = hashlib.sha256(record["url"].encode("utf-8")).hexdigest()
                transport = "temporary_url"
            else:
                continue
            assets.append(
                {
                    "cast_id": cast_id,
                    "role": self.cast_map[cast_id].get("role", cast_id),
                    "output_path": output_path,
                    "source_url": record.get("url"),
                    "source_hash": source_hash,
                    "transport": transport,
                }
            )
        return assets

    def identity_reference_url(self, reference):
        if self.video_provider.name == "seedance":
            return self.video_provider.identity_reference_url(reference, None)
        output_path = reference.get("output_path")
        local_path = Path(output_path) if output_path else None
        local_data_url = None
        if local_path and local_path.is_file():
            local_data_url = self.local_image_data_url(local_path)
        elif not reference.get("source_url"):
            raise RuntimeError(f"Identity reference {reference.get('cast_id')} is unavailable")
        return self.video_provider.identity_reference_url(reference, local_data_url)

    @staticmethod
    def local_image_data_url(path):
        path = Path(path)
        mime_types = {
            ".png": "image/png",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }
        mime_type = mime_types.get(path.suffix.lower(), "image/jpeg")
        return f"data:{mime_type};base64," + base64.b64encode(path.read_bytes()).decode("ascii")

    def validate_local_identity(self, reference):
        output_path = reference.get("output_path")
        local_path = Path(output_path) if output_path else None
        if not (local_path and local_path.is_file()):
            return
        self.video_provider.validate_local_identity(local_path)

    def segment_video_prompt(self, segment, identity_assets):
        identity_numbers = {asset["cast_id"]: offset for offset, asset in enumerate(identity_assets, start=1)}
        reference_lines = []
        for cast_id, number in identity_numbers.items():
            character = self.cast_map[cast_id]
            reference_lines.append(
                f"参考图片{number}是{character.get('role', cast_id)}的人物身份参考，只负责锁定同一名"
                "虚构成年人物的身份、年龄、脸部骨骼、肤色和发型，不负责场景、构图、服装或动作。"
            )

        shot_windows = sorted(segment.get("shot_windows", []), key=lambda item: item["t"][0])
        if len(shot_windows) != 1 or len(segment.get("sub_shots", [])) != 1:
            raise ValueError(f"Segment {segment['index']} must contain exactly one editorial shot")
        only_subshot = self.subshot_map[int(shot_windows[0]["sub_global_index"])]
        rationale = only_subshot.get("long_take_rationale", "")
        edit_contract = (
            "本次请求只生成一个连续编辑镜头。不得在镜头内部插入硬切、叠化、匹配剪辑、另一机位或另一场景；"
            "镜头边界由本地装配器在本次请求之外完成。"
            + (f"长镜头依据：{rationale}。" if rationale else "镜头内部通过连续动作和视觉变化响应音乐。")
        )

        parts = [
            f"真人实拍电影级音乐视频，整段时长{float(segment['duration_sec']):.2f}秒。{self.style_summary()}",
            self.creative_context_text(only_subshot),
            edit_contract,
            "人物身份参考只控制身份，不控制场景、构图、服装或动作。每个镜头的环境由其完整文字场景设定直接生成，"
            "可形成符合该镜头功能的新构图、景深、材质和局部陈设。服装、站位、动作、表情和摄影机全部按下方镜头要求执行。",
            "\n".join(reference_lines),
            "真人自然但富有表现力的表演，动作具有真实重心、连续律动、呼吸、目光、躯干意图和物理惯性。"
            "动作持续响应完整输入音频的乐句、切分、填充、歌词重音和强弱变化，不得用镜头运动、普通走路、静止摆姿代替人物动作。"
            "本镜头的人物名单是硬约束：未列出的人物不得出现，列出的人物不得复制、融合或缺失。",
        ]

        for shot_window in shot_windows:
            subshot = self.subshot_map[int(shot_window["sub_global_index"])]
            scene_id = subshot["scene_id"]
            scene = self.scene_map[scene_id]
            sync = subshot.get("audio_sync", {})
            visual_design = subshot.get("visual_design", {})
            edit_anchor = sync.get("edit_anchor", {})
            table = subshot.get("reference_image", {}).get("cast_scene_table", {})
            entries = {entry.get("id"): entry for entry in table.get("characters", [])}
            cast_present = list(subshot.get("cast_present", []))
            cast_labels = []
            character_actions = []
            for cast_id in cast_present:
                character = self.cast_map[cast_id]
                role = character.get("role", cast_id)
                number = identity_numbers.get(cast_id)
                cast_labels.append(f"{role}{f'（参考图片{number}）' if number else ''}")
                entry = entries.get(cast_id, {})
                wardrobe = scene.get("wardrobe", {}).get(cast_id, "")
                character_actions.append(
                    f"{role}位于{entry.get('position_in_frame', '剧本规定位置')}，朝向{entry.get('facing', '任务方向')}，"
                    f"穿着{wardrobe or '剧本规定服装'}，完成“{entry.get('action') or shot_window.get('what') or subshot.get('shot_summary', '')}”，"
                    f"表情{entry.get('expression', '克制自然')}。"
                )
            composition = "、".join(cast_labels) if cast_labels else "环境空镜（不含主要角色）"
            lip_roles = [
                self.cast_map[cast_id].get("role", cast_id)
                for cast_id in sync.get("lip_sync_ids", [])
                if cast_id in self.cast_map
            ]
            if sync.get("vocal_present") and lip_roles:
                mouth = (
                    f"逐字演唱原歌词“{sync.get('lyric', '')}”，只有{'、'.join(lip_roles)}产生准确口型，其他人物闭嘴。"
                )
            elif sync.get("vocal_present"):
                mouth = f"音乐中有人声歌词“{sync.get('lyric', '')}”，但画面人物不演唱，所有可见嘴部自然关闭。"
            else:
                mouth = "无演唱，所有可见嘴部自然关闭。"
            start, end = shot_window["t"]
            scene_instruction = (
                f"{float(start):.2f}–{float(end):.2f}秒：生成镜头场景“{scene.get('name', scene_id)}”；"
                f"环境与空间：{scene.get('setting', '')}；布局与调度区：{scene.get('layout', scene.get('blocking_map', ''))}；"
                f"光线：{scene.get('lighting', '')}；色彩：{scene.get('palette', '')}；"
                f"材质与制作细节：{scene.get('material_details', scene.get('materials', ''))}。"
            )
            window = [
                scene_instruction,
                f"镜头画面：{visual_design.get('image_content', '')}；"
                f"构图：{visual_design.get('composition', '')}；"
                f"光线、色彩与材质：{visual_design.get('lighting_color_material', '')}；"
                f"镜头内可见变化：{visual_design.get('visible_change', '')}；"
                f"交给下一镜头的画面或动作：{visual_design.get('handoff_to_next', '')}。",
                f"镜头起点依据：{edit_anchor.get('source', 'storyboard edit evidence')}，锚点时间{edit_anchor.get('source_time_sec', '')}秒，"
                f"证据“{edit_anchor.get('evidence', '')}”；选择原因：{edit_anchor.get('selection_reason', '')}。",
                f"画面中恰好出现：{composition}。",
                f"剧情：{subshot.get('shot_summary', '')}；{shot_window.get('what', '')}。导演意图：{subshot.get('director_note', '')}。",
                " ".join(character_actions),
                self.expressive_action_text(subshot),
                f"{self.camera_text(subshot.get('camera', {}))}{mouth}",
                "时间窗末尾完成动作收束，保持道具、服装、空间方向和人物状态连续。",
            ]
            parts.append(" ".join(item for item in window if item))

        scene_id = only_subshot["scene_id"]
        scene_label = f"场景[{self.scene_map[scene_id].get('name', scene_id)}]"
        parts.append(
            f"整个结果保持在{scene_label}内完成这一个连续镜头，不得自行切换到其他机位、镜头或场景。"
            "场景可在镜头内连续变化，但不得融合或切换到其他场景。"
            "禁止身份漂移、改变年龄、换脸、换发型、额外人物、人物复制、融脸、"
            "不自然五官、错误道具、场景突变、2D动画、动漫、卡通、可读文字、字幕、歌词、标志和水印。"
            "本次生成结果本身就是一个镜头；与前后镜头的切换由本地装配阶段完成。"
        )
        return self.video_provider.render_prompt("\n\n".join(parts))

    def cut_audio(self, segment):
        output = self.output_dir / "audio_segments" / f"segment_{int(segment['index']):03d}.wav"
        metadata_path = output.with_suffix(".json")
        source_stat = self.audio_path.stat()
        expected = {
            "source_path": str(self.audio_path),
            "source_size": source_stat.st_size,
            "source_mtime_ns": source_stat.st_mtime_ns,
            "start_sec": float(segment["start_sec"]),
            "duration_sec": float(segment["duration_sec"]),
        }
        if output.exists() and metadata_path.exists() and load_json(metadata_path) == expected:
            return output
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                str(segment["start_sec"]),
                "-t",
                str(segment["duration_sec"]),
                "-i",
                str(self.audio_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(output),
            ],
            check=True,
        )
        atomic_json(metadata_path, expected)
        return output

    def wan_reference_audio_url(self, record, record_path):
        """Resolve or upload the segment WAV without assuming inline audio support."""
        index = int(record["segment_index"])
        path = Path(record["audio_path"])
        source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        cached = record.get("reference_audio", {})
        cached_url = cached.get("url", "")
        cached_age = time.time() - float(cached.get("prepared_epoch", 0) or 0)
        if cached.get("source_hash") == source_hash and cached_url:
            if not cached_url.startswith("oss://") or cached_age < 47 * 3600:
                return cached_url

        provider_config = self.video_provider.config
        configured_urls = provider_config.get("reference_audio_urls") or {}
        url = configured_urls.get(str(index)) or configured_urls.get(index)
        mode = "configured_url"
        if not url and provider_config.get("reference_audio_url_template"):
            url = str(provider_config["reference_audio_url_template"]).format(
                index=index,
                filename=path.name,
                source_hash=source_hash,
            )
            mode = "configured_url_template"
        if not url:
            mode = provider_config.get("reference_audio_mode", "dashscope_oss")
            if mode != "dashscope_oss":
                raise RuntimeError(
                    "Wan 3.0 needs a remote reference-audio URL. Configure reference_audio_urls, "
                    "reference_audio_url_template, or reference_audio_mode=dashscope_oss."
                )
            executable = shutil.which(provider_config.get("dashscope_cli", "dashscope"))
            if not executable:
                raise RuntimeError(
                    "Wan 3.0 audio upload requires DashScope CLI >=1.24.0, or a configured remote audio URL"
                )
            environment = os.environ.copy()
            environment["DASHSCOPE_API_KEY"] = self.video_api_key
            completed = subprocess.run(
                [executable, "oss.upload", "--model", self.video_provider.model, "--file", str(path)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=environment,
            )
            match = re.search(r"oss://[^\s]+", completed.stdout)
            if not match:
                raise RuntimeError("DashScope upload completed without returning an oss:// URL")
            url = match.group(0).rstrip(".,;)")

        audio_record = {
            "mode": mode,
            "source_path": str(path),
            "source_hash": source_hash,
            "size": path.stat().st_size,
            "mime_type": "audio/wav",
            "url": url,
            "prepared_at": timestamp(),
            "prepared_epoch": time.time(),
            "status": "prepared",
        }
        atomic_json(record_path, audio_record)
        record["reference_audio"] = audio_record
        self.state["segments"][str(index)] = record
        self.save_state()
        return url

    def reference_audio_url(self, record, record_path):
        if self.video_provider.reference_audio_transport == "remote_url":
            return self.wan_reference_audio_url(record, record_path)
        if self.video_provider.reference_audio_transport != "inline_data_url":
            raise RuntimeError(
                f"Unsupported reference-audio transport: {self.video_provider.reference_audio_transport}"
            )
        index = int(record["segment_index"])
        path = Path(record["audio_path"])
        source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        audio_record = {
            "mode": "inline_data_url",
            "source_path": str(path),
            "source_hash": source_hash,
            "size": path.stat().st_size,
            "mime_type": "audio/wav",
            "prepared_at": timestamp(),
            "status": "prepared",
        }
        atomic_json(record_path, audio_record)
        record["reference_audio"] = audio_record
        self.state["segments"][str(index)] = record
        self.save_state()
        return "data:audio/wav;base64," + base64.b64encode(path.read_bytes()).decode("ascii")

    def prepare_segment(self, segment):
        index = int(segment["index"])
        existing = self.state["segments"].get(str(index), {})
        subshot = segment["sub_shots"][0]
        visual_unit_id = subshot.get("visual_unit_id")
        visual_unit = self.visual_unit_map.get(visual_unit_id, {})
        identity_assets = self.segment_identity_assets(segment)
        required_asset_cast_ids = list(segment.get("cast_present", []))
        active_cast_ids = {item["cast_id"] for item in identity_assets}
        missing_assets = [cast_id for cast_id in required_asset_cast_ids if cast_id not in active_cast_ids]
        if missing_assets:
            raise RuntimeError(f"Segment {index} is missing identity reference images: {missing_assets}")
        audio_path = self.cut_audio(segment)
        audio_hash = hashlib.sha256(audio_path.read_bytes()).hexdigest()
        source_audio_stat = self.audio_path.stat()
        video_prompt = self.segment_video_prompt(segment, identity_assets)
        signature_data = {
            "mode": "one_editorial_shot_per_request",
            **self.video_provider.signature_parameters(self.video_config),
            "duration_sec": float(segment["duration_sec"]),
            "segment_audio_hash": audio_hash,
            "source_audio_identity": {
                "size": source_audio_stat.st_size,
                "mtime_ns": source_audio_stat.st_mtime_ns,
            },
            "identity_references": [
                {"source_hash": item["source_hash"], "asset_uri": item.get("asset_uri")} for item in identity_assets
            ],
            "video_prompt": video_prompt,
        }
        execution_signature = hashlib.sha256(
            json.dumps(signature_data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        reusable = existing.get("execution_signature") == execution_signature
        record = {
            **(existing if reusable else {}),
            "segment_index": index,
            "absolute_time": {"start_sec": segment["start_sec"], "end_sec": segment["end_sec"]},
            "duration_sec": float(segment["duration_sec"]),
            "visual_unit_id": visual_unit_id,
            "development_stage_id": visual_unit.get("development_stage_id"),
            "api_duration_sec": self.video_provider.api_duration(segment["duration_sec"]),
            "provider": self.video_provider.name,
            "provider_schema": self.video_provider.schema_version,
            "video_model": self.video_provider.model,
            "audio_path": str(audio_path),
            "audio_hash": audio_hash,
            "identity_assets": identity_assets,
            "video_prompt": video_prompt,
            "requested_assembly_mode": segment.get("assembly_mode"),
            "assembly_mode": "single_shot_request",
            "execution_signature": execution_signature,
            "status": existing.get("status", "prepared") if reusable else "prepared",
        }
        self.state["segments"][str(index)] = record
        self.save_state()
        return record

    def submit_segment(self, segment):
        record = self.prepare_segment(segment)
        if record.get("status") in {"submitted", "running", "succeeded"} and record.get("task_id"):
            return record
        index = int(segment["index"])
        self.video_provider.validate_configuration()
        for reference in record["identity_assets"]:
            self.validate_local_identity(reference)
        audio_record_path = self.output_dir / "responses/videos" / f"segment_{index:03d}_audio.json"
        audio_url = self.reference_audio_url(record, audio_record_path)
        identity_urls = [self.identity_reference_url(item) for item in record["identity_assets"]]
        payload = self.video_provider.build_payload(
            record,
            identity_urls,
            audio_url,
            self.video_config,
        )
        request_path = self.output_dir / "requests/videos" / f"segment_{index:03d}.json"
        response_path = self.output_dir / "responses/videos" / f"segment_{index:03d}_submit.json"
        atomic_json(request_path, self.video_provider.persisted_payload(payload))
        status = 0
        response = {}
        for attempt in range(1, int(self.video_config.get("max_submit_attempts", 5)) + 1):
            status, response, _ = self.request(
                "POST",
                self.video_provider.submit_url(),
                payload,
                timeout=300,
                api_key=self.video_api_key,
                api_key_env=self.video_provider.api_key_env,
                extra_headers=self.video_provider.submit_headers(payload),
            )
            if self.video_provider.http_succeeded(status) and self.video_provider.task_id(response):
                break
            message = error_message(response)
            if status == 429 or "RPM" in message:
                time.sleep(retry_delay(response, attempt, self.submit_interval()))
                continue
            break
        response.setdefault("_http", {})["status"] = status
        atomic_json(response_path, response)
        record.update(
            {
                "audio_url_mode": (record.get("reference_audio") or {}).get("mode", "remote_url"),
                "reference_audio": record.get("reference_audio", {"mode": "remote_url"}),
                "provider": self.video_provider.name,
                "provider_schema": self.video_provider.schema_version,
                "request_path": self.relative(request_path),
                "submit_response_path": self.relative(response_path),
                "submitted_at": timestamp(),
            }
        )
        task_id = self.video_provider.task_id(response)
        if self.video_provider.http_succeeded(status) and task_id:
            record.update(
                {
                    "task_id": task_id,
                    "status": "submitted",
                    "last_poll_epoch": time.time(),
                }
            )
            record.pop("error", None)
        else:
            record.update({"status": "submit_failed", "error": response})
        self.state["segments"][str(index)] = record
        self.save_state()
        return record

    def submit_interval(self):
        return 60.0 / float(self.submit_rate.rpm) * float(self.video_config.get("rate_safety_factor", 1.0))

    def submit_until_accepted(self, segment, rate_requeues, error_requeues, last_submit):
        index = int(segment["index"])
        while True:
            wait_sec = self.submit_interval() - (time.monotonic() - last_submit)
            if wait_sec > 0:
                time.sleep(wait_sec)
            record = self.submit_segment(segment)
            last_submit = time.monotonic()
            if record.get("status") != "submit_failed":
                self.observe_submit_rate(False, index)
                return last_submit
            response = record.get("error") or {}
            if is_rate_limit_response(response):
                rate_requeues[index] = rate_requeues.get(index, 0) + 1
                self.observe_submit_rate(True, index)
                maximum = int(self.video_config.get("max_submit_requeues", 120))
                if rate_requeues[index] > maximum:
                    raise RuntimeError(f"Segment {index} exceeded {maximum} rate-limit requeues; no task was skipped.")
                continue
            status = int(response_status(response) or 0)
            if status >= 500 or status == 0:
                error_requeues[index] = error_requeues.get(index, 0) + 1
                maximum = int(self.video_config.get("max_submit_error_requeues", 20))
                if error_requeues[index] > maximum:
                    raise RuntimeError(
                        f"Segment {index} exceeded {maximum} transient submission retries; no task was skipped."
                    )
                time.sleep(retry_delay(response, error_requeues[index], self.submit_interval()))
                continue
            raise RuntimeError(
                f"Segment {index} submission failed with non-retryable status {status}. "
                "Inspect the saved response; execution stopped rather than skipping the task."
            )

    def require_segments_succeeded(self, segments):
        incomplete = {
            int(segment["index"]): self.state["segments"].get(str(int(segment["index"])), {}).get("status", "missing")
            for segment in segments
            if self.state["segments"].get(str(int(segment["index"])), {}).get("status") != "succeeded"
        }
        if incomplete:
            raise RuntimeError(f"Execution cannot finish with incomplete tasks: {incomplete}. No segment was skipped.")

    def query_interval(self):
        return 60.0 / float(self.video_config["query_rpm"]) * float(self.video_config.get("rate_safety_factor", 1.05))

    def poll_segment(self, index):
        record = self.state["segments"][str(index)]
        status, response, _ = self.request(
            "GET",
            self.video_provider.query_url(record["task_id"]),
            timeout=180,
            api_key=self.video_api_key,
            api_key_env=self.video_provider.api_key_env,
            extra_headers=self.video_provider.query_headers(),
        )
        response_path = self.output_dir / "responses/videos" / f"segment_{index:03d}_query.json"
        response.setdefault("_http", {})["status"] = status
        atomic_json(response_path, response)
        remote_status = self.video_provider.remote_status(response)
        record.update(
            {
                "query_response_path": self.relative(response_path),
                "last_polled_at": timestamp(),
                "remote_status": remote_status,
            }
        )
        video_url = self.video_provider.video_url(response)
        if self.video_provider.http_succeeded(status) and self.video_provider.succeeded(remote_status) and video_url:
            record.update(
                {
                    "status": "download_pending",
                    "video_url": video_url,
                    "remote_completed_at": timestamp(),
                }
            )
        elif self.video_provider.http_succeeded(status) and self.video_provider.failed(remote_status):
            record.update({"status": "failed", "error": response})
        else:
            record["status"] = "running"
            if not self.video_provider.http_succeeded(status):
                record["query_error"] = response
        self.state["segments"][str(index)] = record
        self.save_state()
        return record

    def download_segment(self, index, backend="curl"):
        record = self.state["segments"][str(index)]
        if record.get("status") != "download_pending":
            return record
        output = self.output_dir / "video_segments/raw" / f"segment_{index:03d}.mp4"
        try:
            self.download(record["video_url"], output)
        except Exception as exc:
            record.update({"status": "download_failed", "error": str(exc)})
            self.state["segments"][str(index)] = record
            self.save_state()
            raise
        record.update(
            {
                "status": "succeeded",
                "output_path": str(output),
                "output_signature": record["execution_signature"],
                "completed_at": timestamp(),
                "download_backend": backend,
            }
        )
        record.pop("error", None)
        self.state["segments"][str(index)] = record
        self.save_state()
        return record

    def run_videos(self, indexes=None, quality_control=True):
        self.validate_video_provider_storyboard(indexes)
        segments = self.selected_segments(indexes)
        for segment in segments:
            self.prepare_segment(segment)

        for segment in segments:
            index = int(segment["index"])
            record = self.state["segments"][str(index)]
            output = self.output_dir / "video_segments/raw" / f"segment_{index:03d}.mp4"
            if (
                record.get("status") == "succeeded"
                and record.get("output_signature") == record.get("execution_signature")
                and output.exists()
                and output.stat().st_size
            ):
                record["output_path"] = str(output)
                self.state["segments"][str(index)] = record
            elif record.get("status") == "succeeded":
                record["status"] = "prepared"
                self.state["segments"][str(index)] = record

        pending = [
            segment
            for segment in segments
            if self.state["segments"][str(int(segment["index"]))].get("status")
            not in {"submitted", "running", "download_pending", "succeeded"}
        ]
        rate_requeues = {int(segment["index"]): 0 for segment in pending}
        error_requeues = {int(segment["index"]): 0 for segment in pending}
        last_submit = 0.0
        if pending:
            batch_started = time.monotonic()
            initial_interval = self.submit_interval()

            def submit_job(position, segment):
                previous_slot = batch_started + position * initial_interval - initial_interval
                return self.submit_until_accepted(segment, rate_requeues, error_requeues, previous_slot)

            submit_workers = min(len(pending), int(self.video_config.get("submit_workers", 12)))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, submit_workers)) as executor:
                futures = [executor.submit(submit_job, position, segment) for position, segment in enumerate(pending)]
                for future in concurrent.futures.as_completed(futures):
                    last_submit = max(last_submit, future.result())

        download_workers = int(self.video_config.get("download_workers", 12))
        download_futures = {}
        queued_downloads = set()
        download_errors = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=download_workers) as executor:
            last_query = 0.0
            while True:
                for future in list(download_futures):
                    if not future.done():
                        continue
                    index, output = download_futures.pop(future)
                    record = self.state["segments"][str(index)]
                    try:
                        future.result()
                    except Exception as exc:
                        record.update({"status": "download_failed", "error": str(exc)})
                        self.state["segments"][str(index)] = record
                        self.save_state()
                        # Persist the failure without interrupting other provider tasks.
                        download_errors[index] = exc
                        continue
                    record.update(
                        {
                            "status": "succeeded",
                            "output_path": str(output),
                            "output_signature": record["execution_signature"],
                            "completed_at": timestamp(),
                            "download_backend": "curl",
                        }
                    )
                    record.pop("error", None)
                    self.state["segments"][str(index)] = record
                    self.save_state()

                # Transfer files in workers; only this thread reads/writes segment state.
                # Include download_pending tasks restored from an interrupted run.
                for segment in segments:
                    index = int(segment["index"])
                    record = self.state["segments"][str(index)]
                    if record.get("status") == "download_pending" and index not in queued_downloads:
                        output = self.output_dir / "video_segments/raw" / f"segment_{index:03d}.mp4"
                        future = executor.submit(self.download, record["video_url"], output)
                        download_futures[future] = (index, output)
                        queued_downloads.add(index)

                running = sorted(
                    [
                        int(segment["index"])
                        for segment in segments
                        if self.state["segments"][str(int(segment["index"]))].get("status") in {"submitted", "running"}
                    ],
                    key=lambda index: self.state["segments"][str(index)].get("last_poll_epoch", 0),
                )
                queried = False
                for index in running:
                    record = self.state["segments"][str(index)]
                    if (
                        time.time() - record.get("last_poll_epoch", 0) >= float(self.video_config["min_repoll_sec"])
                        and time.monotonic() - last_query >= self.query_interval()
                    ):
                        record["last_poll_epoch"] = time.time()
                        self.state["segments"][str(index)] = record
                        self.save_state()
                        result = self.poll_segment(index)
                        last_query = time.monotonic()
                        queried = True
                        if result.get("status") == "failed":
                            retries = int(result.get("generation_requeues", 0)) + 1
                            maximum = int(self.video_config.get("max_generation_requeues", 3))
                            if retries > maximum:
                                raise RuntimeError(
                                    f"Segment {index} failed generation after {maximum} retries. "
                                    "Execution stopped rather than skipping the task."
                                )
                            for key in ("task_id", "video_url", "remote_status", "error"):
                                result.pop(key, None)
                            result.update({"status": "prepared", "generation_requeues": retries})
                            self.state["segments"][str(index)] = result
                            self.save_state()
                            last_submit = self.submit_until_accepted(
                                self.segment_map[index], rate_requeues, error_requeues, last_submit
                            )
                        break
                if not running and not download_futures:
                    break
                if not queried:
                    time.sleep(0.05)

        if download_errors:
            failed_indexes = sorted(download_errors)
            raise RuntimeError(f"Video downloads failed for segments {failed_indexes}") from download_errors[
                failed_indexes[0]
            ]
        self.save_state()
        self.require_segments_succeeded(segments)
        if quality_control:
            self.run_quality_control(indexes)

    def prepare_video_packages(self, indexes=None):
        self.validate_video_provider_storyboard(indexes)
        packages = []
        for segment in self.selected_segments(indexes):
            record = self.prepare_segment(segment)
            identity_preview_urls = [
                (
                    item["asset_uri"]
                    if self.video_provider.name == "seedance"
                    else f"https://music2mv.invalid/identity/{item['cast_id']}.jpg"
                )
                for item in record["identity_assets"]
            ]
            audio_preview_url = f"https://music2mv.invalid/audio/segment_{record['segment_index']:03d}.wav"
            request_preview = self.video_provider.build_payload(
                record,
                identity_preview_urls,
                audio_preview_url,
                self.video_config,
                validate=False,
            )
            packages.append(
                {
                    "segment_index": record["segment_index"],
                    "absolute_time": record["absolute_time"],
                    "duration_sec": record["duration_sec"],
                    "visual_unit_id": record.get("visual_unit_id"),
                    "development_stage_id": record.get("development_stage_id"),
                    "provider": self.video_provider.name,
                    "video_model": self.video_provider.model,
                    "audio_path": record["audio_path"],
                    "identity_assets": record["identity_assets"],
                    "video_prompt": record["video_prompt"],
                    "request_preview": request_preview,
                    "assembly_mode": record["assembly_mode"],
                }
            )
        path = self.output_dir / "reports" / "video_generation_packages.json"
        atomic_json(path, packages)
        return path

    def qc_prompt(self, segment):
        configured_path = self.qc_config.get("prompt_path")
        prompt_path = Path(configured_path).expanduser().resolve() if configured_path else DEFAULT_SHOT_QC_PROMPT_PATH
        if not prompt_path.is_file():
            raise FileNotFoundError(f"quality-control prompt is unavailable: {prompt_path}")
        window_by_shot = {
            int(window["sub_global_index"]): window.get("t", [])
            for window in segment.get("shot_windows", [])
            if window.get("sub_global_index") is not None
        }
        cast_ids = list(
            dict.fromkeys(cast_id for shot in segment.get("sub_shots", []) for cast_id in shot.get("cast_present", []))
        )
        scene_ids = list(
            dict.fromkeys(shot.get("scene_id") for shot in segment.get("sub_shots", []) if shot.get("scene_id"))
        )
        shots = []
        for subshot in segment.get("sub_shots", []):
            global_index = int(subshot["global_index"])
            shots.append(
                {
                    "global_index": global_index,
                    "local_time": window_by_shot.get(global_index),
                    "absolute_time": [subshot.get("start_sec"), subshot.get("end_sec")],
                    "shot_type": subshot.get("shot_type"),
                    "shot_function": subshot.get("shot_function"),
                    "shot_summary": subshot.get("shot_summary"),
                    "scene_id": subshot.get("scene_id"),
                    "cast_present": subshot.get("cast_present", []),
                    "visual_design": subshot.get("visual_design", {}),
                    "action_design": subshot.get("action_design", {}),
                    "movement_design": subshot.get("movement_design", {}),
                    "performance_design": subshot.get("performance_design", {}),
                    "camera": subshot.get("camera", {}),
                    "camera_geometry": subshot.get("camera_geometry", {}),
                    "continuity": subshot.get("continuity", {}),
                    "reference_image": subshot.get("reference_image", {}),
                    "audio_sync": subshot.get("audio_sync", {}),
                    "transition_in": subshot.get("transition_in"),
                    "transition_basis": subshot.get("transition_basis"),
                }
            )
        review_package = {
            "segment_index": int(segment["index"]),
            "duration_sec": float(segment["duration_sec"]),
            "assembly_mode": segment.get("assembly_mode"),
            "style_bible": self.board.get("style_bible", {}),
            "cast": [self.cast_map[cast_id] for cast_id in cast_ids if cast_id in self.cast_map],
            "scenes": [self.scene_map[scene_id] for scene_id in scene_ids if scene_id in self.scene_map],
            "shots": shots,
            "extra_requirements": self.qc_config.get("extra_requirements", ""),
        }
        return (
            prompt_path.read_text(encoding="utf-8")
            + "\n\nREVIEW_PACKAGE_JSON\n"
            + json.dumps(review_package, ensure_ascii=False, indent=2)
        )

    def prepare_qc_media(self, segment, candidate_path, round_index):
        index = int(segment["index"])
        record = self.state["segments"][str(index)]
        audio_path = Path(record["audio_path"])
        output = self.output_dir / "quality_control/media" / f"segment_{index:03d}_round_{round_index:02d}.mp4"
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(candidate_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError(f"failed to prepare Omni QC media for segment {index}: {completed.stderr.strip()}")
        return output

    def review_qc_candidate(self, segment, candidate_path, round_index):
        prompt = self.qc_prompt(segment)
        media_path = self.prepare_qc_media(segment, candidate_path, round_index)
        arguments = {
            "video_path": str(media_path),
            "prompt": prompt,
            "output_format": "json",
            "temperature": float(self.qc_config.get("temperature", 0.01)),
            "max_tokens": int(self.qc_config.get("max_tokens", 8192)),
            "fps": float(self.qc_config.get("fps", 2.0)),
            "max_pixels": int(self.qc_config.get("max_pixels", 200704)),
        }
        if self.model_config_path:
            arguments["model_config_path"] = str(self.model_config_path)
        for key in ("model", "base_url"):
            if self.qc_config.get(key):
                arguments[key] = self.qc_config[key]
        api_key_env = self.qc_config.get("api_key_env", "DASHSCOPE_API_KEY")
        api_key = get_env(api_key_env, "") or ""
        if api_key:
            arguments["api_key"] = api_key
        raw_result = run_omni_call(arguments)
        normalized = normalize_qc_result(raw_result, segment)
        report_path = (
            self.output_dir
            / "reports/quality_control"
            / f"segment_{int(segment['index']):03d}_round_{round_index:02d}.json"
        )
        atomic_json(
            report_path,
            {
                "review": normalized,
                "raw_omni_response": raw_result,
                "review_media": self.relative(media_path),
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "model": self.qc_config.get("model") or get_env("QWEN_MM_API_OMNI_MODEL", "qwen3.5-omni-plus"),
            },
        )
        return normalized, report_path

    def reset_segment_for_qc_regeneration(self, segment):
        index = int(segment["index"])
        record = self.state["segments"][str(index)]
        for key in (
            "task_id",
            "video_url",
            "remote_status",
            "error",
            "query_error",
            "output_signature",
            "completed_at",
            "normalized_output_path",
            "normalized_frame_count",
        ):
            record.pop(key, None)
        record["status"] = "prepared"
        record["quality_regeneration_count"] = int(record.get("quality_regeneration_count", 0)) + 1
        self.state["segments"][str(index)] = record
        self.save_state()

    def quality_control_segment(self, segment):
        index = int(segment["index"])
        strategy = self.qc_config.get("strategy", "reject_major")
        if strategy not in {"accept_all", "reject_major"}:
            raise ValueError("quality_control.strategy must be accept_all or reject_major")
        max_rounds = int(self.qc_config.get("max_rounds", 3))
        if not 1 <= max_rounds <= 10:
            raise ValueError("quality_control.max_rounds must be between 1 and 10")
        prompt = self.qc_prompt(segment)
        record = self.state["segments"][str(index)]
        qc_signature_data = {
            "execution_signature": record.get("execution_signature"),
            "strategy": strategy,
            "max_rounds": max_rounds,
            "model": self.qc_config.get("model"),
            "base_url": self.qc_config.get("base_url"),
            "api_key_env": self.qc_config.get("api_key_env"),
            "temperature": self.qc_config.get("temperature"),
            "max_tokens": self.qc_config.get("max_tokens"),
            "fps": self.qc_config.get("fps"),
            "max_pixels": self.qc_config.get("max_pixels"),
            "prompt": prompt,
        }
        qc_signature = hashlib.sha256(
            json.dumps(qc_signature_data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        existing_qc = record.get("quality_control", {})
        selected_existing = existing_qc.get("selected_candidate_path")
        if (
            existing_qc.get("execution_signature") == qc_signature
            and existing_qc.get("status") in {"accepted", "fallback_selected"}
            and selected_existing
            and (self.output_dir / selected_existing).is_file()
        ):
            raw_output = self.output_dir / "video_segments/raw" / f"segment_{index:03d}.mp4"
            shutil.copy2(self.output_dir / selected_existing, raw_output)
            return existing_qc

        rounds = []
        selected = None
        for round_index in range(1, max_rounds + 1):
            if round_index > 1:
                self.reset_segment_for_qc_regeneration(segment)
                self.run_videos([index], quality_control=False)
            record = self.state["segments"][str(index)]
            raw_output = Path(
                record.get("output_path") or self.output_dir / "video_segments/raw" / f"segment_{index:03d}.mp4"
            )
            if record.get("status") != "succeeded" or not raw_output.is_file():
                raise RuntimeError(f"segment {index} has no successful video candidate for quality control")
            candidate_dir = self.output_dir / "video_segments/candidates" / f"segment_{index:03d}"
            candidate_dir.mkdir(parents=True, exist_ok=True)
            candidate_path = candidate_dir / f"round_{round_index:02d}.mp4"
            shutil.copy2(raw_output, candidate_path)
            review, report_path = self.review_qc_candidate(segment, candidate_path, round_index)
            candidate = {
                "round": round_index,
                "candidate_path": self.relative(candidate_path),
                "task_id": record.get("task_id"),
                "review_report": self.relative(report_path),
                "review": review,
            }
            candidate["accepted_by_policy"] = qc_candidate_accepted(candidate, strategy)
            rounds.append(candidate)
            record["quality_control"] = {
                "execution_signature": qc_signature,
                "status": "reviewing",
                "strategy": strategy,
                "max_rounds": max_rounds,
                "rounds": rounds,
            }
            self.state["segments"][str(index)] = record
            self.save_state()
            if candidate["accepted_by_policy"]:
                selected = candidate
                break

        fallback = selected is None
        if fallback:
            selected = min(rounds, key=qc_candidate_sort_key)
        selected_path = self.output_dir / selected["candidate_path"]
        raw_output = self.output_dir / "video_segments/raw" / f"segment_{index:03d}.mp4"
        shutil.copy2(selected_path, raw_output)
        record = self.state["segments"][str(index)]
        for key in ("normalized_output_path", "normalized_frame_count"):
            record.pop(key, None)
        record.update(
            {
                "status": "succeeded",
                "output_path": str(raw_output),
                "output_signature": record["execution_signature"],
                "quality_control": {
                    "execution_signature": qc_signature,
                    "status": "fallback_selected" if fallback else "accepted",
                    "strategy": strategy,
                    "max_rounds": max_rounds,
                    "rounds": rounds,
                    "selected_round": selected["round"],
                    "selected_candidate_path": selected["candidate_path"],
                    "selected_task_id": selected.get("task_id"),
                    "selected_review": selected["review"],
                    "all_rounds_rejected": fallback,
                },
            }
        )
        self.state["segments"][str(index)] = record
        self.save_state()
        return record["quality_control"]

    def run_quality_control(self, indexes=None):
        if not self.qc_config.get("enabled"):
            report = {"enabled": False, "status": "skipped", "segments": []}
            path = self.output_dir / "reports" / "shot_quality_control.json"
            atomic_json(path, report)
            return path, report
        segments = self.selected_segments(indexes)
        strategy = self.qc_config.get("strategy", "reject_major")
        if strategy not in {"accept_all", "reject_major"}:
            raise ValueError("quality_control.strategy must be accept_all or reject_major")
        max_rounds = int(self.qc_config.get("max_rounds", 3))
        if not 1 <= max_rounds <= 10:
            raise ValueError("quality_control.max_rounds must be between 1 and 10")
        workers = int(self.qc_config.get("workers", 8))
        if not 1 <= workers <= 64:
            raise ValueError("quality_control.workers must be between 1 and 64")
        launch_interval_sec = float(self.qc_config.get("launch_interval_sec", 1.5))
        if not 0 <= launch_interval_sec <= 60:
            raise ValueError("quality_control.launch_interval_sec must be between 0 and 60")
        max_review_attempts = int(self.qc_config.get("max_review_attempts", 5))
        if not 1 <= max_review_attempts <= 20:
            raise ValueError("quality_control.max_review_attempts must be between 1 and 20")

        contexts = {}
        results_by_index = {}
        for segment in segments:
            index = int(segment["index"])
            prompt = self.qc_prompt(segment)
            record = self.state["segments"][str(index)]
            signature_data = {
                "execution_signature": record.get("execution_signature"),
                "strategy": strategy,
                "max_rounds": max_rounds,
                "model": self.qc_config.get("model"),
                "base_url": self.qc_config.get("base_url"),
                "api_key_env": self.qc_config.get("api_key_env"),
                "temperature": self.qc_config.get("temperature"),
                "max_tokens": self.qc_config.get("max_tokens"),
                "fps": self.qc_config.get("fps"),
                "max_pixels": self.qc_config.get("max_pixels"),
                "prompt": prompt,
            }
            qc_signature = hashlib.sha256(
                json.dumps(signature_data, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            existing = record.get("quality_control", {})
            selected_existing = existing.get("selected_candidate_path")
            if (
                existing.get("execution_signature") == qc_signature
                and existing.get("status") in {"accepted", "fallback_selected"}
                and selected_existing
                and (self.output_dir / selected_existing).is_file()
            ):
                raw_output = self.output_dir / "video_segments/raw" / f"segment_{index:03d}.mp4"
                shutil.copy2(self.output_dir / selected_existing, raw_output)
                results_by_index[index] = existing
                continue

            reusable_rounds = (
                copy.deepcopy(existing.get("rounds", []))
                if existing.get("execution_signature") == qc_signature and existing.get("status") == "reviewing"
                else []
            )
            if not reusable_rounds:
                record.pop("quality_regeneration_count", None)
            record["quality_control"] = {
                "execution_signature": qc_signature,
                "status": "reviewing",
                "strategy": strategy,
                "max_rounds": max_rounds,
                "rounds": reusable_rounds,
            }
            self.state["segments"][str(index)] = record
            contexts[index] = {
                "segment": segment,
                "execution_signature": qc_signature,
                "rounds": reusable_rounds,
            }
        self.save_state()

        def finalize(index, selected, fallback):
            context = contexts[index]
            selected_path = self.output_dir / selected["candidate_path"]
            raw_output = self.output_dir / "video_segments/raw" / f"segment_{index:03d}.mp4"
            shutil.copy2(selected_path, raw_output)
            record = self.state["segments"][str(index)]
            for key in ("normalized_output_path", "normalized_frame_count"):
                record.pop(key, None)
            quality_control = {
                "execution_signature": context["execution_signature"],
                "status": "fallback_selected" if fallback else "accepted",
                "strategy": strategy,
                "max_rounds": max_rounds,
                "rounds": context["rounds"],
                "selected_round": selected["round"],
                "selected_candidate_path": selected["candidate_path"],
                "selected_task_id": selected.get("task_id"),
                "selected_review": selected["review"],
                "all_rounds_rejected": fallback,
            }
            record.update(
                {
                    "status": "succeeded",
                    "output_path": str(raw_output),
                    "output_signature": record["execution_signature"],
                    "quality_control": quality_control,
                }
            )
            self.state["segments"][str(index)] = record
            self.save_state()
            results_by_index[index] = quality_control
            contexts.pop(index)

        def review_with_retry(segment, candidate_path, round_index):
            last_error = None
            for attempt in range(1, max_review_attempts + 1):
                try:
                    return self.review_qc_candidate(segment, candidate_path, round_index)
                except Exception as exc:
                    last_error = exc
                    if attempt >= max_review_attempts:
                        break
                    time.sleep(min(60.0, 5.0 * (2 ** (attempt - 1))))
            raise RuntimeError(
                f"Omni quality review failed for segment {int(segment['index'])} "
                f"round {round_index} after {max_review_attempts} attempts: {last_error}"
            ) from last_error

        while contexts:
            for index in list(contexts):
                rounds = contexts[index]["rounds"]
                if rounds and rounds[-1].get("accepted_by_policy"):
                    finalize(index, rounds[-1], False)
                elif len(rounds) >= max_rounds:
                    finalize(index, min(rounds, key=qc_candidate_sort_key), True)
            if not contexts:
                break

            generation_indexes = []
            for index, context in contexts.items():
                record = self.state["segments"][str(index)]
                rounds = context["rounds"]
                regeneration_count = int(record.get("quality_regeneration_count", 0))
                if rounds and record.get("status") == "succeeded" and regeneration_count < len(rounds):
                    self.reset_segment_for_qc_regeneration(context["segment"])
                    record = self.state["segments"][str(index)]
                if record.get("status") != "succeeded":
                    generation_indexes.append(index)
            generation_executor = None
            generation_future = None
            if generation_indexes:
                # Keep unfinished generation moving while independent, already-downloaded candidates
                # enter Omni review. Regeneration still uses one shared rate-limited provider scheduler.
                generation_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                generation_future = generation_executor.submit(
                    self.run_videos, generation_indexes, quality_control=False
                )

            ready_indexes = [
                index for index in contexts if self.state["segments"][str(index)].get("status") == "succeeded"
            ]
            review_jobs = {}
            try:
                if ready_indexes:
                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=min(workers, len(ready_indexes))
                    ) as executor:
                        for position, index in enumerate(ready_indexes):
                            context = contexts[index]
                            record = self.state["segments"][str(index)]
                            raw_output = Path(
                                record.get("output_path")
                                or self.output_dir / "video_segments/raw" / f"segment_{index:03d}.mp4"
                            )
                            if not raw_output.is_file():
                                raise RuntimeError(
                                    f"segment {index} has no successful video candidate for quality control"
                                )
                            round_index = len(context["rounds"]) + 1
                            candidate_dir = self.output_dir / "video_segments/candidates" / f"segment_{index:03d}"
                            candidate_dir.mkdir(parents=True, exist_ok=True)
                            candidate_path = candidate_dir / f"round_{round_index:02d}.mp4"
                            shutil.copy2(raw_output, candidate_path)
                            future = executor.submit(
                                review_with_retry,
                                context["segment"],
                                candidate_path,
                                round_index,
                            )
                            review_jobs[future] = (index, round_index, candidate_path, record.get("task_id"))
                            if position + 1 < len(ready_indexes) and launch_interval_sec:
                                time.sleep(launch_interval_sec)

                        for future in concurrent.futures.as_completed(review_jobs):
                            index, round_index, candidate_path, task_id = review_jobs[future]
                            review, report_path = future.result()
                            candidate = {
                                "round": round_index,
                                "candidate_path": self.relative(candidate_path),
                                "task_id": task_id,
                                "review_report": self.relative(report_path),
                                "review": review,
                            }
                            candidate["accepted_by_policy"] = qc_candidate_accepted(candidate, strategy)
                            contexts[index]["rounds"].append(candidate)
                            record = self.state["segments"][str(index)]
                            record["quality_control"] = {
                                "execution_signature": contexts[index]["execution_signature"],
                                "status": "reviewing",
                                "strategy": strategy,
                                "max_rounds": max_rounds,
                                "rounds": contexts[index]["rounds"],
                            }
                            self.state["segments"][str(index)] = record
                            self.save_state()
            finally:
                if generation_future is not None:
                    generation_future.result()
                if generation_executor is not None:
                    generation_executor.shutdown()

        results = [
            {"segment_index": int(segment["index"]), **results_by_index[int(segment["index"])]} for segment in segments
        ]
        shot_counts = {rating: 0 for rating in QC_RATINGS}
        for result in results:
            for rating, count in result["selected_review"]["rating_counts"].items():
                shot_counts[rating] += count
        report = {
            "enabled": True,
            "status": "complete",
            "strategy": self.qc_config.get("strategy", "reject_major"),
            "max_rounds": int(self.qc_config.get("max_rounds", 3)),
            "shot_rating_counts": shot_counts,
            "fallback_segment_count": sum(1 for result in results if result["all_rounds_rejected"]),
            "segments": results,
        }
        path = self.output_dir / "reports" / "shot_quality_control.json"
        atomic_json(path, report)
        return path, report

    def status_summary(self):
        asset_state = self.state.get("assets", {})
        active_categories = ("scenes", "characters")
        legacy_categories = ("wardrobe", "keyframes")

        def summarize(records):
            return {
                "total": len(records),
                "succeeded_or_active": sum(
                    1 for record in records.values() if record.get("status") in {"succeeded", "Active"}
                ),
                "failed": sum(1 for record in records.values() if "failed" in str(record.get("status", "")).lower()),
            }

        return {
            "video_provider": self.state.get("video_provider", {}),
            "assets": {category: summarize(asset_state.get(category, {})) for category in active_categories},
            "legacy_unused_assets": {
                category: summarize(asset_state.get(category, {}))
                for category in legacy_categories
                if asset_state.get(category)
            },
            "segments": {
                "total": len(self.state.get("segments", {})),
                "succeeded": sum(
                    1 for record in self.state.get("segments", {}).values() if record.get("status") == "succeeded"
                ),
                "running": sum(
                    1
                    for record in self.state.get("segments", {}).values()
                    if record.get("status") in {"submitted", "running"}
                ),
                "failed": sum(
                    1
                    for record in self.state.get("segments", {}).values()
                    if record.get("status") in {"submit_failed", "failed", "download_failed"}
                ),
            },
            "quality_control": {
                "enabled": bool(self.qc_config.get("enabled")),
                "accepted": sum(
                    1
                    for record in self.state.get("segments", {}).values()
                    if (record.get("quality_control") or {}).get("status") == "accepted"
                ),
                "fallback_selected": sum(
                    1
                    for record in self.state.get("segments", {}).values()
                    if (record.get("quality_control") or {}).get("status") == "fallback_selected"
                ),
                "pending": sum(
                    1
                    for record in self.state.get("segments", {}).values()
                    if self.qc_config.get("enabled")
                    and (record.get("quality_control") or {}).get("status") not in {"accepted", "fallback_selected"}
                ),
            },
            "assembly": self.state.get("assembly", {}),
        }

    def normalize_segment(self, segment):
        index = int(segment["index"])
        record = self.state["segments"][str(index)]
        output = self.output_dir / "video_segments/normalized" / f"segment_{index:03d}.mp4"
        fps = int(self.assembly_config["fps"])
        start_frame = round(float(segment["start_sec"]) * fps)
        end_frame = round((float(segment["start_sec"]) + float(segment["duration_sec"])) * fps)
        frame_count = max(1, end_frame - start_frame)
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=duration",
                "-of",
                "json",
                record["output_path"],
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            source_duration = float(json.loads(probe.stdout)["streams"][0]["duration"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError(f"Cannot determine video duration for segment {index}") from exc
        if not math.isfinite(source_duration) or source_duration <= 0:
            raise ValueError(f"Invalid video duration for segment {index}: {source_duration}")
        # Slow short video across its whole duration; longer video keeps its speed and is trimmed.
        # Use the video stream's duration, since provider audio may extend the container duration.
        time_scale = max(1.0, (frame_count / fps) / source_duration)
        width = int(self.assembly_config["width"])
        height = int(self.assembly_config["height"])
        filter_graph = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            f"setpts={time_scale:.12f}*(PTS-STARTPTS),"
            f"fps={fps}:eof_action=pass,trim=end_frame={frame_count},setpts=PTS-STARTPTS"
        )
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                record["output_path"],
                "-an",
                "-vf",
                filter_graph,
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                str(self.assembly_config["crf"]),
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(fps),
                "-frames:v",
                str(frame_count),
                str(output),
            ],
            check=True,
        )
        record.update({"normalized_output_path": str(output), "normalized_frame_count": frame_count})
        self.state["segments"][str(index)] = record
        self.save_state()
        return output

    def project_root(self):
        if self.storyboard_path.parent.name == "authoring":
            return self.storyboard_path.parent.parent
        return self.output_dir.parent

    def resolve_project_path(self, value):
        path = Path(value).expanduser()
        if path.is_absolute():
            return path.resolve()
        candidates = [
            self.project_root() / path,
            self.storyboard_path.parent / path,
            Path.cwd() / path,
        ]
        return next((candidate.resolve() for candidate in candidates if candidate.exists()), candidates[0].resolve())

    def materialize_lyrics_srt(self):
        if not self.subtitle_config.get("enabled", True):
            return {"status": "disabled", "srt_path": None}

        project_root = self.project_root()
        manifest_path = project_root / "music2mv-manifest.json"
        manifest = load_json(manifest_path) if manifest_path.is_file() else {}
        artifacts = manifest.get("artifacts", {}) if isinstance(manifest, dict) else {}
        explicit_srt = self.lyrics_srt_override or (
            self.resolve_project_path(self.subtitle_config["source_srt"])
            if self.subtitle_config.get("source_srt")
            else None
        )
        srt_candidates = [
            explicit_srt,
            self.resolve_project_path(artifacts["lyrics_srt"]) if artifacts.get("lyrics_srt") else None,
            project_root / "analysis/music-caption/lyrics.srt",
        ]
        source_srt = next((path for path in srt_candidates if path and path.is_file()), None)
        canonical_srt = self.output_dir / "subtitles/lyrics.srt"
        if source_srt:
            if source_srt.resolve() != canonical_srt.resolve():
                shutil.copy2(source_srt, canonical_srt)
            return {
                "status": "ready",
                "source_kind": "srt",
                "source_path": str(source_srt),
                "srt_path": str(canonical_srt),
            }
        if explicit_srt:
            raise FileNotFoundError(f"Configured lyrics SRT does not exist: {explicit_srt}")

        explicit_evidence = (
            self.resolve_project_path(self.subtitle_config["source_evidence"])
            if self.subtitle_config.get("source_evidence")
            else None
        )
        evidence_candidates = [
            explicit_evidence,
            self.resolve_project_path(artifacts["music_caption_evidence"])
            if artifacts.get("music_caption_evidence")
            else None,
            project_root / "analysis/music-caption/evidence.json",
        ]
        evidence_path = next((path for path in evidence_candidates if path and path.is_file()), None)
        if not evidence_path:
            if explicit_evidence:
                raise FileNotFoundError(f"Configured music-caption evidence does not exist: {explicit_evidence}")
            raise RuntimeError(
                "Subtitles are enabled, but no sentence-level lyrics SRT or music-caption evidence was found. "
                "Pass --lyrics-srt, configure subtitles.source_srt/source_evidence, or set subtitles.enabled=false."
            )
        evidence = load_json(evidence_path)
        sentence_lyrics = evidence.get("sentence_lyrics") if isinstance(evidence, dict) else None
        if not isinstance(sentence_lyrics, dict):
            raise ValueError(f"music-caption evidence has no sentence_lyrics object: {evidence_path}")
        lyric_status = sentence_lyrics.get("status")
        cues = sentence_lyrics.get("cues")
        if lyric_status == "no_lyrics" and cues == []:
            return {
                "status": "no_lyrics",
                "source_kind": "music_caption_evidence",
                "source_path": str(evidence_path),
                "srt_path": None,
            }
        if lyric_status != "ok" or not isinstance(cues, list) or not cues:
            raise ValueError(f"invalid sentence_lyrics in music-caption evidence: {evidence_path}")
        blocks = []
        for expected_index, cue in enumerate(cues, start=1):
            if not isinstance(cue, dict) or cue.get("index") != expected_index:
                raise ValueError(f"sentence_lyrics cue index mismatch at {expected_index}")
            start = cue.get("start_timestamp")
            end = cue.get("end_timestamp")
            lines = cue.get("text_lines")
            if not isinstance(start, str) or not isinstance(end, str) or not isinstance(lines, list) or not lines:
                raise ValueError(f"sentence_lyrics cue {expected_index} is incomplete")
            blocks.append(f"{expected_index}\n{start} --> {end}\n" + "\n".join(str(line) for line in lines))
        canonical_srt.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        return {
            "status": "ready",
            "source_kind": "music_caption_evidence",
            "source_path": str(evidence_path),
            "srt_path": str(canonical_srt),
            "cue_count": len(cues),
        }

    def render_final_subtitles(self, master, final):
        source = self.materialize_lyrics_srt()
        report_path = self.output_dir / "reports/subtitle_render.json"
        if source["status"] in {"disabled", "no_lyrics"}:
            shutil.copy2(master, final)
            report = {
                "status": source["status"],
                "output_path": str(final),
                **{key: value for key, value in source.items() if key != "status"},
            }
            atomic_json(report_path, report)
            return report

        script = VIDEO_WORKFLOW_DIR / "scripts/burn_lyrics_subtitles.py"
        font = (
            self.resolve_project_path(self.subtitle_config["font"])
            if self.subtitle_config.get("font")
            else VIDEO_WORKFLOW_DIR / "assets/fonts/NotoSansCJKsc-Bold.otf"
        )
        ass_path = self.output_dir / "subtitles/lyrics.ass"
        command = [
            sys.executable,
            str(script),
            "--video",
            str(master),
            "--srt",
            source["srt_path"],
            "--output",
            str(final),
            "--ass-output",
            str(ass_path),
            "--font",
            str(font),
            "--preset",
            str(self.subtitle_config.get("preset", "slow")),
            "--crf",
            str(int(self.subtitle_config.get("crf", 18))),
            "--overwrite",
        ]
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        report = json.loads(completed.stdout)
        report.update(
            {
                "status": "burned",
                "source_kind": source.get("source_kind"),
                "source_path": source.get("source_path"),
                "report_path": self.relative(report_path),
            }
        )
        atomic_json(report_path, report)
        return report

    def assemble(self):
        segments = self.selected_segments()
        missing = [
            int(segment["index"])
            for segment in segments
            if self.state["segments"].get(str(int(segment["index"])), {}).get("status") != "succeeded"
        ]
        if missing:
            raise RuntimeError(f"Cannot assemble; missing successful native segment tasks: {missing}")
        if self.qc_config.get("enabled"):
            qc_incomplete = [
                int(segment["index"])
                for segment in segments
                if (self.state["segments"][str(int(segment["index"]))].get("quality_control") or {}).get("status")
                not in {"accepted", "fallback_selected"}
            ]
            if qc_incomplete:
                raise RuntimeError(
                    f"Cannot assemble; semantic quality control is incomplete for segments: {qc_incomplete}"
                )
        normalized = [self.normalize_segment(segment) for segment in segments]
        silent = self.output_dir / "video_segments" / "assembled_silent.mp4"
        fps = int(self.assembly_config["fps"])
        frame_counts = [
            int(self.state["segments"][str(int(segment["index"]))]["normalized_frame_count"]) for segment in segments
        ]
        total_frames = sum(frame_counts)
        blend_frames = max(
            0,
            int(self.assembly_config.get("boundary_blend_frames", DEFAULT_CONFIG["assembly"]["boundary_blend_frames"])),
        )
        if blend_frames and len(normalized) > 1:
            blend_frames = min(blend_frames, min(frame_counts) - 1)
            blend_duration = blend_frames / fps
            command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
            for path in normalized:
                command.extend(["-i", str(path)])
            filters = []
            current = "[0:v]"
            cumulative_frames = frame_counts[0]
            for index in range(1, len(normalized)):
                padded = f"[p{index}]"
                output = f"[x{index}]"
                offset = (cumulative_frames - blend_frames) / fps
                filters.append(f"[{index}:v]tpad=start_mode=clone:start={blend_frames + 1}{padded}")
                filters.append(
                    f"{current}{padded}xfade=transition=fade:duration={blend_duration:.9f}:offset={offset:.9f}{output}"
                )
                current = output
                cumulative_frames += frame_counts[index]
            filters.append(
                f"{current}fps={fps},tpad=stop_mode=clone:stop=2,"
                f"trim=end_frame={total_frames},setpts=PTS-STARTPTS[outv]"
            )
            command.extend(
                [
                    "-filter_complex",
                    ";".join(filters),
                    "-map",
                    "[outv]",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    str(self.assembly_config["crf"]),
                    "-pix_fmt",
                    "yuv420p",
                    "-r",
                    str(fps),
                    "-frames:v",
                    str(total_frames),
                    str(silent),
                ]
            )
            subprocess.run(command, check=True)
        else:
            concat_path = self.output_dir / "video_segments" / "concat.txt"
            with open(concat_path, "w", encoding="utf-8") as handle:
                for path in normalized:
                    handle.write(f"file '{path}'\n")
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_path),
                    "-c",
                    "copy",
                    str(silent),
                ],
                check=True,
            )
        master = self.output_dir / "video_segments/assembled_with_audio.mp4"
        duration = float(self.board.get("video", {}).get("duration_sec", segments[-1]["end_sec"]))
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(silent),
                "-i",
                str(self.audio_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                self.assembly_config["audio_bitrate"],
                "-t",
                str(duration),
                "-movflags",
                "+faststart",
                str(master),
            ],
            check=True,
        )
        final = self.output_dir / self.assembly_config["output_name"]
        if final.resolve() == master.resolve():
            raise ValueError("assembly.output_name must differ from video_segments/assembled_with_audio.mp4")
        subtitle_report = self.render_final_subtitles(master, final)
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_frames",
                "-show_entries",
                "format=duration,size",
                "-show_entries",
                "stream=codec_name,width,height,r_frame_rate,nb_read_frames",
                "-of",
                "json",
                str(final),
            ],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.state["assembly"] = {
            "status": "succeeded",
            "output_path": str(final),
            "master_without_subtitles": str(master),
            "subtitles": subtitle_report,
            "segment_providers": {
                str(int(segment["index"])): self.state["segments"][str(int(segment["index"]))].get("provider")
                for segment in segments
            },
            "timeline_duration_sec": duration,
            "boundary_blend_frames": blend_frames,
            "expected_frame_count": total_frames,
            "probe": json.loads(probe.stdout),
            "completed_at": timestamp(),
        }
        self.save_state()
        return final

    def verify(self):
        assembly = self.state.get("assembly", {})
        final = Path(assembly.get("output_path", ""))
        if assembly.get("status") != "succeeded" or not final.exists():
            raise RuntimeError("No assembled final MV is available for verification")
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_frames",
                "-show_entries",
                "format=duration,size,bit_rate",
                "-show_entries",
                "stream=index,codec_type,codec_name,width,height,r_frame_rate,nb_read_frames,sample_rate,channels,duration",
                "-of",
                "json",
                str(final),
            ],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
        probe_data = json.loads(probe.stdout)
        video_stream = next(stream for stream in probe_data["streams"] if stream["codec_type"] == "video")
        audio_stream = next(stream for stream in probe_data["streams"] if stream["codec_type"] == "audio")
        duration = float(self.board.get("video", {}).get("duration_sec", 0))
        fps = int(self.assembly_config["fps"])
        expected_frames = round(duration * fps)
        successful_segments = sum(
            1
            for segment in self.selected_segments()
            if self.state["segments"].get(str(int(segment["index"])), {}).get("status") == "succeeded"
        )
        normalized_frames = sum(
            int(self.state["segments"].get(str(int(segment["index"])), {}).get("normalized_frame_count", 0))
            for segment in self.selected_segments()
        )
        black_log = self.output_dir / "reports" / "blackdetect.log"
        silence_log = self.output_dir / "reports" / "silencedetect.log"
        with open(black_log, "w", encoding="utf-8") as handle:
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostats",
                    "-i",
                    str(final),
                    "-vf",
                    "blackdetect=d=0.4:pix_th=0.02",
                    "-an",
                    "-f",
                    "null",
                    "-",
                ],
                stdout=subprocess.DEVNULL,
                stderr=handle,
                check=False,
            )
        with open(silence_log, "w", encoding="utf-8") as handle:
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostats",
                    "-i",
                    str(final),
                    "-af",
                    "silencedetect=n=-50dB:d=1.0",
                    "-vn",
                    "-f",
                    "null",
                    "-",
                ],
                stdout=subprocess.DEVNULL,
                stderr=handle,
                check=False,
            )
        black_intervals = [line for line in black_log.read_text(errors="ignore").splitlines() if "black_start:" in line]
        silence_events = [
            line
            for line in silence_log.read_text(errors="ignore").splitlines()
            if "silence_start:" in line or "silence_end:" in line
        ]
        passed = (
            successful_segments == len(self.segment_map)
            and normalized_frames == expected_frames
            and video_stream.get("nb_read_frames") == str(expected_frames)
            and video_stream.get("width") == int(self.assembly_config["width"])
            and video_stream.get("height") == int(self.assembly_config["height"])
        )
        semantic_statuses = [
            (self.state["segments"].get(str(int(segment["index"])), {}).get("quality_control") or {}).get("status")
            for segment in self.selected_segments()
        ]
        semantic_complete = bool(self.qc_config.get("enabled")) and all(
            status in {"accepted", "fallback_selected"} for status in semantic_statuses
        )
        subtitle_state = assembly.get("subtitles", {})
        report = {
            "status": "passed" if passed else "failed",
            "generation_mode": "one_editorial_shot_per_request",
            "qc_scope": "technical_only",
            "semantic_quality_control": {
                "enabled": bool(self.qc_config.get("enabled")),
                "status": (
                    "complete" if semantic_complete else ("incomplete" if self.qc_config.get("enabled") else "skipped")
                ),
                "report": "reports/shot_quality_control.json",
            },
            "semantic_visual_review_required": bool(self.qc_config.get("enabled")) and not semantic_complete,
            "subtitles": {
                "enabled": bool(self.subtitle_config.get("enabled", True)),
                "status": subtitle_state.get("status", "missing"),
                "srt_path": subtitle_state.get("srt_path"),
                "ass_path": subtitle_state.get("ass_path"),
                "cue_count": subtitle_state.get("cue_count", 0),
                "render_report": "reports/subtitle_render.json",
            },
            "not_automatically_verified": [
                "identity fidelity",
                "exact character count",
                "anatomy",
                "acting and choreography",
                "reference switching",
                "lip sync",
                "subtitle visual readability",
            ],
            "successful_segments": successful_segments,
            "segment_count": len(self.segment_map),
            "timeline_target_sec": duration,
            "expected_frame_count": expected_frames,
            "normalized_frame_count_sum": normalized_frames,
            "final_video": video_stream,
            "final_audio": audio_stream,
            "format": probe_data["format"],
            "black_intervals_over_0_4_sec": black_intervals,
            "silence_events_over_1_sec": silence_events,
            "blackdetect_log": self.relative(black_log),
            "silencedetect_log": self.relative(silence_log),
        }
        report_path = self.output_dir / "reports" / "final_qc.json"
        atomic_json(report_path, report)
        return report_path, report


def parse_indexes(value):
    if not value:
        return None
    return [int(item) for item in value.split(",")]


def main():
    parser = argparse.ArgumentParser(description="Execute an identity-conditioned live-action MV pipeline")
    parser.add_argument(
        "phase",
        choices=[
            "validate",
            "plan",
            "base-assets",
            "images",
            "keyframes",
            "prepare-videos",
            "videos",
            "quality-control",
            "assemble",
            "verify",
            "status",
            "all",
        ],
    )
    parser.add_argument("--storyboard", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config")
    parser.add_argument(
        "--model-config",
        help="Unified Omni/image/video connection JSON; defaults to QWEN_MM_OMNI_CHATCUT_MODEL_CONFIG",
    )
    parser.add_argument("--lyrics-srt", help="Accepted sentence-level lyrics SRT; overrides auto-discovery")
    parser.add_argument("--segments", help="Comma-separated segment indexes")
    args = parser.parse_args()
    pipeline = Pipeline(
        args.storyboard,
        args.audio,
        args.output,
        config_path=args.config,
        lyrics_srt=args.lyrics_srt,
        model_config_path=args.model_config,
    )
    indexes = parse_indexes(args.segments)
    if args.phase == "validate":
        print(pipeline.validate())
        return
    if args.phase == "plan":
        print(json.dumps(pipeline.plan(indexes), ensure_ascii=False, indent=2))
        return
    if args.phase == "base-assets":
        pipeline.generate_base_assets(indexes)
        return
    if args.phase == "images":
        pipeline.generate_base_assets(indexes)
        return
    if args.phase == "keyframes":
        print(pipeline.generate_keyframes(indexes))
        return
    if args.phase == "prepare-videos":
        print(pipeline.prepare_video_packages(indexes))
        return
    if args.phase == "videos":
        pipeline.run_videos(indexes)
        return
    if args.phase == "quality-control":
        report_path, report = pipeline.run_quality_control(indexes)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(report_path)
        return
    if args.phase == "assemble":
        print(pipeline.assemble())
        return
    if args.phase == "verify":
        report_path, report = pipeline.verify()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(report_path)
        return
    if args.phase == "status":
        print(json.dumps(pipeline.status_summary(), ensure_ascii=False, indent=2))
        return
    pipeline.validate()
    pipeline.generate_base_assets(indexes)
    pipeline.run_videos(indexes)
    print(pipeline.assemble())
    report_path, report = pipeline.verify()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(report_path)


if __name__ == "__main__":
    main()
