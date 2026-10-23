"""Offline functional, packaging, privacy and MCP checks for Omni Deep Research."""

from __future__ import annotations

import importlib.util
import json
import shutil
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
import qwen_mm_plugins_omnidr as plugin
from conftest import mcp_call
from qwen_mm_plugins_omnidr import config, media, project, sources
from qwen_mm_plugins_omnidr.common import InputError, guarded

from shared import api_omni, api_openai, env, omni_media

ROOT = Path(__file__).resolve().parents[1]
CAP = ROOT / "src/capabilities/omnidr"
SERVER = CAP / "qwen_mm_plugins_omnidr"


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    from qwen_mm_plugins_omnidr import policy

    monkeypatch.setenv("QWEN_MM_CONFIG", str(tmp_path / "absent-config"))
    for name, *_ in env.CONFIG_FIELDS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://provider.invalid/v1")
    monkeypatch.setenv("QWEN_MM_API_OMNI_MODEL", "fixture-omni-model")
    monkeypatch.setattr(policy, "STATE_ROOT", tmp_path / "private-policy")


@pytest.fixture
def source_response(monkeypatch):
    """Serve in-memory source bytes with Requests' normal header/encoding behavior."""
    import requests

    def respond(body, content_type="application/octet-stream"):
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = content_type
        response.encoding = requests.utils.get_encoding_from_headers(response.headers)
        response.raw = BytesIO(body)

        def get(_session, _url, **kwargs):
            assert set(kwargs["headers"]) == {"User-Agent"}
            return response

        monkeypatch.setattr(requests.Session, "get", get)
        return response

    return respond


@pytest.fixture
def browser_page():
    sync_playwright = pytest.importorskip("playwright.sync_api", reason="Playwright unavailable").sync_playwright
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip("Chromium unavailable")
        browser = playwright.chromium.launch(args=["--no-sandbox", "--mute-audio"])
        try:
            yield browser.new_page()
        finally:
            browser.close()


def call(name, **arguments):
    return json.loads(plugin.get_handler(name)(arguments)[0]["text"])


def write_data(root, data):
    (root / "js/data.js").write_text("window.DATA = " + json.dumps(data, ensure_ascii=False) + ";\n")


@pytest.fixture
def report(tmp_path, sample_video):
    root = tmp_path / "report"
    project.prepare(sample_video, str(root), "A synthetic test video", "en", "t05")
    return root


@pytest.fixture
def populated_report(report):
    """A deliberately small synthetic data fixture; no claim of real research."""
    _, manifest, data = project.load_project(str(report))
    quote1 = "The demonstration fixture has two contrasting color regions."
    quote2 = "Independent test text describes a synthetic pattern for display checks."
    for sid, quote in [("S1", quote1), ("S2", quote2)]:
        (report / f"_assets/docs/{sid}.html").write_text(f"<html><body><p>{quote}</p></body></html>")
    data.update(
        summary="A small synthetic display check [S1].",
        segments=[
            {
                "id": "seg1",
                "start": 0,
                "end": manifest["duration"],
                "title": "Test pattern",
                "caption": [{"type": "text", "text": "An artificial video pattern [S1]."}],
                "sources": ["S1"],
            }
        ],
        anchors=[
            {
                "id": "A1",
                "label": "Pattern comparison",
                "category": "comparison",
                "why": "Check the evidence contract.",
                "segmentIds": ["seg1"],
            }
        ],
        research=[
            {
                "id": sid,
                "title": "Synthetic source " + sid,
                "type": "web",
                "url": f"https://publisher{sid[-1]}.invalid/article",
                "summary": quote,
                "media": f"_assets/docs/{sid}.html",
                "anchorId": "A1",
            }
            for sid, quote in [("S1", quote1), ("S2", quote2)]
        ],
        insights=[
            {
                "kind": "compare",
                "title": "Synthetic comparison",
                "body": "Compare the two fixture statements [S1][S2].",
                "sources": ["S1", "S2"],
                "evidence": [
                    {"srcId": sid, "quote": quote, "local": f"_assets/docs/{sid}.html"}
                    for sid, quote in [("S1", quote1), ("S2", quote2)]
                ],
            }
        ],
    )
    write_data(report, data)
    return report


def test_registry_schemas_and_generic_entry():
    assert len(plugin.SPECS) == 14
    assert (SERVER / "__main__.py").read_bytes() == (
        ROOT / "src/capabilities/example/qwen_mm_plugins_example/__main__.py"
    ).read_bytes()
    for spec in plugin.SPECS:
        assert spec.description
        assert spec.input_schema["type"] == "object"
        assert all(field.description for field in spec.args_model.model_fields.values())
        assert "api_key" not in spec.args_model.model_fields
    assert plugin.get_handler("nonexistent") is None


def test_shell_schema_and_actual_report_inspection(report, monkeypatch, capsys, tmp_path):
    import sys

    from qwen_mm_plugins_omnidr.invoke import main

    monkeypatch.setattr(sys, "argv", ["invoke", "inspect_omnidr_report", "--schema"])
    assert main() == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema == next(s.meta for s in plugin.SPECS if s.name == "inspect_omnidr_report")
    args = tmp_path / "arguments.json"
    args.write_text(json.dumps({schema["inputSchema"]["required"][0]: str(report)}))
    monkeypatch.setattr(sys, "argv", ["invoke", "inspect_omnidr_report", "--arguments-file", str(args)])
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "inspected"
    assert result["project_dir"] == str(report)


@pytest.mark.parametrize("tool", ["inspect_omnidr_report", "validate_omnidr_report"])
def test_shell_invalid_arguments_show_fields_without_input(tool, monkeypatch, capsys, tmp_path):
    import sys

    from qwen_mm_plugins_omnidr.invoke import main

    secret = "private-credential-do-not-echo"
    args = tmp_path / "invalid.json"
    args.write_text(json.dumps({"report_dir": secret, secret: "unused", "stage": secret}))
    monkeypatch.setattr(plugin, "get_handler", lambda _: pytest.fail("Invalid arguments must not run the tool"))
    monkeypatch.setattr(sys, "argv", ["invoke", tool, "--arguments-file", str(args)])
    assert main() == 1
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["error"] == "invalid_arguments"
    assert {"field": "project_dir", "error": "missing"} in result["fields"]
    assert secret not in output


def test_manifests_follow_plugin_release_contract():
    index = json.loads((ROOT / "plugin-versions.json").read_text())
    version = index["plugins"].get("omnidr")
    if version is not None:
        assert version == plugin.__version__
    ref = index["tag_format"].format(cap="omnidr", version=version) if version is not None else "main"
    for kind in ["claude", "codex", "qoder"]:
        manifest = json.loads((CAP / f".{kind}-plugin/plugin.json").read_text())
        assert manifest["name"] == "qwen-mm-plugins-omnidr"
        assert manifest["version"] == plugin.__version__
        skills = manifest["skills"]
        for path in skills if isinstance(skills, list) else [skills]:
            assert (CAP / path / "SKILL.md").is_file()
        if kind == "qoder":
            assert manifest["displayName"]
            assert manifest["mcp"] == ".mcp.json"
    config = json.loads((CAP / ".mcp.json").read_text())
    assert set(config["mcpServers"]) == {"qwen-mm-plugins-omnidr"}
    assert config["mcpServers"]["qwen-mm-plugins-omnidr"]["args"] == [
        "--from",
        f"qwen-mm-plugins[omnidr] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@{ref}",
        "qwen-mm-plugins-omnidr",
    ]


