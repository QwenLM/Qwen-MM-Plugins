"""Offline unit coverage for the Omni Video2Note pipeline."""

from __future__ import annotations

import importlib
import json
import re
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CAPABILITY_DIR = ROOT / "src" / "capabilities" / "omni-video2note"  # re-exported for the stdio test
PACKAGE = "qwen_mm_plugins_omni_video2note"


def import_capability_module(relative: str):
    """Import one module out of the capability package.

    The real package initializer runs, so ``sys.modules`` keeps the registry other tests expect.
    The extra requires Pydantic 2.11+, which mcp_framework's ``union_format`` schema argument needs.
    """
    return importlib.import_module(f"{PACKAGE}.{relative}")


def import_pipeline_module(name: str):
    """Import one ``pipeline`` implementation module."""
    return import_capability_module(f"pipeline.{name}")


def _video_encoder() -> tuple[str, list[str]]:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe are required")
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        check=True,
    )
    listing = result.stdout + result.stderr
    for name, options in (
        ("libx264", ["-preset", "ultrafast", "-crf", "28"]),
        ("libopenh264", ["-b:v", "500k"]),
        ("mpeg4", ["-q:v", "5"]),
    ):
        if re.search(rf"\b{re.escape(name)}\b", listing):
            return name, options
    pytest.skip("ffmpeg has none of libx264, libopenh264, or mpeg4")


def make_short_av_video(path: Path, *, duration: float = 1.8) -> Path:
    """Generate a small deterministic A/V fixture with an encoder available locally."""
    encoder, options = _video_encoder()
    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=duration={duration}:size=640x360:rate=8",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate=16000:duration={duration}",
        "-c:v",
        encoder,
        *options,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"ffmpeg cannot generate the A/V fixture with {encoder}: {result.stderr[-300:]}")
    return path


@pytest.fixture
def short_av_video(tmp_path: Path) -> Path:
    return make_short_av_video(tmp_path / "short-av.mp4")


def test_pipeline_config_and_dry_run_do_not_create_workdir(
    short_av_video: Path, tmp_path: Path, monkeypatch
):
    config_module = import_pipeline_module("config")
    runner = import_pipeline_module("runner")
    output = tmp_path / "notes.pdf"
    workdir = output.with_suffix(".pdf.work")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dry-run-secret")

    config = config_module.PipelineConfig(video_path=short_av_video, output_path=output, dry_run=True)
    assert config.workdir == workdir
    assert config.profile.name == "balanced"
    assert not workdir.exists()

    result = runner.run_video2note(video_path=short_av_video, output_path=output, dry_run=True)
    serialized = json.dumps(result, ensure_ascii=False)
    assert result["exit_code"] == 0
    assert result["status"] == "dry_run"
    assert "api_key" not in serialized
    assert "dry-run-secret" not in serialized
    assert len(result["phases"]) == 4
    assert not output.exists()
    assert not workdir.exists()


def test_strict_schemas_validate_nested_data_and_paths():
    schemas = import_pipeline_module("schemas")
    plan = schemas.DocumentPlan.parse(
        {
            "title": "Short tutorial",
            "audience": "beginners",
            "overview_goal": "Complete one task",
            "steps": [
                {
                    "id": 1,
                    "title": "Open the panel",
                    "objective": "Find the control",
                    "start": 0,
                    "end": 1.5,
                    "visual_targets": [{"id": "panel", "role": "primary", "query": "open panel"}],
                }
            ],
        }
    )
    assert plan.to_dict()["steps"][0]["visual_targets"][0]["id"] == "panel"
    with pytest.raises(ValueError, match="unknown DocumentPlan field"):
        schemas.DocumentPlan.parse(plan.to_dict() | {"unexpected": True})
    with pytest.raises(ValueError, match="unsafe"):
        schemas.safe_relative_path("../outside.jpg")
    with pytest.raises(ValueError, match="continuous"):
        schemas.DocumentPlan.parse(plan.to_dict() | {"steps": [plan.steps[0].to_dict() | {"id": 2}]})


