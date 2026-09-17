"""Atomic manifest, checkpoint, resume, and offline status handling."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from .schemas import StrictSchema, safe_relative_path

MANIFEST_SCHEMA = 1
MANIFEST_OWNER = "omni-video2note"
MANIFEST_NAME = "manifest.json"
PIPELINE_STATUSES = (
    "new",
    "understanding",
    "planning",
    "writing_selection",
    "render_review_repair",
    "pass",
    "best_effort",
    "failed",
)
TERMINAL_STATUSES = frozenset({"pass", "best_effort", "failed"})
_TRANSITIONS = {
    "new": frozenset({"understanding", "failed"}),
    "understanding": frozenset({"planning", "failed"}),
    "planning": frozenset({"writing_selection", "failed"}),
    "writing_selection": frozenset({"render_review_repair", "failed"}),
    "render_review_repair": frozenset({"render_review_repair", "pass", "best_effort", "failed"}),
    "pass": frozenset(),
    "best_effort": frozenset(),
    "failed": frozenset(),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_sha256(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size < 1:
        raise ValueError("chunk_size must be a positive integer")
    source = Path(path)
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, StrictSchema):
        return value.to_dict()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def atomic_write_json(path: str | Path, value: Any) -> None:
    """Write UTF-8 JSON via fsync and atomic replace."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.{uuid4().hex}.tmp")
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default) + "\n"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_json(path: str | Path) -> Any:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"manifest schema must be {MANIFEST_SCHEMA}")
    if manifest.get("owner") != MANIFEST_OWNER:
        raise ValueError("workdir is not owned by omni-video2note")
    if manifest.get("status") not in PIPELINE_STATUSES:
        raise ValueError("manifest has an invalid pipeline status")
    input_record = manifest.get("input")
    if not isinstance(input_record, dict) or not isinstance(input_record.get("sha256"), str):
        raise ValueError("manifest input hash is missing")
    fingerprint = manifest.get("config_fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise ValueError("manifest config fingerprint is invalid")
    checkpoints = manifest.get("checkpoints")
    if not isinstance(checkpoints, dict):
        raise ValueError("manifest checkpoints must be an object")
    for name, record in checkpoints.items():
        if not isinstance(name, str) or not isinstance(record, dict):
            raise ValueError("manifest checkpoint is invalid")
        if record.get("status") not in {"running", "complete", "failed"}:
            raise ValueError(f"checkpoint {name!r} has an invalid status")
        artifacts = record.get("artifacts", {})
        if not isinstance(artifacts, dict):
            raise ValueError(f"checkpoint {name!r} artifacts must be an object")
        for key, value in artifacts.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError(f"checkpoint {name!r} artifact entries must be strings")
            safe_relative_path(value)


OVERWRITE_ARTIFACT_ROOTS = ("phase1", "phase2", "phase3", "phase4", "frames")


def _manifest_artifact_roots(manifest: dict[str, Any]) -> set[str]:
    roots: set[str] = set()
    for record in manifest.get("checkpoints", {}).values():
        for value in record.get("artifacts", {}).values():
            roots.add(PurePosixPath(safe_relative_path(str(value))).parts[0])
    return roots


def _remove_manifest_artifacts(root: Path, manifest: dict[str, Any]) -> None:
    """Clear the capability's own fixed artifact directories for an explicit overwrite.

    Only the known top-level phase/frame directories are removable; a manifest that points anywhere
    else is refused rather than trusted to name what may be deleted.
    """
    _validate_manifest(manifest)
    unexpected = sorted(_manifest_artifact_roots(manifest) - set(OVERWRITE_ARTIFACT_ROOTS))
    if unexpected:
        raise ValueError(f"refusing to overwrite unrecognized manifest artifact paths: {', '.join(unexpected)}")
    for name in OVERWRITE_ARTIFACT_ROOTS:
        target = root / name
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)