def test_skill_references_resolve_and_bundle_is_small():
    import re

    for document in (CAP / "skill").rglob("*.md"):
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", document.read_text()):
            if "://" not in target:
                assert (document.parent / target).is_file(), (document, target)
    files = [p for p in CAP.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    # Report UI, validation and the bounded handoff index; excludes weights/caches.
    assert sum(p.stat().st_size for p in files) < 315_000
    assert not any(p.suffix in {".bin", ".safetensors", ".pt", ".pth"} for p in files)
    assert not any(p.name in {"settings.json", "youtube-cookies.txt"} for p in files)


def test_prepare_is_portable_and_draft_is_not_complete(report, sample_video):
    root, manifest, data = project.load_project(str(report))
    assert manifest["source_sha256"] == project.sha256(Path(sample_video))
    assert data["video"]["src"] == "media/source.mp4"
    assert project.probe(root / "media/source.mp4")["streams"][0]["codec_name"] == "h264"
    assert not data["segments"]
    assert project.validate(str(root))["status"] == "incomplete"
    original = (root / "js/data.js").read_bytes()
    with pytest.raises(InputError, match="already exists"):
        project.prepare(sample_video, str(root), "replacement", "en", "t01")
    assert (root / "js/data.js").read_bytes() == original


def test_screenshot_reused_evidence_path_reports_actionable_error(tmp_path, monkeypatch):
    from qwen_mm_plugins_omnidr.tools import screenshot

    page = tmp_path / "index.html"
    page.write_text("<html><body>Saved report</body></html>")
    existing = tmp_path / "review.png"
    existing.write_bytes(b"previous evidence")
    monkeypatch.setattr(screenshot, "run_isolated", lambda *a, **kw: pytest.fail("No browser needed for invalid input"))
    result = call("omnidr_screenshot", source=str(page), output_path=str(existing))
    assert result["status"] == "failed" and "Output already exists" in result["error"]
    assert "choose a new path" in result["error"]
    assert existing.read_bytes() == b"previous evidence"
    result = call("omnidr_screenshot", source=str(page), output_path=str(tmp_path / "review.jpg"))
    assert result["status"] == "failed" and ".png" in result["error"]


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js unavailable")
def test_report_validator_accepts_saved_evidence(populated_report):
    result = project.validate(str(populated_report), stage="structure")
    assert result["status"] == "structure_valid", result
    assert result["structural_validation"] is True
    assert result["sources"] == 2
    assert "factual" in result["review_required"]
    inspected = call("inspect_omnidr_report", project_dir=str(populated_report))
    assert inspected["status"] == "inspected"
    assert inspected["validation"] == "not_run"


def test_authoring_diagnostics_distinguish_findings_from_source_catalog(populated_report):
    root, _, data = project.load_project(str(populated_report))
    data["insights"][0]["body"] = "A supported comparison [S1]."
    data["research"].append({**data["research"][1], "id": "S3", "summary": "Catalog only [S3]."})
    write_data(root, data)
    result = call("inspect_omnidr_report", project_dir=str(root))["authoring_diagnostics"]
    assert result["sources"]["without_inline_citations"] == ["S2", "S3"]
    assert result["sources"]["not_linked_to_findings"] == ["S3"]
    data["segments"][0]["caption"].append(
        {"type": "flow", "title": "Mechanism", "steps": ["Condition [S2]", "Consequence [S3]"]}
    )
    write_data(root, data)
    result = call("inspect_omnidr_report", project_dir=str(root))["authoring_diagnostics"]
    assert result["sources"]["without_inline_citations"] == []
    assert result["presentation"]["structured_diagram_blocks"] == 1


def test_reader_handoff_requires_separate_handling_despite_source_citation(populated_report):
    root, _, data = project.load_project(str(populated_report))
    note = root / "_work/anchors/background/findings.md"
    note.parent.mkdir(parents=True)
    note.write_text("A measured operating condition omitted from the summary.")
    data["insights"][0]["body"] = "A broad comparison [S1]."
    write_data(root, data)
    before = (root / "js/data.js").read_bytes()
    result = call("inspect_omnidr_report", project_dir=str(root))["authoring_diagnostics"]
    handoff = result["evidence_handoffs"]["notes"][0]
    assert handoff["handling_status"] == "unrecorded"
    assert "S1" in result["sources"]["inline_cited"]
    record = {
        "path": handoff["path"],
        "notes_sha256": handoff["notes_sha256"],
        "disposition": "integrated",
        "report_locations": ["insights/0"],
        "reason": "Author-declared handling, not independent verification.",
    }
    (root / "_work/synthesis.json").write_text(json.dumps({"handoffs": [record]}))
    result = call("inspect_omnidr_report", project_dir=str(root))["authoring_diagnostics"]
    assert result["evidence_handoffs"]["notes"][0]["handling_status"] == "recorded"
    note.write_text("New findings requiring another integration pass.")
    result = call("inspect_omnidr_report", project_dir=str(root))["authoring_diagnostics"]
    assert result["evidence_handoffs"]["notes"][0]["handling_status"] == "notes_changed"
    assert (root / "js/data.js").read_bytes() == before


@pytest.mark.parametrize("name", ["notes.md", "reader-notes.json", "research-notes.txt", "page-grounded-findings.json"])
def test_reader_handoff_inventory_excludes_reviewer_notes_and_path_escapes(tmp_path, name):
    from qwen_mm_plugins_omnidr.report_health import evidence_handoffs

    root = tmp_path / "report"
    notes = root / "_work/docs/manual" / name
    notes.parent.mkdir(parents=True)
    notes.write_text("Permitted document handoff.")
    review = root / "_work/reviews/check/notes.md"
    review.parent.mkdir(parents=True)
    review.write_text("Independent review working context.")
    outside = tmp_path / "outside.md"
    outside.write_text("Not a report artifact.")
    (notes.parent / "findings.md").symlink_to(outside)
    result = evidence_handoffs(root)
    assert [item["path"] for item in result["notes"]] == [f"_work/docs/manual/{name}"]


@pytest.mark.parametrize("location", ["insights/0", "segments/0/insights/0"])
def test_review_brief_isolates_exact_claim_and_urls(populated_report, location):
    from qwen_mm_plugins_omnidr.audit import claim_records

    root, _, data = project.load_project(str(populated_report))
    claim = data["insights"][0]
    claim["table"] = {"cols": ["Option", "Condition"], "rows": [["A", "Test only"]]}
    if location.startswith("segments"):
        data["segments"][0]["insights"] = [claim]
        data["insights"] = []
    data["summary"] = "unrelated-author-rationale"
    data["research"][0]["summary"] = "author-source-summary"
    data["insights"].append({"kind": "cause", "body": "adjacent-author-conclusion"})
    write_data(root, data)
    before = (root / "js/data.js").read_bytes()
    brief = call("inspect_omnidr_report", project_dir=str(root), review_location=location)
    assert brief["status"] == "review_brief"
    assert brief["location"] == location
    expected = next(c for c in claim_records(data) if c["location"] == location)
    assert brief["claim_sha256"] == expected["claim_sha256"]
    assert brief["claim"]["body"] == claim["body"] and brief["claim"]["table"] == claim["table"]
    assert brief["cited_urls"] == [{"id": s["id"], "url": s["url"]} for s in data["research"]]
    encoded = json.dumps(brief)
    for excluded in [
        "unrelated-author-rationale",
        "author-source-summary",
        "adjacent-author-conclusion",
        "evidence",
        "_assets",
    ]:
        assert excluded not in encoded
    assert (root / "js/data.js").read_bytes() == before
    invalid = call("inspect_omnidr_report", project_dir=str(root), review_location="insights/99")
    assert invalid["status"] == "failed" and "No compare/timeliness claim" in invalid["error"]


@pytest.mark.parametrize("central_metadata", [False, True])
def test_authoring_diagnostics_find_frame_alignment_without_reading_pixels(populated_report, central_metadata):
    root, manifest, data = project.load_project(str(populated_report))
    midpoint = manifest["duration"] / 2
    (root / "img/frame.jpg").write_bytes(b"Synthetic metadata fixture, not a real image.")
    metadata = root / ("_work/frame-provenance/img/frame.jpg.json" if central_metadata else "img/frame.jpg.json")
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(
        json.dumps(
            {
                "source_sha256": manifest["source_sha256"],
                "timestamp": midpoint / 2,
            }
        )
    )
    original = data["segments"][0]
    data["segments"] = [
        {**original, "end": midpoint, "keyframe": "img/frame.jpg"},
        {
            **original,
            "id": "seg2",
            "start": midpoint,
            "keyframe": "img/frame.jpg",
            "caption": [{"type": "image", "src": "img/frame.jpg", "cap": "No recorded time here."}],
        },
    ]
    write_data(root, data)
    before = (root / "js/data.js").read_bytes()
    result = call("inspect_omnidr_report", project_dir=str(root))["authoring_diagnostics"]["presentation"]
    assert result["images_reused_across_segments"][0]["segment_ids"] == ["seg1", "seg2"]
    assert result["images_repeated_within_segments"] == [{"image": "img/frame.jpg", "segment_ids": ["seg2"]}]
    assert result["source_frames_outside_segment"][0]["segment_id"] == "seg2"
    assert result["source_image_captions_without_time"][0]["segment_id"] == "seg2"
    assert (root / "js/data.js").read_bytes() == before


def test_image_inventory_distinguishes_display_from_planning_and_review(populated_report):
    root, _, data = project.load_project(str(populated_report))
    names = ["img/candidate.jpg", "img/banner.png", "anchors/crop.png", "_assets/images/source.png", "_work/review.png"]
    for name in names:
        (root / name).write_bytes(b"Metadata-only fixture; deliberately not decodable pixels.")
    outside = root.parent / "outside.jpg"
    outside.write_bytes(b"Outside the report.")
    (root / "img/outside.jpg").symlink_to(outside)
    data["meta"]["background"] = "img/banner.png"
    data["research"][0]["thumb"] = "_assets/images/source.png"
    data["anchors"][0]["frame"] = "anchors/crop.png"
    data["segments"][0]["title"] = "Part 1"
    write_data(root, data)
    before = {name: (root / name).read_bytes() for name in [*names, "js/data.js"]}
    presentation = call("inspect_omnidr_report", project_dir=str(root))["authoring_diagnostics"]["presentation"]
    assert presentation["unplaced_image_assets"] == ["anchors/crop.png", "img/candidate.jpg"]
    assert presentation["generic_segment_titles"] == ["seg1"]
    assert not presentation["image_inventory"]["truncated"]
    assert before == {name: (root / name).read_bytes() for name in before}

    data["segments"][0].update(title="电路板布局", keyframe="./img/candidate.jpg")
    data["segments"][0]["caption"].append({"type": "image", "src": "anchors/crop.png", "cap": "A selected detail."})
    write_data(root, data)
    presentation = call("inspect_omnidr_report", project_dir=str(root))["authoring_diagnostics"]["presentation"]
    assert presentation["unplaced_image_assets"] == []
    assert presentation["generic_segment_titles"] == []
    assert all((root / name).read_bytes() == before[name] for name in names)


def test_authoring_diagnostics_preserve_stale_reviews_and_unused_readings(full_report):
    root, _, data = project.load_project(str(full_report))
    ledger_path = root / "_work/research-review.json"
    before = ledger_path.read_bytes()
    data["insights"][0]["body"] += " Changed wording after review."
    write_data(root, data)
    folder = root / "_work/unintegrated"
    folder.mkdir()
    (folder / "reading.json").write_text(json.dumps({"status": "read", "source_sha256": "a" * 64}))
    result = call("validate_omnidr_report", project_dir=str(root), stage="delivery")
    assert result["status"] == "incomplete" and not result["delivery_ready"]
    diagnostics = result["authoring_diagnostics"]
    changed = next(c for c in diagnostics["claims"] if c["location"] == "insights/0")
    assert changed["record_current"] is False
    assert changed["issues"] == ["missing_or_stale_claim_hash"]
    assert diagnostics["unintegrated_readings"] == [
        {
            "reading": "_work/unintegrated/reading.json",
            "recorded_status": "read",
            "source_sha256": "a" * 64,
            "integration_verified": False,
        }
    ]
    assert ledger_path.read_bytes() == before


def test_authoring_inventory_cannot_follow_evidence_outside_report(populated_report, tmp_path):
    outside = tmp_path / "private-reading.json"
    outside.write_text(json.dumps({"status": "read", "source_sha256": "private-value"}))
    folder = populated_report / "_work/escape"
    folder.mkdir()
    (folder / "reading.json").symlink_to(outside)
    result = call("inspect_omnidr_report", project_dir=str(populated_report))
    assert "private-value" not in json.dumps(result)
    assert result["authoring_diagnostics"]["unintegrated_readings"] == []


def test_diagrams_render_labels_safe_text_and_working_citations(populated_report, browser_page):
    root, _, data = project.load_project(str(populated_report))
    data["segments"][0]["caption"] += [
        {"type": "flow", "title": "Evidence chain", "steps": ["Observed [S1]", "Explained [S2]"]},
        {
            "type": "mindmap",
            "root": "Options [S1]",
            "branches": [
                {"label": "Current label [S1]", "items": ["Detail [S2]", "<img src=x onerror=alert(1)>"]},
                {"title": "Legacy schema title", "items": ["Compatible"]},
            ],
        },
    ]
    write_data(root, data)
    page = browser_page
    page.goto((root / "index.html").as_uri())
    assert page.locator(".flow .cite").count() == 2
    assert "Current label" in page.locator(".mm-label").nth(0).inner_text()
    assert page.locator(".mm-label").nth(1).inner_text() == "Legacy schema title"
    assert page.locator(".mindmap .cite").count() == 3
    assert page.locator(".mindmap img").count() == 0
    cite = page.locator(".flow .cite").first
    assert cite.get_attribute("data-src") == "S1"
    cite.click()
    assert "flash" in (page.locator("#src-S1").get_attribute("class") or "")


def test_image_caption_has_safe_text_and_clickable_source(populated_report, browser_page):
    from PIL import Image

    root, _, data = project.load_project(str(populated_report))
    Image.new("RGB", (16, 16), "blue").save(root / "img/synthetic.png")
    data["segments"][0]["caption"].append(
        {
            "type": "image",
            "src": "img/synthetic.png",
            "cap": "Illustration <img src=x onerror=alert(1)> [S2] [S404]",
        }
    )
    write_data(root, data)
    page = browser_page
    page.goto((root / "index.html").as_uri())
    caption = page.locator("figcaption").last
    assert "<img src=x onerror=alert(1)>" in caption.inner_text()
    assert caption.locator("img").count() == 0
    cite = caption.locator('.cite[data-src="S2"]')
    assert cite.count() == 1
    assert caption.locator(".cite-broken").inner_text() == "S404"
    cite.click()
    assert "flash" in (page.locator("#src-S2").get_attribute("class") or "")


def test_citation_navigation_survives_a_simultaneous_segment_change(browser_page):
    page = browser_page
    page.set_viewport_size({"width": 1000, "height": 800})
    page.set_content("""<video id="player"></video>
                <section id="seg-one" data-seg-start="0" style="height:100px">
                  Source <sup class="cite" data-src="S1">1</sup>
                </section>
                <section id="seg-two" data-seg-start="10" style="height:100px">Next segment</section>
                <div style="height:5000px"></div><article id="src-S1">Source evidence</article>""")
    page.add_script_tag(path=str(SERVER / "assets/link.js"))
    page.evaluate("""() => {
                window.testTime = 0;
                Object.defineProperty(document.querySelector('#player'), 'currentTime', {get: () => testTime});
                document.querySelector('#player').dispatchEvent(new Event('timeupdate'));
            }""")
    page.wait_for_timeout(1300)
    targets = page.evaluate("""() => {
                const targets = [], scroll = HTMLElement.prototype.scrollIntoView;
                HTMLElement.prototype.scrollIntoView = function (...args) {
                    targets.push(this.id); return scroll.apply(this, args);
                };
                document.querySelector('.cite').click();
                window.testTime = 10;
                document.querySelector('#player').dispatchEvent(new Event('timeupdate'));
                return targets;
            }""")
    assert targets == ["src-S1"]
    assert "is-active" in (page.locator("#seg-two").get_attribute("class") or "")
    page.wait_for_function(
        """() => {
                const r = document.querySelector('#src-S1').getBoundingClientRect();
                return r.top >= 0 && r.bottom <= innerHeight;
            }""",
        timeout=5000,
    )


@pytest.mark.parametrize(
    "defect",
    [
        "missing_quote",
        "invented_quote",
        "duplicate_source",
        "broken_citation",
        "escaped_path",
        "no_evidence",
        "binary_quote",
    ],
)
@pytest.mark.skipif(not shutil.which("node"), reason="Node.js unavailable")
def test_bad_evidence_cannot_pass(populated_report, defect):
    root, _, data = project.load_project(str(populated_report))
    insight = data["insights"][0]
    if defect == "missing_quote":
        insight["evidence"].clear()
    elif defect == "invented_quote":
        insight["evidence"][0]["quote"] = "An invented assertion that is nowhere in the saved source."
    elif defect == "duplicate_source":
        insight["sources"] = ["S1", "S1"]
    elif defect == "broken_citation":
        data["summary"] += " [S999]"
    elif defect == "escaped_path":
        outside = root.parent / "private.txt"
        outside.write_text("private file contents must not be disclosed")
        insight["evidence"][0]["local"] = "../private.txt"
    elif defect == "no_evidence":
        insight.pop("evidence")
    else:
        (root / "_assets/docs/S1.html").write_bytes(b"%PDF-1.7\0binary")
    write_data(root, data)
    result = project.validate(str(root), stage="structure")
    assert result["status"] == "incomplete", result
    assert "private file contents" not in json.dumps(result)


@pytest.mark.parametrize("segment_level", [False, True])
@pytest.mark.skipif(not shutil.which("node"), reason="Node.js unavailable")
def test_unknown_insight_kind_is_rejected(populated_report, segment_level):
    root, _, data = project.load_project(str(populated_report))
    group = data["segments"][0] if segment_level else data
    insight = {"title": "Additional context", "body": "A source-backed observation [S1].", "sources": ["S1"]}
    group.setdefault("insights", []).append(insight)
    write_data(root, data)
    assert project.validate(str(root), stage="structure")["status"] == "structure_valid"

    insight["kind"] = "comparison"
    write_data(root, data)
    result = project.validate(str(root), stage="structure")
    assert result["status"] == "incomplete", result
    assert '.kind "comparison" invalid' in result["details"]


@pytest.mark.parametrize("known", [True, False])
@pytest.mark.skipif(not shutil.which("node"), reason="Node.js unavailable")
def test_diagram_citations_must_resolve(populated_report, known):
    root, _, data = project.load_project(str(populated_report))

    def label(number):
        return f"A sourced finding [S{1 if known else number}]"

    data["segments"][0]["caption"] += [
        {"type": "flow", "title": label(901), "steps": [label(902)]},
        {
            "type": "mindmap",
            "root": label(903),
            "branches": [
                {"label": label(904), "items": [label(905)], "title": "Hidden [S998]", "children": ["Hidden [S999]"]},
                {"title": label(906), "children": [label(907)]},
            ],
        },
    ]
    write_data(root, data)
    result = project.validate(str(root), stage="structure")
    assert result["status"] == ("structure_valid" if known else "incomplete"), result
    if not known:
        for number in range(901, 908):
            assert f"broken citation [S{number}]" in result["details"]
    assert "S998" not in result["details"] and "S999" not in result["details"]


def test_executable_data_is_rejected_before_evaluation(report):
    target = report.parent / "must-not-exist"
    (report / "js/data.js").write_text(
        f'window.DATA = {{}}; require("fs").writeFileSync({json.dumps(str(target))}, "bad");'
    )
    result = call("validate_omnidr_report", project_dir=str(report))
    assert result["status"] == "failed"
    assert not target.exists()


@pytest.mark.parametrize(
    ("code", "location"),
    [
        ('window.DATA = {"text":"secret\nvalue"};', "line 1, column 30"),
        ('\n\nwindow.DATA = {\n  "text": "敏感\nvalue"\n};', "line 4, column 14"),
    ],
)
def test_report_json_error_locates_control_character_without_echo(tmp_path, code, location):
    (tmp_path / project.MANIFEST).write_text('{"schema_version": 1}')
    (tmp_path / "js").mkdir()
    target = tmp_path / "js/data.js"
    target.write_text(code)
    result = call("inspect_omnidr_report", project_dir=str(tmp_path))
    assert result["status"] == "failed"
    assert "js/data.js: invalid JSON at " + location in result["error"]
    assert "json.dumps/JSON.stringify" in result["error"]
    assert all(text not in result["error"] for text in ("secret", "敏感", str(tmp_path)))
    assert target.read_text() == code
    write_data(tmp_path, {"text": "escaped\ntext"})
    assert project.load_project(str(tmp_path))[2] == {"text": "escaped\ntext"}


def test_manifest_json_error_identifies_manifest_not_report(tmp_path):
    (tmp_path / project.MANIFEST).write_text('{"schema_version":1,\n "private-fixture": }\n')
    result = call("inspect_omnidr_report", project_dir=str(tmp_path))
    assert result["status"] == "failed"
    assert project.MANIFEST + ": invalid JSON at line 2, column 21" in result["error"]
    assert "private-fixture" not in result["error"]
    assert "js/data.js" not in result["error"]


def test_original_quote_rule_allows_one_quotable_source(populated_report):
    root, _, data = project.load_project(str(populated_report))
    data["insights"][0]["evidence"].pop()
    write_data(root, data)
    assert project.validate(str(root), stage="structure")["status"] == "structure_valid"


@pytest.mark.parametrize("filename", ["cookies.txt", ".env", "model.bin", "small.pt"])
def test_accidental_private_artifacts_are_rejected(populated_report, filename):
    (populated_report / filename).write_text("private fixture")
    result = project.validate(str(populated_report), stage="structure")
    assert result["status"] == "incomplete"
    assert "private fixture" not in json.dumps(result)


def test_public_article_and_image_slugs_are_not_credentials(tmp_path):
    (tmp_path / "article.html").write_text(
        '<a href="https://publisher.invalid/sk-hynix-hbm-packaging-at-hot-chips-2026/">'
        '<img src="https://publisher.invalid/uploads/sk-hynix-hbm-packaging-slide-02-218x150.jpg"></a>'
    )
    assert project.privacy_errors(tmp_path) == []


@pytest.mark.parametrize(
    "content",
    [
        "sk-abcdefghijklmnopqrstuvwxyz0123456789",
        "https://publisher.invalid/files/sk-abcdefghijklmnopqrstuvwxyz0123456789",
        "https://publisher.invalid/?key=sk-abcdefghijklmnopqrstuvwxyz0123456789",
        "https://publisher.invalid/?key=sk-several-short-readable-words",
        "https://publisher.invalid/?key=https://other.invalid/sk-several-short-readable-words",
        "https://publisher.invalid/sk-several-short-readable-words/?key=sk-several-short-readable-words",
        "https://publisher.invalid/sk-proj-several-short-readable-words/",
        "https://publisher.invalid/sk-svcacct-several-short-readable-words/",
        "sk-several-short-readable-words",
        "-----BEGIN PRIVATE KEY-----",
        "# Netscape HTTP Cookie File",
    ],
)
def test_credential_markers_remain_blocked(tmp_path, content):
    (tmp_path / "source.txt").write_text(content)
    errors = project.privacy_errors(tmp_path)
    assert any("credential-like" in error for error in errors)
    assert content not in json.dumps(errors)


def test_configured_secret_matching_public_slug_remains_blocked(tmp_path, monkeypatch):
    secret = "sk-several-short-readable-words"
    monkeypatch.setenv("DASHSCOPE_API_KEY", secret)
    (tmp_path / "source.html").write_text(f'<a href="https://publisher.invalid/{secret}/">Article</a>')
    errors = project.privacy_errors(tmp_path)
    assert any("configured secret" in error for error in errors)
    assert secret not in json.dumps(errors)


def test_provider_failure_is_redacted():
    @guarded
    def fails(_):
        raise RuntimeError("Bearer PRIVATE-TOKEN https://private-fixture.invalid/v1")

    result = fails({})[0]["text"]
    assert "RuntimeError" in result
    assert "PRIVATE-TOKEN" not in result
    assert "private-fixture" not in result


@pytest.mark.parametrize("code", [13, 28, 30])
def test_local_filesystem_failure_retains_errno_without_private_details(code):
    @guarded
    def fails(_):
        raise OSError(code, "Bearer PRIVATE-TOKEN", "/private-fixture/credentials.env")

    result = json.loads(fails({})[0]["text"])
    assert result["status"] == "failed" and result["errno"] == code
    assert result["http_status"] is None
    assert "PRIVATE-TOKEN" not in json.dumps(result)
    assert "private-fixture" not in json.dumps(result)


def test_frame_crop_uses_display_coordinates(tmp_path, sample_video):
    result = media.extract_frame(sample_video, 1, str(tmp_path / "crop.png"), [0, 0, 500, 1000])
    assert result["size"] == [80, 120]
    assert Path(result["path"]).is_file()
    with pytest.raises(InputError):
        media.extract_frame(sample_video, 7, str(tmp_path / "beyond.png"), None)
    with pytest.raises(InputError):
        media.extract_frame(sample_video, 1, str(tmp_path / "bad.png"), [500, 0, 100, 1000])


def test_frames_perception_saves_bounded_observations(tmp_path, sample_video, monkeypatch):
    captured = {}

    def completion(**kwargs):
        captured.update(kwargs)
        return "A synthetic moving test pattern.", None

    monkeypatch.setattr(config, "call_omni", completion)
    output = tmp_path / "observed.json"
    result = call(
        "omnidr_read_media",
        file_path=sample_video,
        mode="frames",
        prompt="Describe.",
        start_time=2,
        end_time=100,
        frames=3,
        output_path=str(output),
    )
    assert result["status"] == "observed", result
    assert result["source_interval"] == [2, 6]
    assert result["timestamp_origin_seconds"] == 0
    parts = captured["messages"][0]["content"]
    assert sum(p["type"] == "image_url" for p in parts) == 3
    assert "2.000" in parts[0]["text"]
    saved = output.read_text()
    assert "base64" not in saved
    assert result["source_sha256"] == project.sha256(Path(sample_video))


def test_omni_ranges_and_cleanup(tmp_path, sample_media_av, monkeypatch):
    clip = tmp_path / "temporary.wav"
    captured = {}

    def parts(file_path, mode, fps, pixels, cleanup, max_duration=None, **kwargs):
        captured.update(kwargs)
        clip.write_text("temporary")
        cleanup.append(str(clip))
        return [{"type": "input_audio", "input_audio": {"data": "synthetic"}}]

    monkeypatch.setattr(media, "build_media_parts", parts)
    monkeypatch.setattr(config, "call_omni", lambda **kwargs: ("Spoken test signal.", None))
    result = call(
        "omnidr_read_media", file_path=sample_media_av, mode="audio", prompt="Transcribe.", start_time=1, end_time=2
    )
    assert result["timestamp_origin_seconds"] == 1
    assert captured["start_time"] == 1 and captured["duration"] == 1
    assert captured["preserve_timestamps"] is True
    assert not clip.exists()


@pytest.mark.parametrize("kind", ["web", "video", "image"])
def test_public_search_never_sends_model_credentials(monkeypatch, kind):
    import html

    import requests

    captured = []
    image = {"t": "Fixture", "murl": "https://source.invalid/image.png", "purl": "https://source.invalid/page"}
    page = (
        '<a class="iusc" m="' + html.escape(json.dumps(image), quote=True) + '"></a>'
        if kind == "image"
        else '<li class="b_algo"><h2><a href="https://source.invalid/page">Fixture</a></h2><p>Evidence lead.</p></li>'
    )

    def get(url, **kwargs):
        captured.append((url, kwargs))
        assert set(kwargs["headers"]) == {"User-Agent"}
        return SimpleNamespace(text=page, raise_for_status=lambda: None)

    monkeypatch.setattr(requests.Session, "get", lambda _session, *a, **kw: get(*a, **kw))
    monkeypatch.setattr(
        requests.Session, "post", lambda *a, **kw: pytest.fail("Public search must not submit API requests")
    )
    result = sources.search("test", kind, 1, "example.org", None, market="en-US")
    url, request = captured[0]
    assert url == "https://www.bing.com/" + ("images/search" if kind == "image" else "search")
    assert request["params"]["q"] == "test site:example.org"
    assert request["params"]["mkt"] == "en-US"
    assert result["backend"] == "bing" and len(result["results"]) == 1
    assert "synthetic-test-key" not in json.dumps(result)


def test_no_implicit_model_key_for_search(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "model-only-key")
    from qwen_mm_plugins_omnidr import public_search

    monkeypatch.setattr(public_search, "bing", lambda *args: [])
    result = sources.search("test", "web", 3, None, None)
    assert result["backend"] == "bing"


def test_fetch_size_limit_does_not_publish_partial_file(tmp_path, source_response):
    # Write one 64 KiB chunk before exceeding the limit on the next chunk.
    source_response(b"a" * (128 * 1024))
    output = tmp_path / "source.html"
    with pytest.raises(InputError, match="limit"):
        sources.fetch("https://source.invalid/", str(output), 64 * 1024 + 1)
    assert not output.exists()
    assert not Path(str(output) + ".source.json").exists()


@pytest.mark.parametrize("content_type", ["text/html; charset=utf-8", "application/xhtml+xml"])
def test_direct_html_keeps_original_and_complete_bound_readable_text(tmp_path, source_response, content_type):
    passage = "Measured 42 W at 230 V, not the maximum rating. &amp; Conditions apply."
    original = (
        "<!doctype html><html><head><script>unrelatedNavigationPayload()</script></head><body>"
        + "<p>"
        + passage * 300
        + "</p><table><tr><td>Final row</td><td>7 A</td></tr></table></body></html>"
    ).encode()

    source_response(original, content_type)
    target = tmp_path / "misleading.md"
    fetched = call("omnidr_fetch_source", url="https://publisher.invalid/page", output_path=str(target))
    assert fetched["status"] == "saved" and target.read_bytes() == original
    readable = Path(fetched["readable_path"])
    text = readable.read_text()
    assert text.count("Measured 42 W at 230 V, not the maximum rating.") == 300
    assert "& Conditions apply." in text and "Final row" in text and "7 A" in text
    assert "<script>" not in text and "unrelatedNavigationPayload" not in text
    assert fetched["truncated"] and len(fetched["text"]) == 12000 < len(text)
    receipt = json.loads(Path(fetched["source_record_path"]).read_text())
    assert receipt["source_sha256"] == project.sha256(target)
    assert receipt["readable_sha256"] == project.sha256(readable)
    assert receipt["readable_artifact"] == readable.name and receipt["status"] == "acquired"
    assert receipt["content_type"] == content_type.split(";")[0]


def test_readable_text_collision_stops_before_source_request(tmp_path, monkeypatch):
    import requests

    target = tmp_path / "source.md"
    readable = Path(str(target) + ".readable.txt")
    readable.write_text("Preserved previous evidence")
    monkeypatch.setattr(requests.Session, "get", lambda *a, **kw: pytest.fail("No new fetch may overwrite saved text"))
    with pytest.raises(InputError, match="already exists"):
        sources.fetch("https://publisher.invalid/page", str(target), 1000)
    assert readable.read_text() == "Preserved previous evidence"
    assert not target.exists() and not Path(str(target) + ".source.json").exists()


def test_download_uses_disposable_cookie_copy(tmp_path, sample_video, monkeypatch):
    url = "https://www.youtube.com/watch?v=fixture"
    cookie = tmp_path / "external-cookies.txt"
    cookie.write_text("synthetic-cookie-fixture")
    copied = []

    def fake_run(command, **kwargs):
        assert "--ignore-config" in command and "--no-cache-dir" in command
        copy = Path(command[command.index("--cookies") + 1])
        copied.append(copy)
        assert copy != cookie and copy.read_bytes() == cookie.read_bytes()
        copy.write_text("changed by downloader")
        template = command[command.index("-o") + 1]
        shutil.copyfile(sample_video, template.replace("%(ext)s", "mp4"))

    monkeypatch.setattr(sources, "run", fake_run)
    output = tmp_path / "result.mp4"
    result = sources.download_video(url, str(output), str(cookie))
    assert result["status"] == "downloaded"
    assert cookie.read_text() == "synthetic-cookie-fixture"
    assert not copied[0].exists()
    assert "cookie-fixture" not in json.dumps(result)
    receipt = Path(result["source_record_path"]).read_text()
    assert "cookie-fixture" not in receipt and str(cookie) not in receipt
    assert json.loads(receipt)["url"] == url


def test_download_without_cookie_argument_never_borrows_credentials(tmp_path, sample_video, monkeypatch):
    def fake_run(command, **kwargs):
        assert "--cookies" not in command
        assert "--extractor-args" not in command
        template = command[command.index("-o") + 1]
        shutil.copyfile(sample_video, template.replace("%(ext)s", "mp4"))

    monkeypatch.setattr(sources, "run", fake_run)
    result = sources.download_video("https://video.invalid/watch/1", str(tmp_path / "result.mp4"), None)
    assert result["status"] == "downloaded"


def test_download_does_not_enable_challenge_solvers_or_custom_clients(tmp_path, sample_video, monkeypatch):
    yt_dlp = pytest.importorskip("yt_dlp")

    def fake_run(command, **kwargs):
        # Parse the invocation with the downloader itself, without accessing YouTube.
        options = yt_dlp.parse_options(command[3:]).ydl_opts
        assert not options["js_runtimes"]
        assert not options["remote_components"]
        assert not options["extractor_args"]
        assert options["cachedir"] is False
        assert not options.get("cookiefile")
        template = command[command.index("-o") + 1]
        shutil.copyfile(sample_video, template.replace("%(ext)s", "mp4"))

    monkeypatch.setattr(sources, "run", fake_run)
    result = sources.download_video("https://www.youtube.com/watch?v=fixture", str(tmp_path / "result.mp4"), None)
    assert result["status"] == "downloaded"


@pytest.mark.parametrize(("heights", "expected"), [([360, 720, 1080], 720), ([1080], 1080), ([None], None)])
def test_generic_download_preserves_original_format_fallback(tmp_path, sample_video, monkeypatch, heights, expected):
    youtube_dl = pytest.importorskip("yt_dlp").YoutubeDL
    formats = [
        {
            "format_id": str(height),
            "url": "https://video.invalid/fixture.mp4",
            "height": height,
            "ext": "mp4",
            "protocol": "https",
            "vcodec": "h264",
            "acodec": "aac",
        }
        for height in heights
    ]

    def fake_run(command, **kwargs):
        # Use yt-dlp's actual selector, without opening the synthetic URL.
        with youtube_dl({"quiet": True, "no_warnings": True}) as downloader:
            selector = downloader.build_format_selector(command[command.index("-f") + 1])
            selected = downloader._select_formats(formats, selector)
        assert len(selected) == 1
        assert selected[0]["height"] == expected
        assert command[command.index("--max-filesize") + 1] == "500M"
        template = command[command.index("-o") + 1]
        shutil.copyfile(sample_video, template.replace("%(ext)s", "mp4"))

    monkeypatch.setattr(sources, "run", fake_run)
    result = sources.download_video("https://video.invalid/watch/1", str(tmp_path / "result.mp4"), None)
    assert result["status"] == "downloaded"


def test_generic_download_retains_identity_without_exporting_provider_metadata(tmp_path, sample_video, monkeypatch):
    workdirs = []

    def fake_run(command, **kwargs):
        assert "--write-info-json" in command
        template = command[command.index("-o") + 1]
        artifact = Path(template.replace("%(ext)s", "mp4"))
        workdirs.append(artifact.parent)
        shutil.copyfile(sample_video, artifact)
        artifact.with_suffix(".info.json").write_text(
            json.dumps(
                {
                    "title": "Actual platform title",
                    "uploader": "Original publisher",
                    "url": "https://media.invalid/?token=synthetic-private-value",
                    "http_headers": {"Cookie": "synthetic-private-cookie"},
                }
            )
        )

    monkeypatch.setattr(sources, "run", fake_run)
    result = sources.download_video("https://video.invalid/watch/1", str(tmp_path / "result.mp4"), None)
    receipt = json.loads(Path(result["source_record_path"]).read_text())
    assert result["title"] == receipt["title"] == "Actual platform title"
    assert result["uploader"] == receipt["uploader"] == "Original publisher"
    assert "synthetic-private" not in json.dumps([result, receipt])
    assert not workdirs[0].exists()


@pytest.mark.parametrize(
    ("stderr", "diagnosis"),
    [
        ("ERROR: connection timed out", "network connection"),
        ("ERROR: Sign in to confirm you're not a bot", "authentication"),
        ("ERROR: signature challenge failed", "extraction challenge"),
        ("No module named yt_dlp", "Install yt-dlp"),
        ("ERROR: HTTP Error 403", "rejected or rate-limited"),
        ("ERROR: Requested format is not available", "downloadable video format"),
        ("WARNING: JavaScript runtime missing\nERROR: connection timed out", "network connection"),
    ],
)
def test_download_failure_is_actionable_without_exposing_stderr(stderr, diagnosis, monkeypatch):
    from qwen_mm_plugins_omnidr import common

    private = "https://private-fixture.invalid/?credential=synthetic-private-token"
    monkeypatch.setattr(common, "find_tool", lambda command: command)
    monkeypatch.setattr(
        common.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr=stderr + " " + private)
    )
    with pytest.raises(InputError) as caught:
        common.run(["unused-python", "-m", "yt_dlp"], failure_hint=sources.download_failure_hint)
    assert diagnosis in str(caught.value)
    assert "private-fixture" not in str(caught.value)
    assert "synthetic-private-token" not in str(caught.value)