def test_state_checkpoint_resume_hash_conflict_and_status(short_av_video: Path, tmp_path: Path):
    state_module = import_pipeline_module("state")
    workdir = tmp_path / "job"
    state = state_module.PipelineState.create(workdir, short_av_video, "a" * 64)
    state.transition("understanding")
    artifact = workdir / "phase1" / "probe.json"
    artifact.parent.mkdir()
    artifact.write_text("{}\n", encoding="utf-8")
    state.checkpoint("phase1_understanding", "complete", artifacts={"probe": "phase1/probe.json"})

    status = state_module.offline_status(workdir)
    assert status["status"] == "understanding"
    assert status["resumable"] is True
    assert status["checkpoints"]["phase1_understanding"]["complete"] is True

    state.transition("failed", error="interrupted")
    assert state_module.offline_status(workdir)["resumable"] is True
    resumed = state_module.PipelineState.resume(workdir, short_av_video, "a" * 64)
    assert resumed.status == "planning"
    assert resumed.is_checkpoint_complete("phase1_understanding")

    with pytest.raises(RuntimeError, match="configuration fingerprint"):
        state_module.PipelineState.resume(workdir, short_av_video, "b" * 64)
    short_av_video.write_bytes(short_av_video.read_bytes() + b"changed")
    with pytest.raises(RuntimeError, match="input hash"):
        state_module.PipelineState.resume(workdir, short_av_video, "a" * 64)


def test_media_probe_and_frame_extraction(short_av_video: Path, tmp_path: Path):
    media = import_pipeline_module("media")
    probe = media.probe_video(short_av_video)
    assert probe.duration > 1
    assert (probe.width, probe.height) == (640, 360)
    assert probe.has_audio is True

    frame = media.extract_frame(short_av_video, 0.5, tmp_path / "frame.jpg", width=320)
    with Image.open(frame) as opened:
        assert opened.width == 320
        assert opened.height > 0
    end_frames = media.extract_frames(short_av_video, [probe.duration], tmp_path / "end-frames")
    assert len(end_frames) == 1
    assert end_frames[0].timestamp < probe.duration
    assert end_frames[0].path.is_file()


def test_image_quality_and_near_duplicate_detection(tmp_path: Path):
    quality = import_pipeline_module("image_quality")
    first = tmp_path / "first.png"
    duplicate = tmp_path / "duplicate.png"
    different = tmp_path / "different.png"
    black = tmp_path / "black.png"

    forward = Image.new("RGB", (400, 240))
    reverse = Image.new("RGB", (400, 240))
    for x in range(400):
        value = round(255 * x / 399)
        for y in range(240):
            forward.putpixel((x, y), (value, 255 - value, (x + y) % 256))
            reverse.putpixel((x, y), (255 - value, value, (x + y) % 256))
    forward.save(first)
    forward.save(duplicate)
    reverse.save(different)
    Image.new("RGB", (400, 240), "black").save(black)

    first_metrics = quality.evaluate_image(first, min_score=0)
    duplicate_metrics = quality.evaluate_image(duplicate, min_score=0)
    different_metrics = quality.evaluate_image(different, min_score=0)
    assert quality.are_near_duplicates(first_metrics, duplicate_metrics)
    assert not quality.are_near_duplicates(first_metrics, different_metrics)
    assert "too_dark" in quality.evaluate_image(black).reject_reasons