class PipelineState:
    """Own and mutate one schema-1 pipeline manifest."""

    def __init__(self, workdir: str | Path, manifest: dict[str, Any]):
        self.workdir = Path(workdir).expanduser().resolve()
        self.path = self.workdir / MANIFEST_NAME
        _validate_manifest(manifest)
        self.manifest = manifest

    @classmethod
    def create(
        cls,
        workdir: str | Path,
        input_path: str | Path,
        config_fingerprint: str,
        *,
        overwrite: bool = False,
    ) -> PipelineState:
        root = Path(workdir).expanduser().resolve()
        source = Path(input_path).expanduser().resolve()
        manifest_path = root / MANIFEST_NAME
        if root.exists() and not root.is_dir():
            raise ValueError("workdir must be a directory")
        if manifest_path.exists():
            existing = load_json(manifest_path)
            _validate_manifest(existing)
            if not overwrite:
                raise FileExistsError("owned workdir already has a manifest; use resume or overwrite")
            _remove_manifest_artifacts(root, existing)
        elif root.exists() and any(root.iterdir()):
            raise RuntimeError("refusing to initialize a non-empty unowned workdir")
        root.mkdir(parents=True, exist_ok=True)
        now = utc_now()
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "owner": MANIFEST_OWNER,
            "status": "new",
            "created_at": now,
            "updated_at": now,
            "input": {
                "name": source.name,
                "path": str(source),
                "size_bytes": source.stat().st_size,
                "sha256": file_sha256(source),
            },
            "config_fingerprint": config_fingerprint,
            "checkpoints": {},
            "error": "",
        }
        state = cls(root, manifest)
        state.save()
        return state

    @classmethod
    def resume(
        cls,
        workdir: str | Path,
        input_path: str | Path,
        config_fingerprint: str,
    ) -> PipelineState:
        root = Path(workdir).expanduser().resolve()
        path = root / MANIFEST_NAME
        if not path.is_file():
            raise FileNotFoundError("resume requires an existing omni-video2note manifest")
        state = cls(root, load_json(path))
        state.validate_resume(input_path, config_fingerprint)
        if state.status == "failed":
            checkpoints = state.manifest.get("checkpoints", {})
            if checkpoints.get("phase3_writing_selection", {}).get("status") == "complete":
                recovered_status = "render_review_repair"
            elif checkpoints.get("phase2_planning", {}).get("status") == "complete":
                recovered_status = "writing_selection"
            elif checkpoints.get("phase1_understanding", {}).get("status") == "complete":
                recovered_status = "planning"
            else:
                recovered_status = "new"
            state.manifest["status"] = recovered_status
            state.manifest["error"] = ""
            state.save()
        return state

    @classmethod
    def load(cls, workdir: str | Path) -> PipelineState:
        root = Path(workdir).expanduser().resolve()
        path = root / MANIFEST_NAME
        if not path.is_file():
            raise FileNotFoundError("omni-video2note manifest not found")
        return cls(root, load_json(path))

    def save(self) -> None:
        self.manifest["updated_at"] = utc_now()
        _validate_manifest(self.manifest)
        atomic_write_json(self.path, self.manifest)

    def validate_resume(self, input_path: str | Path, config_fingerprint: str) -> None:
        source = Path(input_path).expanduser().resolve()
        if self.manifest["owner"] != MANIFEST_OWNER:
            raise RuntimeError("resume owner does not match omni-video2note")
        if file_sha256(source) != self.manifest["input"]["sha256"]:
            raise RuntimeError("resume input hash does not match manifest")
        if config_fingerprint != self.manifest["config_fingerprint"]:
            raise RuntimeError("resume configuration fingerprint does not match manifest")

    @property
    def status(self) -> str:
        return str(self.manifest["status"])

    def transition(self, status: str, *, error: str = "") -> None:
        if status not in PIPELINE_STATUSES:
            raise ValueError(f"unknown pipeline status: {status}")
        current = self.status
        if status != current and status not in _TRANSITIONS[current]:
            raise ValueError(f"invalid pipeline status transition: {current} -> {status}")
        self.manifest["status"] = status
        self.manifest["error"] = error if status == "failed" else ""
        self.save()

    def checkpoint(
        self,
        name: str,
        status: str,
        *,
        artifacts: dict[str, str | Path] | None = None,
        data: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        if not name.strip():
            raise ValueError("checkpoint name is required")
        if status not in {"running", "complete", "failed"}:
            raise ValueError("checkpoint status must be running, complete, or failed")
        normalized: dict[str, str] = {}
        for key, value in (artifacts or {}).items():
            relative = safe_relative_path(str(value))
            target = self.artifact_path(relative)
            if status == "complete" and not target.exists():
                raise FileNotFoundError(f"completed checkpoint artifact is missing: {relative}")
            normalized[str(key)] = relative
        previous = self.manifest["checkpoints"].get(name, {})
        started_at = previous.get("started_at") or utc_now()
        self.manifest["checkpoints"][name] = {
            "status": status,
            "started_at": started_at,
            "updated_at": utc_now(),
            "artifacts": normalized,
            "data": data or {},
            "error": error,
        }
        self.save()

    def is_checkpoint_complete(self, name: str, *, verify_artifacts: bool = True) -> bool:
        record = self.manifest["checkpoints"].get(name, {})
        if record.get("status") != "complete":
            return False
        if not verify_artifacts:
            return True
        return all(self.artifact_path(value).exists() for value in record.get("artifacts", {}).values())

    def artifact_path(self, relative: str | Path) -> Path:
        normalized = safe_relative_path(str(relative))
        root = self.workdir.resolve()
        result = (root / normalized).resolve()
        if root not in result.parents:
            raise ValueError(f"artifact escapes workdir: {relative}")
        return result

    def relative_artifact(self, path: str | Path) -> str:
        target = Path(path).resolve()
        try:
            return safe_relative_path(target.relative_to(self.workdir).as_posix())
        except ValueError as exc:
            raise ValueError(f"artifact is outside workdir: {path}") from exc


def offline_status(workdir: str | Path) -> dict[str, Any]:
    """Read resumability and artifact health without hashing input or accessing a network."""
    try:
        state = PipelineState.load(workdir)
    except Exception as exc:  # noqa: BLE001 - status API returns structured local failures
        return {
            "exit_code": 1,
            "status": "failed",
            "passed": False,
            "workdir": str(Path(workdir).expanduser().resolve()),
            "resumable": False,
            "final_pdf": None,
            "best_iteration": None,
            "best_score": 0.0,
            "iterations": 0,
            "error": str(exc),
        }
    checkpoints = {}
    for name, record in state.manifest["checkpoints"].items():
        try:
            artifacts_present = all(
                state.artifact_path(path).exists() for path in record.get("artifacts", {}).values()
            )
        except (TypeError, ValueError):
            artifacts_present = False
        complete = record.get("status") == "complete" and artifacts_present
        checkpoints[name] = {
            "status": record.get("status"),
            "complete": complete,
            "artifacts_present": artifacts_present,
            "error": record.get("error", ""),
        }
    status = state.status
    result = {
        "exit_code": 0 if status == "pass" else 2 if status == "best_effort" else 1,
        "status": status,
        "passed": status == "pass",
        "workdir": str(state.workdir),
        "resumable": status not in {"pass", "best_effort"},
        "input": dict(state.manifest["input"]),
        "checkpoints": checkpoints,
        "final_pdf": None,
        "best_iteration": None,
        "best_score": 0.0,
        "iterations": 0,
        "error": state.manifest.get("error", ""),
    }
    final_relative = (
        state.manifest.get("checkpoints", {})
        .get("phase4_render_review_repair", {})
        .get("artifacts", {})
        .get("final_report")
    )
    if isinstance(final_relative, str):
        try:
            final = load_json(state.artifact_path(final_relative))
            if isinstance(final, dict):
                result["passed"] = bool(final.get("passed", result["passed"]))
                result["best_iteration"] = final.get("best_iteration")
                result["best_score"] = final.get("best_score", 0.0)
                result["iterations"] = len(final.get("iterations", []))
                result["error"] = final.get("error", result["error"])
                relative_pdf = final.get("final_pdf")
                if isinstance(relative_pdf, str):
                    result["final_pdf"] = str(state.artifact_path(relative_pdf))
        except Exception as exc:  # malformed terminal details should not hide the manifest status
            result["error"] = result["error"] or f"cannot read final report: {exc}"
    return result


read_status = offline_status
checkpoint_complete = PipelineState.is_checkpoint_complete