@pytest.mark.skipif(importlib.util.find_spec("pypdfium2") is None, reason="PDFium optional test dependency missing")
def test_pdf_read_and_render_in_isolated_worker(tmp_path):
    import pypdfium2 as pdfium

    source = tmp_path / "sample.pdf"
    with pdfium.PdfDocument.new() as pdf:
        page = pdf.new_page(200, 100)
        page.close()
        pdf.save(source)
    result = call("omnidr_read_document", file_path=str(source), output_path=str(tmp_path / "pages.txt"), render=True)
    assert result["status"] == "read", result
    assert result["total_pages"] == 1
    assert Path(result["pages"][0]["image"]).is_file()


@pytest.fixture
def long_synthetic_pdf(tmp_path):
    pdfium = pytest.importorskip("pypdfium2")
    source = tmp_path / "synthetic-25-pages.pdf"
    with pdfium.PdfDocument.new() as pdf:
        for _ in range(25):
            pdf.new_page(200, 100).close()
        pdf.save(source)
    return source


@pytest.mark.parametrize("start,end", [(1, None), (3, None), (3, 25)])
def test_pdf_text_can_cover_all_remaining_pages(tmp_path, long_synthetic_pdf, start, end):
    result = call(
        "omnidr_read_document",
        file_path=str(long_synthetic_pdf),
        output_path=str(tmp_path / "full.txt"),
        start_page=start,
        end_page=end,
    )
    assert result["status"] == "read", result
    assert [p["page"] for p in result["pages"]] == list(range(start, 26))
    record = json.loads(Path(result["reading_record"]).read_text())
    assert record["pages"] == result["pages"]
    assert "Page 25" in Path(result["text_path"]).read_text()
    assert not list(tmp_path.glob("*.png"))