def test_frame_selector_generates_r0_r1_r2_and_calls_review_once(monkeypatch, tmp_path: Path):
    selection = import_pipeline_module("frame_selection")
    media = import_pipeline_module("media")
    schemas = import_pipeline_module("schemas")
    quality = import_pipeline_module("image_quality")
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"not decoded because extraction is mocked")
    calls: list[list[dict]] = []

    def image_at(path: Path, timestamp: float):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (640, 360), (round(timestamp * 71) % 255, 80, 170)).save(path)
        return media.ExtractedFrame(round(timestamp, 3), path, "test")

    def fake_coarse(_video, output_dir, **_kwargs):
        root = Path(output_dir)
        return [image_at(root / "coarse-250.jpg", 0.25), image_at(root / "coarse-2750.jpg", 2.75)]

    def fake_frames(_video, timestamps, output_dir, **_kwargs):
        root = Path(output_dir)
        return [image_at(root / f"frame-{round(value * 1000)}.jpg", value) for value in timestamps]

    def fake_quality(path, **_kwargs):
        token = sum(path.name.encode()) % 255
        return quality.QualityMetrics(
            640,
            360,
            120.0,
            45.0,
            0.0,
            0.0,
            900.0,
            0.1,
            0.6,
            0.8,
            (),
            f"{token:016x}"[-16:],
            tuple([1 / 48] * 48),
        )

    monkeypatch.setattr(selection, "extract_coarse_frames", fake_coarse)
    monkeypatch.setattr(selection, "extract_frames", fake_frames)
    monkeypatch.setattr(selection, "evaluate_image", fake_quality)
    monkeypatch.setattr(selection, "are_near_duplicates", lambda *_args, **_kwargs: False)
    selector = selection.FrameSelector(
        video,
        tmp_path / "work",
        duration=3.0,
        config=selection.SelectionConfig(
            coarse_frames=2,
            candidate_limit=4,
            step_padding=0.5,
            dense_step=0.75,
            fine_step=0.1,
            fine_radius=0.3,
            max_step_candidates=20,
            min_frame_score=0,
            min_relevance=0.5,
        ),
    )
    step = schemas.PlanStep(
        id=1,
        title="Step",
        objective="Show the control",
        start=1.0,
        end=2.0,
        visual_targets=[schemas.VisualTarget("control", "primary", "visible control")],
    )

    def review(requests):
        calls.append(requests)
        candidate = requests[0]["candidates"][0]
        return [
            {
                "step_id": 1,
                "target_id": "control",
                "selected_id": candidate["id"],
                "relevance": 0.99,
                "reason": "clear",
                "ranked_ids": [candidate["id"]],
            }
        ]

    result = selector.select_steps([step], review)
    assert len(calls) == 1
    assert {item["round_name"] for item in calls[0][0]["candidates"]} == {"R0", "R1", "R2"}
    assert len(result.selections[0].choices) == 1


def test_validation_and_error_results_are_structured(short_av_video: Path, tmp_path: Path):
    config_module = import_pipeline_module("config")
    runner = import_pipeline_module("runner")
    with pytest.raises(ValueError, match="URLs are not accepted"):
        config_module.PipelineConfig("https://example.test/video.mp4", tmp_path / "out.pdf")
    with pytest.raises(ValueError, match="mutually exclusive"):
        config_module.PipelineConfig(
            short_av_video,
            tmp_path / "out.pdf",
            no_asr=True,
            require_asr=True,
        )

    failed = runner.run_video2note(video_path=tmp_path / "missing.mp4", output_path=tmp_path / "out.pdf")
    assert failed["exit_code"] == 1
    assert failed["status"] == "failed"
    missing_status = runner.get_status(workdir=tmp_path / "missing-workdir")
    assert missing_status["exit_code"] == 1
    assert missing_status["resumable"] is False


def _clean_audit(schemas):
    return schemas.AuditReport(
        passed=True,
        input_hash_matches=True,
        unresolved_placeholders=False,
        step_count=1,
        image_count=1,
        page_count=1,
        coverage=1.0,
    )


def _high_review(schemas, verdict: str):
    return schemas.ReviewReport(
        verdict=verdict,
        overall=9.5,
        scores={"accuracy": 9.0, "completeness": 9.0, "clarity": 9.0, "visual_quality": 9.0},
        summary="scores are high",
    )


def test_review_gate_requires_the_model_pass_verdict():
    audit_module = import_pipeline_module("audit")
    schemas = import_pipeline_module("schemas")
    audit = _clean_audit(schemas)

    assert audit_module.passes_review_gate(audit, _high_review(schemas, "pass")) is True
    # A repair verdict must never be accepted, however high the numeric scores are.
    assert audit_module.passes_review_gate(audit, _high_review(schemas, "repair")) is False
    unavailable = _high_review(schemas, "pass")
    unavailable.available = False
    assert audit_module.passes_review_gate(audit, unavailable) is False