def test_pdf_rendering_keeps_its_page_bound(tmp_path, long_synthetic_pdf):
    refused = tmp_path / "too-many.txt"
    result = call(
        "omnidr_read_document", file_path=str(long_synthetic_pdf), output_path=str(refused), end_page=21, render=True
    )
    assert result["status"] == "failed" and not refused.exists()
    result = call(
        "omnidr_read_document",
        file_path=str(long_synthetic_pdf),
        output_path=str(tmp_path / "rendered.txt"),
        render=True,
    )
    assert result["status"] == "read"
    assert [p["page"] for p in result["pages"]] == list(range(1, 11))
    assert len(list(tmp_path.glob("*.png"))) == 10


def test_long_pdf_text_stays_complete_on_disk_with_bounded_tool_preview(tmp_path, monkeypatch):
    pdfium = pytest.importorskip("pypdfium2")
    source = tmp_path / "synthetic.pdf"
    source.write_bytes(b"synthetic mocked PDF")

    class Document:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __len__(self):
            return 25

        def __getitem__(self, index):
            text = f"Synthetic paragraph on page {index + 1}. " * 100
            return SimpleNamespace(
                get_textpage=lambda: SimpleNamespace(get_text_range=lambda: text, close=lambda: None),
                close=lambda: None,
            )

    monkeypatch.setattr(pdfium, "PdfDocument", lambda _: Document())
    result = media.read_pdf_worker({"file_path": str(source), "output_path": str(tmp_path / "text.txt")})
    assert result["text_in_preview"] is False
    assert len(json.dumps(result)) < 5000
    record = json.loads(Path(result["reading_record"]).read_text())
    assert len(record["pages"]) == 25
    assert all(p["text"] for p in record["pages"])
    text = Path(result["text_path"]).read_text()
    assert "Synthetic paragraph on page 25." in text
    assert record["source_sha256"] == project.sha256(source)


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="MCP SDK unavailable")
def test_real_stdio_handshake_discovery_and_media_call(sample_video):
    async def action(session):
        listed = await session.list_tools()
        assert {t.name for t in listed.tools} == {s.name for s in plugin.SPECS}
        response = await session.call_tool("omnidr_media_info", {"file_path": sample_video})
        assert not response.isError
        return json.loads(response.content[0].text)

    result = mcp_call(str(SERVER), action)
    assert result["duration"] == 6


@pytest.fixture
def full_report(populated_report, sample_video, monkeypatch):
    """Synthetic delivery ledger: exercises gates, not factual truth or real research."""
    from qwen_mm_plugins_omnidr import audit, reading

    root = populated_report
    _, _, data = project.load_project(str(root))
    monkeypatch.setattr(
        config,
        "call_omni",
        lambda **_: ("Synthetic visual observations with source timestamps.", None),
    )
    read = reading.read_video(sample_video, str(root / "_work/source"))
    assert read["status"] == "read", read
    for i in range(3, 26):
        path = root / f"_assets/docs/S{i}.txt"
        path.write_text(
            f"Synthetic source {i}: independently authored fixture text, not evidence about the real world."
        )
        item = {
            "id": f"S{i}",
            "type": "web",
            "title": f"Synthetic source {i}",
            "url": f"https://publisher{i}.invalid/article",
            "summary": path.read_text(),
            "anchorId": "A1",
            "media": str(path.relative_to(root)),
        }
        if 3 <= i <= 7:
            video = root / f"_assets/videos/S{i}.mp4"
            shutil.copyfile(sample_video, video)
            item.update(
                type="video",
                url=f"https://www.youtube.com/watch?v=synthetic{i}"
                if i < 5
                else f"https://www.bilibili.com/video/BVsynthetic{i}/",
                media=str(video.relative_to(root)),
                perception="_work/source/reading.json",
            )
        if 8 <= i <= 10:
            item["primary"] = True
        data["research"].append(item)
    for item in data["research"]:
        item["publisher"] = "Synthetic independent publisher " + item["id"]
    crop = root / "anchors/A1.png"
    media.extract_frame(sample_video, 1, str(crop), [0, 0, 500, 1000])
    data["segments"][0]["caption"].append({"type": "image", "src": "anchors/A1.png", "cap": "Synthetic crop"})
    data["research"][10].update(type="image", media="anchors/A1.png")
    original = data["insights"][0]
    data["insights"].append(
        {
            "kind": "timeliness",
            "title": "Synthetic dated check",
            "videoClaim": "A synthetic fixture claim.",
            "currentFact": "2026-10-10: this is a synthetic statement, not a real-world correction [S1][S2].",
            "sources": ["S1", "S2"],
            "evidence": original["evidence"],
        }
    )
    ledger = {
        "main_reader": "author",
        "subagents_available": True,
        "source_readings": ["_work/source/reading.json"],
        "primary_readings": [],
        "visual_anchors": [
            {"anchor_id": "A1", "crop": "anchors/A1.png", "inspected": True, "identification_sources": ["S11"]}
        ],
        "claims": [],
        "render_review": {
            "screenshot": "anchors/A1.png",
            "images": True,
            "playback": True,
            "bidirectional_seeking": True,
            "citations": True,
            "legibility": True,
        },
    }
    for item in data["research"]:
        if item.get("primary"):
            notes = root / f"_work/{item['id']}-notes.md"
            notes.write_text(
                "Synthetic complete-reader notes for a generated source. This is a testing artifact, not a claim of actual research.\n"
            )
            ledger["primary_readings"].append(
                {
                    "source_id": item["id"],
                    "reader": "reader-" + item["id"],
                    "scope": "full",
                    "source_sha256": project.sha256(root / item["media"]),
                    "notes": str(notes.relative_to(root)),
                }
            )
    for item in audit.claim_records(data):
        notes = root / f"_work/review-{len(ledger['claims'])}.md"
        notes.write_text("Synthetic reviewer result; fixture quotes agree with the synthetic fixture sources.")
        ledger["claims"].append(
            {
                **item,
                "method": "independent",
                "reviewer": "different-context-reader",
                "verdict": "SUPPORTED",
                "opened_sources": item["sources"],
                "notes": str(notes.relative_to(root)),
            }
        )
    write_data(root, data)
    (root / "_work/research-review.json").write_text(json.dumps(ledger))
    return root


@pytest.mark.parametrize("legacy_metadata", [False, True])
def test_original_delivery_floor_and_review_contract(full_report, legacy_metadata):
    if legacy_metadata:
        image = full_report / "anchors/A1.png"
        project.frame_metadata_path(image).rename(image.with_suffix(".png.json"))
    result = project.validate(str(full_report))
    assert result["status"] == "validated", result
    assert result["delivery_ready"]
    structural = project.validate(str(full_report), stage="structure")
    assert structural["status"] == "structure_valid" and not structural["delivery_ready"]


@pytest.mark.parametrize("segment_level", [False, True])
@pytest.mark.parametrize(
    ("title", "dated"),
    [
        ("截至2031-04-09，合成测试结论仍然成立", True),
        ("As of 2031-04-09, this synthetic result remains current", True),
        ("截至2031/04/09，合成测试使用了不同日期格式", False),
        ("Synthetic identifier 12031-04-09 has no ISO date", False),
    ],
)
def test_dated_review_accepts_iso_dates_adjacent_to_chinese(full_report, segment_level, title, dated):
    from qwen_mm_plugins_omnidr import audit

    _, _, data = project.load_project(str(full_report))
    claim = data["insights"].pop()
    claim.update(title=title, currentFact="Synthetic current finding [S1][S2].")
    target = data["segments"][0].setdefault("insights", []) if segment_level else data["insights"]
    target.append(claim)
    write_data(full_report, data)
    ledger_path = full_report / "_work/research-review.json"
    ledger = json.loads(ledger_path.read_text())
    # Rebind this synthetic fixture's reviews to the new fixture claims and locations.
    for review, record in zip(ledger["claims"], audit.claim_records(data), strict=True):
        review.update(record)
    ledger_path.write_text(json.dumps(ledger))

    result = project.validate(str(full_report))
    if dated:
        assert result["delivery_ready"], result
    else:
        assert not result["delivery_ready"]
        assert any("Include a dated timeliness check" in error for error in result["errors"])


@pytest.mark.parametrize("scope", ["full, all pages and figures read", "full except an unread appendix"])
def test_primary_scope_error_is_actionable_without_relaxing_completion(full_report, scope):
    path = full_report / "_work/research-review.json"
    ledger = json.loads(path.read_text())
    ledger["primary_readings"][0]["scope"] = scope
    path.write_text(json.dumps(ledger))
    before = path.read_bytes()
    result = project.validate(str(full_report))
    assert not result["delivery_ready"]
    assert any("primary_readings[0].scope must be exactly 'full'" in error for error in result["errors"])
    assert path.read_bytes() == before
    ledger["primary_readings"][0]["scope"] = "full"
    path.write_text(json.dumps(ledger))
    assert project.validate(str(full_report))["delivery_ready"]


@pytest.mark.parametrize(
    "defect",
    [
        "thin",
        "one_platform",
        "missing_audio_pass",
        "wrong_audio_manifest",
        "partial_video",
        "repeated_interval",
        "missing_primary",
        "same_publisher",
        "missing_crop",
        "unused_crop",
        "self_review",
        "stale_claim",
        "no_render_check",
    ],
)
def test_delivery_rejects_missing_original_gates(full_report, defect):
    root, _, data = project.load_project(str(full_report))
    ledger_path = root / "_work/research-review.json"
    ledger = json.loads(ledger_path.read_text())
    if defect == "thin":
        data["research"].pop()
    elif defect == "one_platform":
        for source in data["research"]:
            if source["type"] == "video":
                source["url"] = "https://www.youtube.com/watch?v=" + source["id"]
    elif defect == "missing_audio_pass":
        (root / "_work/source/clip-0000.tr.json").unlink()
    elif defect == "wrong_audio_manifest":
        path = root / "_work/source/reading.json"
        reading = json.loads(path.read_text())
        reading["has_audio"] = True  # This source actually has no audio stream.
        path.write_text(json.dumps(reading))
        transcript = root / "_work/source/clip-0000.tr.json"
        record = json.loads(transcript.read_text())
        record["status"] = "transcribed"
        transcript.write_text(json.dumps(record))
    elif defect in {"partial_video", "repeated_interval"}:
        path = root / "_work/source/reading.json"
        reading = json.loads(path.read_text())
        if defect == "partial_video":
            reading["clips"][0]["end"] = 2
        else:
            reading["clips"].append(dict(reading["clips"][0]))
        path.write_text(json.dumps(reading))
    elif defect == "missing_primary":
        ledger["primary_readings"].pop()
    elif defect == "same_publisher":
        for source in data["research"]:
            source["publisher"] = "same syndication owner"
    elif defect == "missing_crop":
        project.frame_metadata_path(root / "anchors/A1.png").unlink()
    elif defect == "unused_crop":
        data["segments"][0]["caption"] = [data["segments"][0]["caption"][0]]
    elif defect == "self_review":
        ledger["claims"][0]["reviewer"] = "author"
    elif defect == "stale_claim":
        data["insights"][0]["body"] += " A substantively new unsupported conclusion."
    else:
        ledger["render_review"]["bidirectional_seeking"] = False
    write_data(root, data)
    ledger_path.write_text(json.dumps(ledger))
    result = project.validate(str(root))
    assert result["status"] == "incomplete", result


def test_headless_self_check_is_explicit(full_report):
    path = full_report / "_work/research-review.json"
    ledger = json.loads(path.read_text())
    ledger["subagents_available"] = False
    ledger["review_limitation"] = "Host has no subagents; author reopened every source. No independent review."
    for review in ledger["claims"]:
        review.update(method="self-check", reviewer="author")
    path.write_text(json.dumps(ledger))
    assert project.validate(str(full_report))["status"] == "validated"


@pytest.mark.parametrize("sampling", [{}, {"frames": 48, "frame_height": 480}])
def test_reader_parallelism_resume_and_failed_pass_retry(tmp_path, sample_media_av, monkeypatch, sampling):
    import threading

    from qwen_mm_plugins_omnidr import reading, transcription

    barrier = threading.Barrier(2)
    counts = {"visual": 0, "audio": 0}
    digest = project.sha256(Path(sample_media_av))

    def visual(arguments):
        counts["visual"] += 1
        assert arguments["frames"] == sampling.get("frames", 32)
        assert arguments["frame_height"] == sampling.get("frame_height", 320)
        barrier.wait(timeout=5)
        record = {
            "status": "observed",
            "mode": "frames",
            "text": "Synthetic visual pass",
            "source_sha256": digest,
            "source_interval": [0, 3],
        }
        Path(arguments["output_path"]).write_text(json.dumps(record))
        return record

    def audio(file_path, output_path, start, end, **kwargs):
        counts["audio"] += 1
        if counts["audio"] == 1:
            barrier.wait(timeout=5)
            raise RuntimeError("synthetic temporary service failure")
        record = {"status": "transcribed", "segments": [], "source_sha256": digest, "source_interval": [0, 3]}
        Path(output_path).write_text(json.dumps(record))
        return record

    monkeypatch.setattr(media, "perceive", visual)
    monkeypatch.setattr(transcription, "transcribe", audio)
    output = tmp_path / "reading"
    first = reading.read_video(sample_media_av, str(output), **sampling)
    assert first["status"] == "incomplete"
    saved_visual = (output / "clip-0000.visual.json").read_bytes()
    # A different requested detail must not repeat or relabel a completed legacy
    # observation; only the failed audio pass runs again.
    second = reading.read_video(sample_media_av, str(output), frames=48, frame_height=480)
    assert second["status"] == "read" and counts == {"visual": 1, "audio": 2}
    assert (output / "clip-0000.visual.json").read_bytes() == saved_visual
    reading.read_video(sample_media_av, str(output))
    assert counts == {"visual": 1, "audio": 2}


@pytest.mark.parametrize("case", ["overlap", "alias", "different_source", "adjacent", "failed"])
def test_reader_does_not_repeat_completed_report_asr_in_a_new_directory(tmp_path, sample_media_av, monkeypatch, case):
    from qwen_mm_plugins_omnidr import policy, reading, transcription

    root = tmp_path / "report"
    project.prepare(sample_media_av, str(root), "ASR reuse fixture", "en", "t01")
    digest = project.sha256(Path(sample_media_av))
    cached_digest = "a" * 64 if case in {"alias", "different_source"} else digest
    if case == "alias":
        policy.register_aliases(digest, cached_digest)
    cached = root / "_work/first/clip-0000.tr.json"
    cached.parent.mkdir(parents=True)
    cached.write_text(
        json.dumps(
            {
                "status": "failed" if case == "failed" else "transcribed",
                "source_sha256": cached_digest,
                "source_interval": [0, 1 if case == "adjacent" else 1.5],
                "segments": [{"start": 0.1, "end": 0.7, "text": "Saved speech"}],
            }
        )
    )
    before = cached.read_bytes()
    calls = []

    def visual(arguments):
        calls.append("visual")
        result = {"status": "observed", "text": "Fixture", "source_sha256": digest, "source_interval": [1, 2]}
        Path(arguments["output_path"]).write_text(json.dumps(result))
        return result

    def audio(file_path, output_path, start, end, **kwargs):
        calls.append("audio")
        result = {"status": "transcribed", "segments": [], "source_sha256": digest, "source_interval": [start, end]}
        Path(output_path).write_text(json.dumps(result))
        return result

    monkeypatch.setattr(media, "perceive", visual)
    monkeypatch.setattr(transcription, "transcribe", audio)
    output = root / "_work/new-directory"
    if case in {"overlap", "alias"}:
        with pytest.raises(InputError, match="Changing output directories must not repeat"):
            reading.read_video(sample_media_av, str(output), 1, 2)
        assert not calls
    else:
        result = reading.read_video(sample_media_av, str(output), 1, 2)
        assert result["status"] == "read" and sorted(calls) == ["audio", "visual"]
    assert cached.read_bytes() == before


def test_frame_sampling_records_actual_resolution_and_decoded_timestamps(tmp_path, sample_media_av, monkeypatch):
    captured = {}

    def extract(path, times, height, width):
        captured.update(times=times, height=height, width=width)
        # Simulate partial decoding without sending any real media/model request.
        return [(times[0], "synthetic"), (times[4], "synthetic")]

    monkeypatch.setattr(media, "extract_frames_by_seeking", extract)
    monkeypatch.setattr(
        media,
        "get_video_info",
        lambda _: {
            "height": 240,
            "width": 320,
            "duration": 3,
            "native_fps": 24,
        },
    )
    monkeypatch.setattr(
        config,
        "call_omni",
        lambda **_: ("Synthetic observation", None),
    )
    path = tmp_path / "visual.json"
    result = call(
        "omnidr_read_media",
        file_path=sample_media_av,
        mode="frames",
        frames=48,
        frame_height=480,
        start_time=1,
        end_time=2,
        prompt="Synthetic",
        output_path=str(path),
    )
    assert result["status"] == "observed"
    assert len(captured["times"]) == 48
    assert (captured["height"], captured["width"]) == (240, 320)
    assert result["sampling"] == {
        "requested_frames": 48,
        "requested_height": 480,
        "height": 240,
        "width": 320,
        "timestamps": [1, 1 + 4 / 48],
    }
    assert json.loads(path.read_text())["sampling"] == result["sampling"]


@pytest.mark.parametrize("budget", [None, 200704, 3000000, 4000000])
def test_image_read_preserves_original_pixels_unless_downsampling_is_requested(tmp_path, monkeypatch, budget):
    import base64
    import io

    from PIL import Image

    source = tmp_path / "synthetic-page.png"
    Image.new("RGB", (1500, 2100), "white").save(source)
    submitted = []

    def chat(**kwargs):
        url = kwargs["messages"][0]["content"][0]["image_url"]["url"]
        submitted.append(base64.b64decode(url.split(",", 1)[1]))
        return "Synthetic page", None

    monkeypatch.setattr(config, "call_omni", chat)
    output = tmp_path / "observation.json"
    options = {} if budget is None else {"max_pixels": budget}
    result = call(
        "omnidr_read_media", file_path=str(source), mode="image", prompt="Synthetic", output_path=str(output), **options
    )
    assert result["status"] == "observed", result
    with Image.open(io.BytesIO(submitted[0])) as sent:
        if budget is None or budget >= 1500 * 2100:
            assert submitted[0] == source.read_bytes()
            assert sent.size == (1500, 2100)
        else:
            assert sent.width * sent.height <= budget
            assert sent.width < 1500 and sent.height < 2100
        assert result["image_size"] == list(sent.size)
    saved = json.loads(output.read_text())
    assert saved["image_size"] == result["image_size"]
    assert saved["max_pixels"] == budget


def test_long_video_plan_has_exact_300_second_source_ranges(tmp_path, monkeypatch):
    from qwen_mm_plugins_omnidr import reading

    source = tmp_path / "synthetic.mp4"
    source.write_bytes(b"test fixture")
    monkeypatch.setattr(reading, "probe", lambda _: {"duration": 601.7, "streams": [{"codec_type": "video"}]})
    result = reading.plan(str(source))
    assert [(c["start"], c["end"]) for c in result["clips"]] == [(0, 300), (300, 600), (600, 601.7)]


def test_asr_source_timestamps_and_no_silent_empty_success(tmp_path, sample_media_av, monkeypatch):
    from qwen_mm_plugins_omnidr import transcription

    monkeypatch.setattr(
        media,
        "perceive",
        lambda args: {
            "text": json.dumps({"segments": [{"start": 0.1, "end": 0.7, "text": "synthetic speech"}], "language": "en"})
        },
    )
    result = transcription.transcribe(sample_media_av, str(tmp_path / "tr.json"), 1, 2)
    assert result["segments"][0]["start"] == 1.1 and result["timestamp_origin_seconds"] == 0
    assert (tmp_path / "tr.txt").read_text().startswith("[1.100-1.700]")
    with pytest.raises(InputError):
        transcription.parse_segments("[]", 10)
    with pytest.raises(InputError):
        transcription.parse_segments('{"segments":[{"start":10.1,"end":10.2,"text":"outside"}]}', 10)
    assert transcription.parse_segments('{"segments":[],"speech_detected":false}', 10)[0] == []


@pytest.mark.parametrize("asr", [True, False])
def test_media_request_preserves_asr_roles_and_provider_defaults(tmp_path, sample_media_av, monkeypatch, asr):
    import re

    from qwen_mm_plugins_omnidr import transcription

    captured = []
    reply = '{"segments":[{"start":0.1,"end":0.7,"text":"synthetic speech"}],"language":"en"}'

    def complete(**kwargs):
        captured.append(kwargs)
        return reply, None

    def parts(*args, **kwargs):
        assert kwargs["preserve_timestamps"] is True and kwargs["duration"] == 1
        return [{"type": "input_audio", "input_audio": {"data": "data:;base64,ZmFrZQ==", "format": "wav"}}]

    monkeypatch.setattr(config, "call_omni", complete)
    monkeypatch.setattr(media, "build_media_parts", parts)
    hint = "Synthetic domain terms; this is user-provided context."
    if asr:
        result = transcription.transcribe(
            sample_media_av, str(tmp_path / "tr.json"), 0.5, 1.5, language="en", initial_prompt=hint
        )
        assert result["segments"][0]["start"] == pytest.approx(0.6) and result["fallback"] is False
    else:
        media.perceive(dict(file_path=sample_media_av, mode="audio", prompt=hint, start_time=1, end_time=2))
    assert len(captured) == 1
    request = captured[0]
    messages = request["messages"]
    assert "max_tokens" not in request
    assert request["model"] == "fixture-omni-model" and "timeout" not in request
    user_text = next(p["text"] for p in messages[-1]["content"] if p["type"] == "text")
    if asr:
        assert request["temperature"] == 0.0
        assert [m["role"] for m in messages] == ["system", "user"]
        assert messages[0]["content"] == transcription.PROMPT
        assert hint not in messages[0]["content"]
        assert "5–10 seconds" in messages[0]["content"] and "throughout the clip" in messages[0]["content"]
        assert float(re.search(r"Clip duration: ([0-9.]+) seconds", user_text)[1]) == 1.0
        assert "0 <= start < end <= 1.000000" in user_text
    else:
        assert "temperature" not in request
        assert [m["role"] for m in messages] == ["user"]
    assert hint in user_text


def test_reader_plan_avoids_an_orphan_subsecond_tail_without_gaps(tmp_path, monkeypatch):
    from qwen_mm_plugins_omnidr import reading

    source = tmp_path / "synthetic.mp4"
    source.write_bytes(b"fixture")
    monkeypatch.setattr(reading, "probe", lambda _: {"duration": 600.1175, "streams": [{"codec_type": "video"}]})
    clips = reading.plan(str(source))["clips"]
    assert [(c["start"], c["end"]) for c in clips] == [(0, 300), (300, 599.1175), (599.1175, 600.1175)]
    assert all(0 < c["end"] - c["start"] <= 300 for c in clips)


@pytest.mark.parametrize("missing_tail_visual", [False, True])
def test_reader_resume_preserves_legacy_asr_and_recovers_only_missing_visual(
    tmp_path, monkeypatch, missing_tail_visual
):
    from qwen_mm_plugins_omnidr import audit, reading, transcription

    source = tmp_path / "synthetic.mp4"
    source.write_bytes(b"fixture")
    monkeypatch.setattr(reading, "probe", lambda _: {"duration": 300.1175, "streams": [{"codec_type": "video"}]})
    planned = reading.plan(str(source))
    planned["clips"] = [
        {"id": "clip-0000", "start": 0, "end": 300},
        {"id": "clip-0001", "start": 300, "end": 300.1175},
    ]
    output = tmp_path / "reading"
    output.mkdir()
    (output / "reading-plan.json").write_text(json.dumps(planned))
    saved = {}
    for clip in planned["clips"]:
        provenance = {"source_sha256": planned["source_sha256"], "source_interval": [clip["start"], clip["end"]]}
        for suffix, record in [
            ("visual", {"status": "observed", "mode": "frames", "text": "Synthetic saved observation"}),
            ("tr", {"status": "no_audio", "segments": []}),
        ]:
            if missing_tail_visual and suffix == "visual" and clip["id"] == "clip-0001":
                continue
            path = output / (clip["id"] + "." + suffix + ".json")
            path.write_text(json.dumps({**record, **provenance}))
            saved[path] = path.read_bytes()

    def unexpected(*args, **kwargs):
        pytest.fail("A completed legacy pass must not be repeated")

    visual_calls = []

    def recover(args):
        assert missing_tail_visual
        assert [args["start_time"], args["end_time"]] == [299.1175, 300.1175]
        visual_calls.append(args)
        result = {
            "status": "observed",
            "mode": "frames",
            "text": "Synthetic saved tail observation",
            "source_sha256": planned["source_sha256"],
            "source_interval": [args["start_time"], args["end_time"]],
        }
        Path(args["output_path"]).write_text(json.dumps(result))
        return result

    monkeypatch.setattr(media, "perceive", recover)
    monkeypatch.setattr(transcription, "transcribe", unexpected)
    result = reading.read_video(str(source), str(output))
    assert result["status"] == "read"
    assert json.loads((output / "reading.json").read_text())["clips"][-1]["start"] == 300
    assert all(path.read_bytes() == contents for path, contents in saved.items())
    assert len(visual_calls) == int(missing_tail_visual)
    assert audit.reading_complete(tmp_path, "reading/reading.json", planned["source_sha256"], 300.1175, False)
    reading.read_video(str(source), str(output))
    assert len(visual_calls) == int(missing_tail_visual)
    if missing_tail_visual:
        manifest = json.loads((output / "reading.json").read_text())
        manifest["clips"][-1]["visual_interval"] = [100, 300.1175]
        (output / "reading.json").write_text(json.dumps(manifest))
        assert not audit.reading_complete(tmp_path, "reading/reading.json", planned["source_sha256"], 300.1175, False)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is required")
def test_reader_tail_plan_keeps_decodable_frames_with_audio_padding(tmp_path, monkeypatch):
    from qwen_mm_plugins_omnidr import reading
    from qwen_mm_plugins_omnidr.common import run

    source = tmp_path / "padded.mp4"
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=32x32:r=25:d=300",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "300.1175",
            "-c:v",
            "mpeg4",
            "-q:v",
            "15",
            "-c:a",
            "aac",
            str(source),
        ],
        timeout=30,
    )
    duration = project.probe(source)["duration"]
    assert 300 < duration < 301
    calls = []

    def chat(**kwargs):
        calls.append(kwargs)
        return "Synthetic blue frame", None

    monkeypatch.setattr(config, "call_omni", chat)
    arguments = {"file_path": str(source), "mode": "frames", "prompt": "Describe the synthetic fixture"}
    with pytest.raises(InputError, match="No video frames"):
        media.perceive({**arguments, "start_time": 300, "end_time": duration})
    last = reading.plan(str(source))["clips"][-1]
    result = media.perceive({**arguments, "start_time": last["start"], "end_time": last["end"]})
    assert result["status"] == "observed" and len(calls) == 1
    assert result["source_interval"] == [last["start"], duration]