def test_model_interfaces_expose_only_current_names(short_av_video: Path, tmp_path: Path):
    config_module = import_pipeline_module("config")
    tool = import_capability_module("tools.create_video_note")
    cli = import_capability_module("cli")

    config_fields = {
        name for name, field in config_module.PipelineConfig.__dataclass_fields__.items() if field.init
    }
    tool_fields = set(tool.CreateVideoNoteArgs.model_json_schema()["properties"])
    cli_fields = {action.dest for action in cli._parser()._actions}
    required_models = {"omni_model", "vl_model", "review_model"}
    removed_fields = {"api_key", "base_url", "text_model", "judge_model"}

    assert required_models <= config_fields
    assert required_models <= tool_fields
    assert required_models <= cli_fields
    assert removed_fields.isdisjoint(config_fields)
    assert removed_fields.isdisjoint(tool_fields)
    assert removed_fields.isdisjoint(cli_fields)

    with pytest.raises(TypeError, match="unexpected keyword argument 'text_model'"):
        config_module.PipelineConfig(short_av_video, tmp_path / "old.pdf", text_model="legacy")
    with pytest.raises(Exception, match="Extra inputs are not permitted"):
        tool.CreateVideoNoteArgs.model_validate(
            {"video_path": str(short_av_video), "output_path": str(tmp_path / "old.pdf"), "api_key": "secret"}
        )


def test_canonical_model_and_endpoint_resolution(short_av_video: Path, tmp_path: Path, monkeypatch):
    config_module = import_pipeline_module("config")
    canonical = {
        # A DashScope host: the shared resolver maps only known hosts to DASHSCOPE_API_KEY.
        "DASHSCOPE_BASE_URL": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "DASHSCOPE_API_KEY": "canonical-secret",
        "QWEN_MM_API_OMNI_MODEL": "canonical-omni",
        "QWEN_MM_API_VL_MODEL": "canonical-vl",
    }
    legacy = {
        "OMNI_BASE_URL": "https://legacy.example/v1",
        "OMNI_API_KEY": "legacy-secret",
        "OMNI_MODEL": "legacy-omni",
    }
    for name, value in (canonical | legacy).items():
        monkeypatch.setenv(name, value)

    resolved = config_module.PipelineConfig(short_av_video, tmp_path / "resolved.pdf")
    assert resolved.omni_model == "canonical-omni"
    assert resolved.vl_model == "canonical-vl"
    assert resolved.review_model == "canonical-vl"
    assert resolved._base_url == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    assert resolved._api_key == "canonical-secret"

    # An unlisted host gets no credential: keys are scoped per endpoint, so a private gateway must
    # be given its own key rather than silently receiving the DashScope one.
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://gateway.example/v1")
    unlisted = config_module.PipelineConfig(short_av_video, tmp_path / "unlisted.pdf")
    assert unlisted._base_url == "https://gateway.example/v1"
    assert unlisted._api_key == "EMPTY"
    monkeypatch.setenv("DASHSCOPE_BASE_URL", canonical["DASHSCOPE_BASE_URL"])

    explicit = config_module.PipelineConfig(
        short_av_video,
        tmp_path / "explicit.pdf",
        omni_model="explicit-omni",
        vl_model="explicit-vl",
        review_model="explicit-review",
    )
    assert (explicit.omni_model, explicit.vl_model, explicit.review_model) == (
        "explicit-omni",
        "explicit-vl",
        "explicit-review",
    )


def test_fingerprint_includes_endpoint_and_models_but_never_key(short_av_video: Path, tmp_path: Path, monkeypatch):
    config_module = import_pipeline_module("config")
    output = tmp_path / "note.pdf"
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://first.example/v1")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "first-secret")
    first = config_module.PipelineConfig(short_av_video, output, omni_model="omni-a", vl_model="vl-a")
    payload = first.fingerprint_payload()

    assert payload["base_url"] == "https://first.example/v1"
    assert (payload["omni_model"], payload["vl_model"], payload["review_model"]) == (
        "omni-a",
        "vl-a",
        "vl-a",
    )
    assert "api_key" not in first.to_dict()
    assert "_api_key" not in asdict(first)
    assert "api_key" not in payload
    assert "first-secret" not in json.dumps(first.to_dict())

    monkeypatch.setenv("DASHSCOPE_API_KEY", "second-secret")
    key_changed = config_module.PipelineConfig(short_av_video, output, omni_model="omni-a", vl_model="vl-a")
    assert key_changed.fingerprint() == first.fingerprint()

    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://second.example/v1")
    endpoint_changed = config_module.PipelineConfig(short_av_video, output, omni_model="omni-a", vl_model="vl-a")
    assert endpoint_changed.fingerprint() != first.fingerprint()
    model_changed = config_module.PipelineConfig(short_av_video, output, omni_model="omni-b", vl_model="vl-a")
    assert model_changed.fingerprint() != endpoint_changed.fingerprint()