def test_missing_model_key_stops_before_submission(monkeypatch):
    from qwen_mm_plugins_omnidr import config

    monkeypatch.delenv("DASHSCOPE_API_KEY")
    monkeypatch.setattr(config, "call_omni", lambda **kw: pytest.fail("A missing key must prevent submission"))
    with pytest.raises(InputError, match="DASHSCOPE_API_KEY"):
        config.chat("Question")


@pytest.mark.parametrize("override", [None, "explicit-omni-model"])
def test_model_settings_resolve_only_three_service_fields(monkeypatch, override):
    from qwen_mm_plugins_omnidr import config

    values = {
        "DASHSCOPE_API_KEY": "synthetic-key",
        "DASHSCOPE_BASE_URL": "https://provider.invalid/v1/",
        "QWEN_MM_API_OMNI_MODEL": "fixture-omni-model",
    }
    requested = []

    def get_env(name, *args):
        assert name in values, f"Unexpected runtime configuration: {name}"
        requested.append(name)
        return values[name]

    monkeypatch.setattr(config, "get_env", get_env)
    assert config.omni_settings(override) == (
        "https://provider.invalid/v1",
        "synthetic-key",
        override or "fixture-omni-model",
    )
    assert set(requested) == (set(values) if override is None else {"DASHSCOPE_BASE_URL", "DASHSCOPE_API_KEY"})


@pytest.mark.parametrize("engine", ["auto", "shared"])
def test_transcription_uses_one_omni_pass_and_absolute_timestamps(tmp_path, sample_media_av, monkeypatch, engine):
    from qwen_mm_plugins_omnidr import transcription

    captured = []

    def perceive(arguments):
        captured.append(arguments)
        return {"text": '{"segments":[{"start":0.1,"end":0.7,"text":"Test speech"}],"language":"en"}'}

    monkeypatch.setattr(media, "perceive", perceive)
    result = transcription.transcribe(sample_media_av, str(tmp_path / "transcript.json"), 1, 2, engine=engine)
    assert len(captured) == 1 and captured[0]["_asr"] is True
    assert captured[0]["mode"] == "audio" and "backend" not in captured[0]
    assert result["engine"] == "shared" and result["fallback"] is False and result["fallback_error"] is None
    assert result["segments"][0]["start"] == 1.1


@pytest.mark.parametrize("failure", ["http", "filesystem", "input", "asr"])
def test_failed_asr_keeps_diagnostics_private_and_publishes_no_transcript(
    tmp_path, sample_media_av, monkeypatch, failure
):
    from shared import isolated_worker

    error = OSError(30, "PRIVATE-TOKEN", "/private-fixture/journal")
    if failure == "http":
        error = RuntimeError("PRIVATE-TOKEN https://private-fixture.invalid/v1")
        error.status_code = 429
    elif failure == "input":
        error = InputError("The selected media is invalid.")

    def fail(_):
        if failure == "asr":
            return {"text": '[{"start":0.1,"end":20,"text":"PRIVATE-TOKEN https://private-fixture.invalid/v1"}]'}
        raise error

    monkeypatch.setattr(media, "perceive", fail)
    monkeypatch.setattr(
        isolated_worker,
        "run_isolated",
        lambda *a, **kw: pytest.fail("ASR must not switch engines after a failed request"),
    )
    output = tmp_path / "failed.json"
    result = call("omnidr_transcribe", file_path=sample_media_av, output_path=str(output), start_time=1, end_time=2)
    assert result["status"] == "failed"
    if failure in {"http", "filesystem"}:
        assert result["error"] == ("RuntimeError" if failure == "http" else "OSError")
        assert result["http_status"] == (429 if failure == "http" else None)
        assert result["errno"] == (30 if failure == "filesystem" else None)
    elif failure == "asr":
        assert "segment[0]" in result["error"] and "end exceeds" in result["error"]
    assert "PRIVATE-TOKEN" not in json.dumps(result) and "private-fixture" not in json.dumps(result)
    assert not output.exists() and not output.with_suffix(".txt").exists()


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        ("```json private-fixture-token", "malformed JSON code fence"),
        ("private-fixture-token", "invalid JSON"),
        ('{"segments":"private-fixture-token"}', "segments must be an array"),
        ('{"segments":[]}', "empty segments require"),
        ('[{"start":"private-fixture-token","end":2,"text":"test"}]', "numeric seconds"),
        ('[{"start":0,"end":30,"text":"private-fixture-token"}]', "end exceeds"),
        ('[{"start":10,"end":11,"text":"private-fixture-token"}]', "inside the clip"),
        ('[{"start":0,"end":1,"text":""}]', "nonempty string"),
        ('[{"start":2,"end":3,"text":"a"},{"start":1,"end":2,"text":"b"}]', "ordered by start time"),
    ],
)
def test_asr_invalid_result_locates_constraint_without_echoing_response(response, reason):
    from qwen_mm_plugins_omnidr.transcription import ASRValidationError, parse_segments

    with pytest.raises(ASRValidationError) as caught:
        parse_segments(response, 10, engine="shared")
    assert reason in str(caught.value)
    assert reason in caught.value.constraint
    assert "ASR (shared)" in str(caught.value)
    assert "private-fixture-token" not in str(caught.value)
    assert "not complete" in str(caught.value)


@pytest.mark.parametrize("journal_writable", [True, False])
def test_provider_policy_refusal_is_private_and_does_not_fall_back(
    tmp_path, sample_media_av, monkeypatch, journal_writable
):
    import httpx
    from openai import BadRequestError
    from qwen_mm_plugins_omnidr import policy

    from shared import isolated_worker

    def refuse(*args, **kwargs):
        raise BadRequestError(
            "private-provider-body synthetic-secret",
            response=httpx.Response(400, request=httpx.Request("POST", "https://provider.invalid/v1")),
            body={"error": {"code": "content_policy_violation", "message": "private-provider-body"}},
        )

    def unexpected_fallback(*args, **kwargs):
        pytest.fail("A provider-policy refusal must not invoke local ASR")

    monkeypatch.setattr(config, "call_omni", refuse)
    monkeypatch.setattr(isolated_worker, "run_isolated", unexpected_fallback)
    if not journal_writable:

        def unwritable(*args):
            raise OSError(30, "private-provider-body", "/private-fixture/journal")

        monkeypatch.setattr(policy, "remember", unwritable)
    output = tmp_path / "transcript.json"
    result = call("omnidr_transcribe", file_path=sample_media_av, output_path=str(output))
    assert result["status"] == "failed" and "content-safety policy" in result["error"]
    assert "synthetic-secret" not in str(result) and "provider.invalid" not in str(result)
    assert "private-provider-body" not in str(result)
    assert ("stop all readers" in result["error"]) == (not journal_writable)
    assert not output.exists()


def test_reader_resume_preserves_policy_refusal_without_resubmitting(tmp_path, monkeypatch):
    from qwen_mm_plugins_omnidr import reading, transcription
    from qwen_mm_plugins_omnidr.common import ProviderPolicyError

    source = tmp_path / "source.mp4"
    source.write_bytes(b"synthetic-video")
    monkeypatch.setattr(reading, "probe", lambda _: {"duration": 3, "streams": [{"codec_type": "video"}]})
    calls = []

    def visual(arguments):
        calls.append("visual")
        raise ProviderPolicyError()

    def audio(file_path, output_path, start, end, **kwargs):
        calls.append("audio")
        Path(output_path).write_text(
            json.dumps(
                {
                    "status": "no_audio",
                    "segments": [],
                    "source_sha256": project.sha256(source),
                    "source_interval": [start, end],
                }
            )
        )

    monkeypatch.setattr(media, "perceive", visual)
    monkeypatch.setattr(transcription, "transcribe", audio)
    first = reading.read_video(str(source), str(tmp_path / "reading"))
    second = reading.read_video(str(source), str(tmp_path / "reading"), frames=48, frame_height=480)
    assert first["status"] == second["status"] == "incomplete"
    assert first["errors"] == second["errors"]
    assert first["errors"][0]["code"] == "provider_policy_rejection"
    assert first["errors"][0]["retry_allowed"] is False
    assert sorted(calls) == ["audio", "visual"]


@pytest.mark.parametrize("stopped_mode", ["frames", "video"])
def test_reader_preflight_retains_original_stop_without_widening_or_resubmission(tmp_path, monkeypatch, stopped_mode):
    from qwen_mm_plugins_omnidr import policy, reading, transcription

    source = tmp_path / "source.mp4"
    source.write_bytes(b"synthetic-video")
    info = {"duration": 3, "streams": [{"codec_type": "video"}]}
    monkeypatch.setattr(reading, "probe", lambda _: info)
    monkeypatch.setattr(media, "probe", lambda _: info)
    monkeypatch.setattr(
        media, "get_video_info", lambda _: pytest.fail("Preflight must precede decoding/provider calls")
    )
    policy.remember(source, stopped_mode, [0.25, 1.0])
    journal_before = {p: p.read_bytes() for p in policy.journal().rglob("*.json")}
    audio_calls = []

    def audio(file_path, output_path, start, end, **kwargs):
        audio_calls.append((start, end))
        policy.check(file_path, "audio", [start, end])
        Path(output_path).write_text(
            json.dumps(
                {
                    "status": "no_audio",
                    "segments": [],
                    "source_sha256": project.sha256(source),
                    "source_interval": [start, end],
                }
            )
        )

    monkeypatch.setattr(transcription, "transcribe", audio)
    first = reading.read_video(str(source), str(tmp_path / "reading"))
    second = reading.read_video(str(source), str(tmp_path / "reading"))
    assert first["status"] == second["status"] == "incomplete"
    assert sorted(first["errors"], key=lambda e: e["pass"]) == sorted(second["errors"], key=lambda e: e["pass"])
    assert len(first["errors"]) == (2 if stopped_mode == "video" else 1)
    for error in first["errors"]:
        assert error["recorded_stop"] == {
            "source_sha256": project.sha256(source),
            "modalities": sorted(policy.modalities(stopped_mode)),
            "interval": [0.25, 1.0],
        }
        assert error["provider_request_sent"] is False and error["retry_allowed"] is False
    assert audio_calls == [(0.0, 3)]
    assert journal_before == {p: p.read_bytes() for p in policy.journal().rglob("*.json")}
    # The broad blocked request never becomes a new refusal covering unrelated time.
    policy.check(source, "video", [1.25, 2.0])


def test_refusal_blocks_new_folders_narrow_ranges_and_other_models(tmp_path, sample_media_av, monkeypatch):
    import httpx
    from openai import BadRequestError
    from qwen_mm_plugins_omnidr.common import ProviderPolicyError

    requests = []

    def refuse(*args, **kwargs):
        requests.append(True)
        raise BadRequestError(
            "private diagnostic",
            response=httpx.Response(400, request=httpx.Request("POST", "https://private.invalid/v1")),
            body={"code": "content_policy_violation"},
        )

    monkeypatch.setattr(config, "call_omni", refuse)
    args = dict(
        file_path=sample_media_av,
        mode="frames",
        start_time=0.25,
        end_time=1.0,
        frames=2,
        prompt="synthetic observation",
    )
    with pytest.raises(ProviderPolicyError):
        media.perceive({**args, "output_path": str(tmp_path / "first.json")})
    copy = tmp_path / "same-content.mp4"
    shutil.copyfile(sample_media_av, copy)
    with pytest.raises(ProviderPolicyError):
        media.perceive(
            {
                **args,
                "file_path": str(copy),
                "start_time": 0.4,
                "end_time": 0.6,
                "model": "another-omni-model",
                "output_path": str(tmp_path / "new-directory/second.json"),
            }
        )
    with pytest.raises(ProviderPolicyError):
        media.extract_frame(str(copy), 0.5, str(tmp_path / "blocked-crop.jpg"), None, exact=True)
    assert len(requests) == 1
    assert not (tmp_path / "blocked-crop.jpg").exists()
    monkeypatch.setattr(config, "call_omni", lambda *a, **kw: ("Unrelated interval observed.", None))
    assert media.perceive({**args, "start_time": 1.25, "end_time": 1.5})["status"] == "observed"


def test_refusal_follows_transcode_aliases_in_a_fresh_process(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    from qwen_mm_plugins_omnidr import policy
    from qwen_mm_plugins_omnidr.common import ProviderPolicyError

    original, transcode = tmp_path / "original.mp4", tmp_path / "transcoded.mp4"
    original.write_bytes(b"synthetic original content")
    transcode.write_bytes(b"synthetic transcoded content")
    policy.register_aliases(project.sha256(original), project.sha256(transcode))
    policy.remember(transcode, "frames", [10, 20])
    with pytest.raises(ProviderPolicyError):
        policy.check(original, "video", [12, 14])
    policy.check(original, "audio", [12, 14])
    policy.check(original, "frames", [20, 25])
    program = """import sys
from pathlib import Path
from qwen_mm_plugins_omnidr import policy
from qwen_mm_plugins_omnidr.common import ProviderPolicyError
policy.STATE_ROOT = Path(sys.argv[2])
try:
    policy.check(Path(sys.argv[1]), 'frames', [11, 12])
except ProviderPolicyError:
    print('blocked')
else:
    raise SystemExit(2)
"""
    child = subprocess.run(
        [sys.executable, "-c", program, str(original), str(policy.STATE_ROOT)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(CAP)))},
    )
    assert child.returncode == 0 and child.stdout.strip() == "blocked"
    before = policy.journal()
    another = tmp_path / "another-working-directory"
    another.mkdir()
    monkeypatch.chdir(another)
    assert policy.journal() == before
    with pytest.raises(ProviderPolicyError):
        policy.check(original, "frames", [11, 12])


def test_saved_frame_provenance_prevents_later_image_resubmission(report, tmp_path, monkeypatch):
    from qwen_mm_plugins_omnidr import policy
    from qwen_mm_plugins_omnidr.common import ProviderPolicyError

    video = report / "media/source.mp4"
    frame = tmp_path / "ordinary-frame.jpg"
    media.extract_frame(str(video), 0, str(frame), None, exact=True)
    assert frame.with_suffix(".jpg.json").exists()
    policy.remember(video, "frames", [0, project.probe(video)["duration"]])
    monkeypatch.setattr(config, "call_omni", lambda *a, **kw: pytest.fail("A saved frame must not bypass the refusal"))
    with pytest.raises(ProviderPolicyError):
        media.perceive(dict(file_path=str(frame), mode="image", prompt="synthetic"))