def test_iteration_and_plan_limits_are_hard_capped(short_av_video: Path, tmp_path: Path):
    config_module = import_pipeline_module("config")
    schemas = import_pipeline_module("schemas")
    tool = import_capability_module("tools.create_video_note")

    assert config_module.MAX_ITERATIONS_LIMIT == 8
    capped = config_module.PipelineConfig(short_av_video, tmp_path / "capped.pdf", max_iterations=8)
    assert capped.max_iterations == 8
    with pytest.raises(ValueError, match="must not exceed 8"):
        config_module.PipelineConfig(short_av_video, tmp_path / "over.pdf", max_iterations=9)

    with pytest.raises(Exception, match="less than or equal to 8"):
        tool.CreateVideoNoteArgs.model_validate(
            {"video_path": str(short_av_video), "output_path": str(tmp_path / "tool.pdf"), "max_iterations": 9}
        )

    def plan_with(step_count: int) -> dict:
        return {
            "title": "Long tutorial",
            "audience": "beginners",
            "overview_goal": "Complete every task",
            "steps": [
                {
                    "id": index,
                    "title": f"Step {index}",
                    "objective": f"Do part {index}",
                    "start": float(index),
                    "end": float(index) + 0.5,
                    "visual_targets": [{"id": f"t{index}", "role": "primary", "query": "the control"}],
                }
                for index in range(1, step_count + 1)
            ],
        }

    assert len(schemas.DocumentPlan.parse(plan_with(schemas.MAX_PLAN_STEPS)).steps) == schemas.MAX_PLAN_STEPS
    with pytest.raises(ValueError, match="must not exceed 30 steps"):
        schemas.DocumentPlan.parse(plan_with(schemas.MAX_PLAN_STEPS + 1))


def test_model_gateway_uses_resolved_endpoint_and_role_models(short_av_video: Path, tmp_path: Path, monkeypatch):
    gateway = import_pipeline_module("model_gateway")
    config_module = import_pipeline_module("config")
    media = import_pipeline_module("media")
    schemas = import_pipeline_module("schemas")
    base_url = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    monkeypatch.setenv("DASHSCOPE_BASE_URL", base_url)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "gateway-secret")
    config = config_module.PipelineConfig(
        short_av_video,
        tmp_path / "roles.pdf",
        omni_model="omni-role",
        vl_model="vl-role",
        review_model="review-role",
    )

    endpoint_calls = []
    monkeypatch.setattr(gateway, "call_openai_chat", lambda **kwargs: endpoint_calls.append(kwargs) or {})
    gateway._openai_json(config, model="vl-role", system="system", content="content")
    assert endpoint_calls[0]["base_url"] == base_url
    assert endpoint_calls[0]["api_key"] == "gateway-secret"

    understanding = schemas.VideoUnderstanding(
        language="en",
        subject="Task",
        summary="Summary",
        events=[schemas.TimedEvent(0.0, 1.0, "Action")],
    )
    omni_calls = []
    monkeypatch.setattr(gateway, "call_omni_json", lambda **kwargs: omni_calls.append(kwargs) or understanding.to_dict())
    frame_one = tmp_path / "frame-one.png"
    frame_two = tmp_path / "frame-two.png"
    Image.new("RGB", (16, 16), "white").save(frame_one)
    Image.new("RGB", (16, 16), "black").save(frame_two)
    chunk = media.MediaChunk(
        path=short_av_video,
        source_start=0.0,
        source_end=1.0,
        delivery={"kind": "frames", "frames": [str(frame_one), str(frame_two)]},
    )
    probe = schemas.ProbeResult(
        path=str(short_av_video),
        size_bytes=short_av_video.stat().st_size,
        duration=1.0,
        width=640,
        height=360,
        fps=8.0,
        video_codec="test",
        has_audio=True,
    )
    gateway.understand_video(config, probe, [chunk])
    assert omni_calls[0]["model"] == "omni-role"
    assert omni_calls[0]["base_url"] == base_url
    assert omni_calls[0]["api_key"] == "gateway-secret"

    plan = schemas.DocumentPlan(
        title="Title",
        audience="Audience",
        overview_goal="Goal",
        steps=[
            schemas.PlanStep(
                1,
                "Step",
                "Objective",
                0.0,
                1.0,
                [schemas.VisualTarget("target", "primary", "object")],
            )
        ],
    )
    draft = schemas.DocumentDraft(
        title="Title",
        overview="Overview",
        steps=[schemas.DraftStep(1, "Step", "Instruction")],
    )
    review = _high_review(schemas, "pass")
    parsed_roles = []

    def fake_parse(_config, *, schema, model, **_kwargs):
        parsed_roles.append((schema.__name__, model))
        return {
            "DocumentPlan": plan,
            "DocumentDraft": draft,
            "ReviewReport": review,
        }[schema.__name__]

    monkeypatch.setattr(gateway, "_parse_with_one_repair", fake_parse)
    assert gateway.plan_document(config, understanding) is plan
    assert gateway.write_document(config, plan, understanding) is draft
    preview_dir = tmp_path / "previews"
    preview_dir.mkdir()
    Image.new("RGB", (16, 16), "white").save(preview_dir / "page-001.png")
    assert gateway.review_pdf(
        config,
        tmp_path / "unused.pdf",
        _clean_audit(schemas),
        understanding=understanding,
        plan=plan,
        draft=draft,
        preview_dir=preview_dir,
    ) is review
    assert parsed_roles == [
        ("DocumentPlan", "vl-role"),
        ("DocumentDraft", "vl-role"),
        ("ReviewReport", "review-role"),
    ]

    repair_roles = []
    monkeypatch.setattr(
        gateway,
        "_openai_json",
        lambda _config, *, model, **_kwargs: repair_roles.append(model) or json.dumps({"actions": []}),
    )
    assert gateway.propose_repairs(config, plan, draft, _clean_audit(schemas), review) == []
    assert repair_roles == ["vl-role"]


def test_candidate_reviews_are_batched_under_the_image_budget(short_av_video: Path, tmp_path: Path, monkeypatch):
    gateway = import_pipeline_module("model_gateway")
    config_module = import_pipeline_module("config")
    schemas = import_pipeline_module("schemas")
    config = config_module.PipelineConfig(
        short_av_video,
        tmp_path / "batched.pdf",
        vl_model="vl-role",
        review_model="review-role",
    )

    requests = []
    for step_index in range(1, 4):
        step = schemas.PlanStep(
            id=step_index,
            title=f"Step {step_index}",
            objective="Show the control",
            start=float(step_index) * 0.1,
            end=float(step_index) * 0.1 + 0.2,
            visual_targets=[schemas.VisualTarget(f"target{step_index}", "primary", "the visible control")],
        )
        candidates = []
        for candidate_index in range(20):
            path = tmp_path / f"frame-{step_index}-{candidate_index:02d}.jpg"
            Image.new("RGB", (16, 16), (candidate_index * 5, 40 * step_index, 160)).save(path)
            candidates.append({"id": f"s{step_index}-c{candidate_index:02d}", "absolute_path": str(path)})
        requests.append({"step": step.to_dict(), "candidates": candidates, "minimum_relevance": 0.5})

    image_counts: list[int] = []

    def fake_openai_json(_config, *, model, system, content, max_tokens=8192):
        assert model == str(config.review_model)
        assert "untrusted source data" in system
        image_counts.append(sum(part.get("type") == "image_url" for part in content))
        prompt_requests = json.loads(content[-1]["text"].split("\n", 1)[1])
        reviews = []
        for request in prompt_requests:
            selected = request["candidates"][0]["id"]
            for target in request["step"]["visual_targets"]:
                reviews.append(
                    {
                        "step_id": request["step"]["id"],
                        "target_id": target["id"],
                        "selected_id": selected,
                        "relevance": 0.9,
                        "reason": "clear evidence",
                        "ranked_ids": [selected],
                    }
                )
        return json.dumps({"reviews": reviews})

    monkeypatch.setattr(gateway, "_openai_json", fake_openai_json)
    reviews = gateway.review_candidates(config, requests)

    assert gateway.MAX_REVIEW_IMAGES == 40
    assert image_counts == [40, 20]
    assert [(item.step_id, item.target_id) for item in reviews] == [(1, "target1"), (2, "target2"), (3, "target3")]