def test_report_frame_records_stay_outside_visible_assets(report):
    video = report / "media/source.mp4"
    image = report / "img/frame.png"
    result = media.extract_frame(str(video), 0, str(image), None, exact=True)
    metadata = report / "_work/frame-provenance/img/frame.png.json"
    assert Path(result["metadata_path"]) == metadata
    assert list((report / "img").iterdir()) == [image]
    saved = json.loads(metadata.read_text())
    assert saved["source_sha256"] == project.sha256(video)
    assert saved["output_sha256"] == project.sha256(image)
    assert saved["timestamp"] == 0
    moved = report.with_name("moved-report")
    report.rename(moved)
    assert project.frame_metadata_path(moved / "img/frame.png") == moved / metadata.relative_to(report)


@pytest.mark.parametrize("legacy", [False, True])
def test_report_frame_never_overwrites_existing_provenance(report, legacy):
    image = report / "img/frame.png"
    record = image.with_suffix(".png.json") if legacy else project.frame_metadata_path(image)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text("Existing evidence must remain unchanged.")
    before = record.read_bytes()
    with pytest.raises(InputError, match="metadata already exists"):
        media.extract_frame(str(report / "media/source.mp4"), 0, str(image), None, exact=True)
    assert record.read_bytes() == before and not image.exists()


def test_report_frame_rejects_provenance_directory_escape(report, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (report / "_work/frame-provenance").symlink_to(outside, target_is_directory=True)
    with pytest.raises(InputError, match="inside its report"):
        media.extract_frame(str(report / "media/source.mp4"), 0, str(report / "img/frame.png"), None, exact=True)
    assert not list(outside.iterdir())


@pytest.mark.parametrize("layout", ["central", "legacy", "both"])
def test_report_frame_policy_bindings_survive_layout_and_move(report, layout):
    from qwen_mm_plugins_omnidr import policy
    from qwen_mm_plugins_omnidr.common import ProviderPolicyError

    video = report / "media/source.mp4"
    image = report / "img/frame.png"
    result = media.extract_frame(str(video), 1, str(image), None, exact=True)
    metadata = Path(result["metadata_path"])
    assert metadata.is_relative_to(report / "_work")
    legacy = image.with_suffix(".png.json")
    if layout == "legacy":
        metadata.rename(legacy)
    elif layout == "both":
        record = json.loads(metadata.read_text())
        record["timestamp"] = 0  # A historical binding must not be hidden by the new layout.
        legacy.write_text(json.dumps(record))
    policy.remember(video, "frames", [0, 0] if layout == "both" else [1, 1])
    moved = report.with_name("moved-report")
    report.rename(moved)
    with pytest.raises(ProviderPolicyError):
        policy.check(moved / "img/frame.png", "image", None)


@pytest.mark.parametrize("site,prefix", [("youtube", "ytsearch"), ("bilibili", "bilisearch")])
def test_platform_search_uses_public_watch_urls(monkeypatch, site, prefix):
    from qwen_mm_plugins_omnidr import public_search

    captured = []
    identity = "fixture-video"

    def run(command, **kwargs):
        captured.append(command)
        return SimpleNamespace(stdout=json.dumps({"entries": [{"id": identity, "title": "Fixture", "url": identity}]}))

    monkeypatch.setattr(public_search, "run", run)
    result = sources.search("observed subject", "video", 3, site, None, backend="platform")
    command = captured[0]
    assert command[-1] == f"{prefix}3:observed subject"
    assert "--ignore-config" in command and "--no-cache-dir" in command
    assert not any("synthetic-test-key" in str(part) for part in command)
    expected = (
        f"https://www.youtube.com/watch?v={identity}"
        if site == "youtube"
        else f"https://www.bilibili.com/video/{identity}/"
    )
    assert result["results"][0]["url"] == expected


def test_reverse_search_reports_unavailable_without_upload_or_keyword_substitution(monkeypatch):
    import requests
    from qwen_mm_plugins_omnidr import public_search

    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported reverse search must not upload or substitute a keyword search")

    for name in ("get", "post"):
        monkeypatch.setattr(requests.Session, name, forbidden)
    monkeypatch.setattr(public_search, "bing", forbidden)
    monkeypatch.setattr(public_search, "platform", forbidden)
    result = call("omnidr_search", kind="reverse", image_url="https://source.invalid/image.png")
    assert result["status"] == "failed" and "unavailable" in result["error"]


def test_search_and_fetch_schemas_expose_only_public_routes(monkeypatch, tmp_path):
    from pydantic import ValidationError
    from qwen_mm_plugins_omnidr.tools.fetch_source import FetchArgs
    from qwen_mm_plugins_omnidr.tools.search_sources import SearchArgs

    assert SearchArgs.model_json_schema()["properties"]["backend"]["enum"] == ["auto", "bing", "platform"]
    assert FetchArgs.model_json_schema()["properties"]["engine"]["enum"] == ["direct", "auto"]
    monkeypatch.setattr(sources, "search", lambda *a, **kw: pytest.fail("Invalid search routes must not run"))
    monkeypatch.setattr(sources, "fetch", lambda *a, **kw: pytest.fail("Invalid fetch routes must not run"))
    assert call("omnidr_search", query="fixture", backend="unsupported")["error"] == "ValidationError"
    assert (
        call(
            "omnidr_fetch_source",
            url="https://source.invalid/page",
            output_path=str(tmp_path / "page"),
            engine="unsupported",
        )["error"]
        == "ValidationError"
    )
    with pytest.raises(ValidationError):
        SearchArgs.model_validate({"query": "fixture", "api_key": "synthetic-key"})


def test_auto_fetch_preserves_original_bytes_despite_output_suffix(tmp_path, source_response):
    original = b"original source bytes"
    source_response(original)
    output = tmp_path / "source.pdf"
    result = sources.fetch("https://publisher.invalid/source", str(output), 1000, engine="auto")
    assert output.read_bytes() == original
    receipt = json.loads(Path(result["source_record_path"]).read_text())
    assert receipt["engine"] == "direct" and receipt["source_sha256"] == project.sha256(output)


@pytest.mark.parametrize("field", ["?token", "?sig", "?X-Goog-Credential", "#session", "?password"])
def test_source_receipt_omits_signed_url_secrets(tmp_path, field):
    artifact = tmp_path / "source.txt"
    artifact.write_text("Publicly discussable content")
    url = "https://cdn.invalid/file" + field + "=private-fixture"
    result = sources.source_record({"path": str(artifact), "canonical_url": url}, url, "direct")
    text = Path(result["source_record_path"]).read_text()
    assert "private-fixture" not in text
    assert "private-fixture" not in json.dumps(result)
    assert json.loads(text)["url"] is None and json.loads(text)["source_url_needs_canonical"] is True


def test_acquisition_index_survives_resume_without_claiming_reading(report, monkeypatch, source_response):
    import requests

    source_response(b"Saved public source content.", "text/plain; charset=utf-8")
    target = report / "_assets/docs/new.md"
    fetched = sources.fetch("https://publisher.invalid/article", str(target), 1000)
    receipt = Path(fetched["source_record_path"]).read_text()
    assert "synthetic-test-key" not in receipt and "provider.invalid" not in receipt
    result = call("inspect_omnidr_report", project_dir=str(report))
    acquisitions = result["authoring_diagnostics"]["unintegrated_acquisitions"]
    assert acquisitions == [
        {
            "media": "_assets/docs/new.md",
            "url": "https://publisher.invalid/article",
            "record": "_assets/docs/new.md.source.json",
            "reading_verified": False,
        }
    ]
    assert result["authoring_diagnostics"]["sources"]["total"] == 0
    before = target.read_bytes()
    monkeypatch.setattr(requests.Session, "get", lambda *a, **kw: pytest.fail("Saved evidence must not be overwritten"))
    with pytest.raises(InputError, match="already exists"):
        sources.fetch("https://publisher.invalid/replacement", str(target), 1000)
    assert target.read_bytes() == before


@pytest.mark.parametrize("mode", ["image", "frames", "video", "audio", "page"])
def test_every_modality_and_page_answer_use_three_settings_and_streaming(
    tmp_path, sample_image, sample_media_av, monkeypatch, mode
):
    import openai
    from qwen_mm_plugins_omnidr import config

    from shared import dashscope_upload, oss

    values = {
        "DASHSCOPE_API_KEY": "synthetic-key",
        "DASHSCOPE_BASE_URL": "https://provider.invalid/v1",
        "QWEN_MM_API_OMNI_MODEL": "fixture-omni-model",
    }
    requested, calls, clients, closed = [], [], [], []

    def setting(name, *args):
        assert name in values, f"Unexpected runtime configuration: {name}"
        requested.append(name)
        return values[name]

    shared_get_env = env.get_env

    def forbidden_setting(name, *args):
        if name in {"QWEN_MM_CHAT_TIMEOUT", "QWEN_MM_FFMPEG_TIMEOUT"}:
            return shared_get_env(name, *args)
        pytest.fail(f"A reader must not resolve an additional setting: {name}")

    def complete(**kwargs):
        calls.append(kwargs)
        return iter(
            [
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="Synthetic observation"))], usage=None
                )
            ]
        )

    def client(**kwargs):
        clients.append(kwargs)
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=complete)), close=lambda: closed.append(True)
        )

    monkeypatch.setattr(config, "get_env", setting)
    for module in (env, api_omni, api_openai, omni_media, dashscope_upload, oss):
        if hasattr(module, "get_env"):
            monkeypatch.setattr(module, "get_env", forbidden_setting)
    monkeypatch.setattr(api_omni, "get_bool_env", forbidden_setting)
    monkeypatch.setattr(openai, "OpenAI", client)
    if mode == "page":
        assert sources.answer_page("A saved source passage.", "What does the source say?") == "Synthetic observation"
    else:
        output = tmp_path / "observation.json"
        arguments = {
            "file_path": sample_image if mode == "image" else sample_media_av,
            "mode": mode,
            "prompt": "Describe.",
            "output_path": str(output),
        }
        if mode != "image":
            arguments.update(start_time=1, end_time=2, frames=3)
        result = media.perceive(arguments)
        assert result["status"] == "observed" and result["model"] == values["QWEN_MM_API_OMNI_MODEL"]
        assert "synthetic-key" not in output.read_text() and "provider.invalid" not in output.read_text()
        assert result["timestamp_origin_seconds"] == (1 if mode in {"video", "audio"} else 0)
    assert set(requested) == set(values)
    assert clients == [
        {
            "api_key": "synthetic-key",
            "base_url": "https://provider.invalid/v1",
            "timeout": 1800,
            "organization": "",
            "project": "",
            "max_retries": 0,
        }
    ]
    assert closed == [True]
    assert len(calls) == 1
    request = calls[0]
    assert request["model"] == "fixture-omni-model" and request["stream"] is True
    assert request["stream_options"] == {"include_usage": True}
    assert request["extra_body"] == {"modalities": ["text"]}
    assert "temperature" not in request and "max_tokens" not in request
    if mode == "audio":
        part = next(p["input_audio"] for p in request["messages"][-1]["content"] if p["type"] == "input_audio")
        assert part["data"].startswith("data:;base64,") and part["format"] == "wav"


@pytest.mark.parametrize("media_type", ["image", "video", "audio", "raw_audio"])
def test_streaming_rejects_an_oversized_item_before_opening_a_connection(monkeypatch, media_type):
    import openai

    monkeypatch.setattr(api_omni, "OMNI_MAX_B64_BYTES", 100)
    encoded = "A" * 101
    if media_type == "video":
        part = api_omni.omni_frames_part([api_omni.jpeg_data_url(encoded), api_omni.jpeg_data_url("B")])
    elif media_type in {"audio", "raw_audio"}:
        data = encoded if media_type == "raw_audio" else "data:;base64," + encoded
        part = {"type": "input_audio", "input_audio": {"data": data, "format": "wav"}}
    else:
        part = {"type": "image_url", "image_url": {"url": api_omni.jpeg_data_url(encoded)}}
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: pytest.fail("An oversized item must not reach a provider"))
    with pytest.raises(InputError, match="inline media item"):
        config.call_omni(
            base_url="https://provider.invalid/v1",
            api_key="synthetic-key",
            model="fixture-model",
            messages=[{"role": "user", "content": [part]}],
        )


def test_streaming_budget_is_per_item_and_preserves_usage(monkeypatch):
    import openai

    monkeypatch.setattr(api_omni, "OMNI_MAX_B64_BYTES", 100)
    messages = [
        {
            "role": "user",
            "content": [
                api_omni.omni_frames_part([api_omni.jpeg_data_url("A" * 60), api_omni.jpeg_data_url("B" * 60)])
            ],
        }
    ]
    assert api_omni.inline_b64_bytes(messages) == 120
    captured, closed = [], []
    usage = SimpleNamespace(total_tokens=7)

    def create(**kwargs):
        captured.append(kwargs)
        return [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="one "))], usage=None),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="two"))], usage=None),
            SimpleNamespace(choices=[], usage=usage),
        ]

    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **kw: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=lambda: closed.append(True)
        ),
    )
    assert config.call_omni(
        base_url="https://provider.invalid/v1", api_key="synthetic-key", model="fixture-model", messages=messages
    ) == ("one two", usage)
    assert closed == [True] and len(captured) == 1
    submitted = captured[0]["messages"][0]["content"]
    assert [part["image_url"]["url"] for part in submitted if part["type"] == "image_url"] == [
        api_omni.jpeg_data_url("A" * 60),
        api_omni.jpeg_data_url("B" * 60),
    ]


@pytest.mark.parametrize("failure", ["transient", "empty", "transient_exhausted", "empty_exhausted", "policy", "auth"])
def test_streaming_retry_rules_and_connection_cleanup(monkeypatch, failure):
    import httpx
    import openai

    from shared import retry

    attempts, closed = [], []
    response = httpx.Response(
        503 if failure.startswith("transient") else 401,
        request=httpx.Request("POST", "https://provider.invalid/v1"),
    )
    if failure == "policy":
        error = openai.BadRequestError(
            "private provider response", response=response, body={"code": "content_policy_violation"}
        )
    else:
        error = openai.APIStatusError("private provider response", response=response, body=None)

    def create(**kwargs):
        attempts.append(True)
        assert kwargs["model"] == "fixture-model"
        if failure in {"policy", "auth"} or failure.endswith("exhausted") or len(attempts) == 1:
            if failure.startswith("empty"):
                return []
            raise error
        return [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Recovered"))], usage=None)]

    monkeypatch.setattr(retry.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **kw: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=lambda: closed.append(True)
        ),
    )
    options = {
        "base_url": "https://provider.invalid/v1",
        "api_key": "synthetic-key",
        "model": "fixture-model",
        "messages": [],
    }
    if failure in {"policy", "auth"}:
        with pytest.raises(openai.APIStatusError):
            config.call_omni(**options)
        assert len(attempts) == 1
    elif failure.endswith("exhausted"):
        expected = api_omni.EmptyCompletionError if failure.startswith("empty") else openai.APIStatusError
        with pytest.raises(expected):
            config.call_omni(**options)
        assert len(attempts) == 4
    else:
        assert config.call_omni(**options)[0] == "Recovered"
        assert len(attempts) == 2
    assert closed == [True]


@pytest.mark.parametrize("reason", ["duration", "size"])
def test_inline_video_fallback_keeps_audio_and_range_without_storage_services(sample_media_av, monkeypatch, reason):
    from shared import dashscope_upload, oss

    shared_get_env = env.get_env

    def forbidden(*args, **kwargs):
        if args and args[0] in {"QWEN_MM_CHAT_TIMEOUT", "QWEN_MM_FFMPEG_TIMEOUT"}:
            return shared_get_env(*args, **kwargs)
        pytest.fail("Inline delivery must not access additional configuration or storage services")

    for module in (env, api_omni, api_openai, omni_media, dashscope_upload, oss):
        if hasattr(module, "get_env"):
            monkeypatch.setattr(module, "get_env", forbidden)
    monkeypatch.setattr(api_omni, "get_bool_env", forbidden)
    for helper in (
        "build_media_parts",
        "local_video_parts",
        "frames_and_audio_parts",
        "temporary_oss_parts",
        "transcode_and_upload",
    ):
        monkeypatch.setattr(omni_media, helper, forbidden)
    monkeypatch.setattr(oss, "upload_and_sign", forbidden)
    if reason == "size":

        def oversized(*args, **kwargs):
            raise omni_media.InlineBudgetExceeded("synthetic inline limit")

        monkeypatch.setattr(omni_media, "preprocess_video", oversized)
    else:
        monkeypatch.setattr(omni_media, "preprocess_video", forbidden)
    cleanup = []
    try:
        parts = media.build_media_parts(
            sample_media_av,
            "video",
            2.0,
            200704,
            cleanup,
            max_video_sec=0.5 if reason == "duration" else None,
            start_time=1,
            duration=1,
        )
        assert len(parts[0]["video"]) == 2
        assert parts[1]["type"] == "input_audio"
        assert parts[1]["input_audio"]["data"].startswith("data:;base64,")
        assert "#1=0s, #2=0.5s" in parts[-1]["text"]
        assert "COMPLETE, uninterrupted" in parts[-1]["text"]
        audio = next(Path(path) for path in cleanup if Path(path).suffix == ".mp3")
        assert 0.9 <= project.probe(audio)["duration"] <= 1.2
    finally:
        omni_media.cleanup_files(cleanup)
    assert all(not Path(path).exists() for path in cleanup)


@pytest.mark.parametrize("with_audio", [False, True])
def test_inline_fallback_reserves_a_slot_for_continuous_audio(tmp_path, monkeypatch, with_audio):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"synthetic video")
    monkeypatch.setattr(api_omni, "has_video_stream", lambda _: True)
    monkeypatch.setattr(omni_media, "media_duration", lambda _: 1000.0)
    monkeypatch.setattr(omni_media, "has_audio_stream", lambda _: with_audio)

    def audio(_source, output, **kwargs):
        assert kwargs["duration"] == 1000.0 and kwargs["start_time"] == 0
        Path(output).write_bytes(b"audio")

    def frames(_source, duration, fps, _pixels, _budget, **kwargs):
        count = int(duration * fps)
        return ["data:image/jpeg;base64,eA=="] * count, list(range(count))

    monkeypatch.setattr(omni_media, "fit_audio", audio)
    monkeypatch.setattr(omni_media, "fit_frames", frames)
    cleanup = []
    try:
        parts = media.build_media_parts(str(source), "video", 1.0, 200704, cleanup, max_video_sec=1)
        assert len(parts[0]["video"]) == 250 - int(with_audio)
        assert sum(part["type"] == "input_audio" for part in parts) == int(with_audio)
    finally:
        omni_media.cleanup_files(cleanup)


def test_inline_audio_limit_is_checked_without_model_or_storage_access(tmp_path, monkeypatch):
    source = tmp_path / "audio.wav"
    source.write_bytes(b"synthetic audio beyond the limit")
    monkeypatch.setattr(api_omni, "OMNI_MAX_B64_BYTES", 8)
    monkeypatch.setattr(api_omni, "has_video_stream", lambda _: False)
    monkeypatch.setattr(omni_media, "media_duration", lambda _: 1.0)
    with pytest.raises(InputError, match="per-item inline limit"):
        media.build_media_parts(str(source), "audio", 1.0, 200704, [])


@pytest.mark.parametrize("leak", ["secret", "host"])
def test_configured_model_secret_and_endpoint_are_blocked_independently(populated_report, monkeypatch, leak):
    secret, host = "synthetic-private-credential", "custom-provider.invalid"
    monkeypatch.setenv("DASHSCOPE_API_KEY", secret)
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://" + host + "/v1")
    (populated_report / "_work/leak.md").write_text(secret if leak == "secret" else "https://" + host + "/")
    result = project.validate(str(populated_report), stage="structure")
    assert result["status"] == "incomplete"
    assert any("configured secret or private model endpoint" in error for error in result["errors"])
    assert host not in json.dumps(result) and secret not in json.dumps(result)


def test_bilibili_api_download_preserves_audio_and_external_cookie(tmp_path, sample_media_av, monkeypatch):
    from qwen_mm_plugins_omnidr import bilibili
    from qwen_mm_plugins_omnidr.common import run

    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.m4a"
    run(["ffmpeg", "-v", "error", "-i", sample_media_av, "-map", "0:v:0", "-c", "copy", str(video)])
    run(["ffmpeg", "-v", "error", "-i", sample_media_av, "-map", "0:a:0", "-c", "copy", str(audio)])
    cookie = tmp_path / "external.txt"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n.bilibili.com\tTRUE\t/\tFALSE\t2147483647\tSESSDATA\tsynthetic-session\n"
    )
    original = cookie.read_bytes()
    captured = {}

    def api(session, path, params=None):
        assert session.cookies.get("SESSDATA") == "synthetic-session"
        if path.endswith("/spi"):
            return {"b_3": "server-issued-fixture"}
        if path.endswith("/view"):
            return {"bvid": "BV1234567890", "pages": [{"page": 2, "cid": 99}]}
        captured.update(params)
        return {
            "dash": {
                "video": [{"id": 64, "baseUrl": "https://cdn.invalid/video"}],
                "audio": [{"id": 30280, "baseUrl": "https://cdn.invalid/audio"}],
            }
        }

    def stream(session, url, target, remaining):
        assert session.headers["Referer"] == "https://www.bilibili.com/"
        source = video if url.endswith("video") else audio
        shutil.copyfile(source, target)
        return source.stat().st_size

    monkeypatch.setattr(bilibili, "api", api)
    monkeypatch.setattr(bilibili, "download_stream", stream)
    result = sources.download_video(
        "https://www.bilibili.com/video/av123?p=2", str(tmp_path / "result.mp4"), str(cookie)
    )
    assert result["backend"] == "bilibili-api"
    assert captured["avid"] == "123" and captured["cid"] == 99
    assert {s["codec_type"] for s in project.probe(result["path"])["streams"]} == {"video", "audio"}
    assert cookie.read_bytes() == original
    assert result["canonical_url"] == "https://www.bilibili.com/video/BV1234567890/?p=2"
    assert json.loads(Path(result["source_record_path"]).read_text())["url"] == result["canonical_url"]


@pytest.mark.parametrize("filename", ["settings.yaml", "notes"])
def test_privacy_scan_does_not_depend_on_text_filename(populated_report, filename):
    (populated_report / "_work" / filename).write_text("synthetic-test-key")
    result = project.validate(str(populated_report), stage="structure")
    assert result["status"] == "incomplete"
    assert any("configured secret" in error for error in result["errors"])
    assert "synthetic-test-key" not in json.dumps(result)


def test_svg_document_validation_and_inert_rendering(populated_report, browser_page):
    import html

    root, _, data = project.load_project(str(populated_report))
    active = '<script>parent.document.documentElement.dataset.auditExecuted="yes"</script>'
    payload = '<iframe srcdoc="' + html.escape(active, quote=True) + '"></iframe>'
    data["segments"][0]["caption"].append({"type": "svg", "svg": payload})
    write_data(root, data)
    checked = project.validate(str(root), stage="structure")
    assert "SVG blocks must be static and self-contained." in checked["errors"]
    # A report may be opened before validation; the renderer must also be safe.
    page = browser_page
    page.goto((root / "index.html").as_uri(), wait_until="load")
    assert page.locator("iframe").count() == 0
    assert page.evaluate("document.documentElement.dataset.auditExecuted") is None
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40"><rect width="100" height="40" fill="green"/></svg>'
    data["segments"][0]["caption"][-1]["svg"] = svg
    write_data(root, data)
    assert project.validate(str(root), stage="structure")["status"] == "structure_valid"
    page.reload(wait_until="load")
    assert page.locator(".svg-block img").evaluate("image => image.naturalWidth") == 100


def test_source_session_does_not_import_netrc_on_requests_or_redirects(tmp_path, monkeypatch):
    import requests
    from qwen_mm_plugins_omnidr.common import public_session

    netrc = tmp_path / "synthetic.netrc"
    netrc.write_text("default login synthetic-user password synthetic-password\n")
    netrc.chmod(0o600)
    monkeypatch.setenv("NETRC", str(netrc))
    with public_session() as session:
        request = session.prepare_request(requests.Request("GET", "https://source.invalid/article"))
        assert "Authorization" not in request.headers
        redirected = request.copy()
        redirected.url = "https://redirected.invalid/article"
        response = requests.Response()
        response.request = request
        session.rebuild_auth(redirected, response)
        assert "Authorization" not in redirected.headers
        assert session.trust_env  # User proxy and certificate settings still work.


def test_local_plugin_copy_includes_shared_roles_and_local_commands(tmp_path):
    import subprocess
    import sys

    source_mcp = (CAP / ".mcp.json").read_bytes()
    destination = tmp_path / "plugin"
    subprocess.run(
        [
            sys.executable,
            str(SERVER / "local_plugin.py"),
            "--python",
            sys.executable,
            "--output",
            str(destination),
        ],
        check=True,
        capture_output=True,
    )
    claude = json.loads((destination / ".claude-plugin/plugin.json").read_text())
    mcp = json.loads((destination / ".mcp.json").read_text())
    qoder = json.loads((destination / ".qoder-plugin/plugin.json").read_text())
    assert json.loads((destination / qoder["mcp"]).read_text()) == mcp
    assert (destination / qoder["skills"] / "SKILL.md").is_file()
    assert "agents" not in claude
    assert (CAP / "agents").is_dir() and not (CAP / "agents").is_symlink()
    assert not (CAP / "skill/agents").exists()
    assert {path.stem for path in (destination / "agents").glob("*.md")} == {
        "video-reader",
        "anchor-researcher",
        "doc-reader",
        "fact-checker",
        "synthesis-reviewer",
    }
    for path in (destination / "agents").glob("*.md"):
        canonical = CAP / "agents" / path.name
        assert not path.is_symlink()
        assert path.read_bytes() == canonical.read_bytes()
        assert path.read_text().startswith("---\nname: " + path.stem + "\n")
    import re

    for document in (destination / "skill").rglob("*.md"):
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", document.read_text()):
            if "://" not in target:
                assert (document.parent / target).is_file(), (document, target)
    assert claude["mcpServers"] == mcp["mcpServers"]
    assert mcp["mcpServers"]["qwen-mm-plugins-omnidr"]["args"] == [str(SERVER)]
    assert (CAP / ".mcp.json").read_bytes() == source_mcp


def test_real_mcp_reader_roundtrip_with_local_streaming_endpoint(tmp_path, sample_media_av):
    import os
    import re
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    captured = []
    question = "Compare the blue fixture with a fictional red engine; output a table."

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            parts = next(m["content"] for m in body["messages"] if m["role"] == "user")
            types = [part["type"] for part in parts]
            prompt = "\n".join(p["text"] for p in parts if p["type"] == "text")
            captured.append((self.path, self.headers.get("Authorization"), types, prompt, body))
            content = (
                json.dumps({"language": "en", "segments": [{"start": 0.1, "end": 1, "text": "synthetic speech"}]})
                if "input_audio" in types
                else "Synthetic test-pattern frames, source timestamps preserved."
            )
            chunk = {
                "id": "fixture",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "fixture-model",
                "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
            }
            response = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_):
            pass

    bootstrap = tmp_path / "mcp_server.py"
    bootstrap.write_text(
        "import sys\nfrom pathlib import Path\n"
        + "sys.path[:0] = "
        + repr([str(ROOT / "src"), str(CAP)])
        + "\n"
        + "from qwen_mm_plugins_omnidr import policy\n"
        + "policy.STATE_ROOT = Path("
        + repr(str(tmp_path / "private-policy"))
        + ")\n"
        + "from qwen_mm_plugins_omnidr.__main__ import main\nmain()\n"
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        settings = dict(
            os.environ,
            DASHSCOPE_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1",
            DASHSCOPE_API_KEY="synthetic-mcp-key",
            QWEN_MM_API_OMNI_MODEL="fixture-model",
        )
        result = mcp_call(
            str(bootstrap),
            lambda session: session.call_tool(
                "omnidr_read_video",
                {"file_path": sample_media_av, "output_dir": str(tmp_path / "reading"), "question": question},
            ),
            env=settings,
        )
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "read", payload
        assert len(captured) == 2
        assert all(
            path == "/v1/chat/completions" and auth == "Bearer synthetic-mcp-key" for path, auth, _, _, _ in captured
        )
        assert all(
            body["model"] == "fixture-model" and body["stream"] is True and body["modalities"] == ["text"]
            for _, _, _, _, body in captured
        )
        audio_prompt, audio_body = next(
            (prompt, body) for _, _, types, prompt, body in captured if "input_audio" in types
        )
        assert [m["role"] for m in audio_body["messages"]] == ["system", "user"]
        assert question not in audio_prompt and audio_body["temperature"] == 0
        assert question not in audio_body["messages"][0]["content"]
        duration = re.search(r"Clip duration: ([0-9.]+) seconds", audio_prompt)
        assert duration is not None
        assert float(duration[1]) == pytest.approx(payload["source_interval"][1] - payload["source_interval"][0])
        assert any(
            types.count("image_url") == 32
            and question in prompt
            and "temperature" not in body
            and [m["role"] for m in body["messages"]] == ["user"]
            for _, _, types, prompt, body in captured
        )
        transcript = json.loads((tmp_path / "reading/clip-0000.tr.json").read_text())
        assert transcript["segments"][0]["text"] == "synthetic speech"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