def test_omni_payload_aliases_are_normalized_without_relaxing_schemas():
    gateway = import_pipeline_module("model_gateway")
    schemas = import_pipeline_module("schemas")

    understanding = gateway._normalize_understanding_payload(
        {
            "language": "en",
            "subject": "Task",
            "summary": "Summary",
            "events": [{"start_time": "00:01", "end_time": "2.5s", "description": "Action"}],
            "audience": ["beginners", "home users"],
            "prerequisites": "one item",
            "tools": None,
            "security": "use care",
            "uncertainties": None,
            "visible_terms": "label",
        }
    )
    parsed = schemas.VideoUnderstanding.parse(understanding)
    assert (parsed.events[0].start, parsed.events[0].end, parsed.events[0].fact) == (1.0, 2.5, "Action")
    assert parsed.audience == "beginners / home users"
    assert parsed.prerequisites == ["one item"]
    assert parsed.tools == []
    assert parsed.safety == ["use care"]
    assert parsed.visible_terms == ["label"]

    plan = gateway._normalize_schema_payload(
        schemas.DocumentPlan,
        {"title": "T", "audience": "A", "overview": "G", "steps": []},
    )
    assert plan["overview_goal"] == "G" and "overview" not in plan
    review = gateway._normalize_schema_payload(
        schemas.ReviewReport,
        {
            "verdict": "pass",
            "score": "90%",
            "dimension_scores": {"accuracy": "high", "completeness": "8.5"},
            "issues": None,
        },
    )
    assert review["overall"] == 9.0
    assert review["scores"] == {"accuracy": 9.0, "completeness": 8.5}
    assert review["issues"] == []


def test_review_candidates_handles_qualitative_scores_and_omissions(short_av_video: Path, tmp_path: Path, monkeypatch):
    gateway = import_pipeline_module("model_gateway")
    config_module = import_pipeline_module("config")
    schemas = import_pipeline_module("schemas")
    config = config_module.PipelineConfig(short_av_video, tmp_path / "review.pdf")
    image = tmp_path / "frame.jpg"
    Image.new("RGB", (16, 16), (80, 120, 160)).save(image)
    requests = [
        {
            "step": schemas.PlanStep(
                1,
                "Step 1",
                "Show it",
                0.0,
                1.0,
                [schemas.VisualTarget("target1", "primary", "visible item")],
            ).to_dict(),
            "candidates": [{"id": "candidate1", "absolute_path": str(image)}],
            "minimum_relevance": 0.5,
        },
        {
            "step": schemas.PlanStep(
                2,
                "Step 2",
                "No frame",
                1.0,
                2.0,
                [schemas.VisualTarget("target2", "primary", "missing item")],
            ).to_dict(),
            "candidates": [],
            "minimum_relevance": 0.5,
        },
    ]

    monkeypatch.setattr(
        gateway,
        "_openai_json",
        lambda *_args, **_kwargs: json.dumps(
            {
                "reviews": [
                    {
                        "step_id": 1,
                        "target_id": "target1",
                        "selected_id": "candidate1",
                        "relevance": "high",
                        "ranked_ids": ["candidate1"],
                    }
                ]
            }
        ),
    )
    reviews = gateway.review_candidates(config, requests)
    assert [(item.target_id, item.selected_id, item.relevance) for item in reviews] == [
        ("target2", None, 0.0),
        ("target1", "candidate1", 0.9),
    ]


def test_resume_is_bound_to_its_output_path(short_av_video: Path, tmp_path: Path):
    config_module = import_pipeline_module("config")
    state_module = import_pipeline_module("state")
    workdir = tmp_path / "job"
    first = config_module.PipelineConfig(short_av_video, tmp_path / "first.pdf", workdir=workdir)
    redirected = config_module.PipelineConfig(short_av_video, tmp_path / "second.pdf", workdir=workdir)
    assert first.fingerprint() != redirected.fingerprint()

    state_module.PipelineState.create(workdir, short_av_video, first.fingerprint())
    assert state_module.PipelineState.resume(workdir, short_av_video, first.fingerprint()).status == "new"
    with pytest.raises(RuntimeError, match="configuration fingerprint"):
        state_module.PipelineState.resume(workdir, short_av_video, redirected.fingerprint())
