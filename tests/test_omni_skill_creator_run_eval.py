"""Focused checks for the standalone trigger evaluator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "src/capabilities/omni-skill-creator/skill"


def _load_run_eval():
    sys.path.insert(0, str(SKILL_ROOT))
    try:
        spec = importlib.util.spec_from_file_location("omni_skill_creator_run_eval", SKILL_ROOT / "scripts/run_eval.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SKILL_ROOT))


def _load_improve_description():
    spec = importlib.util.spec_from_file_location(
        "omni_skill_creator_improve_description",
        SKILL_ROOT / "scripts/improve_description.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_trigger_evaluator_applies_configured_timeout(monkeypatch):
    run_eval = _load_run_eval()
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="yes"))])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    evaluator = run_eval.TriggerEvaluator(
        model="test-model",
        api_base="https://example.invalid/v1",
        api_key="test-key",
        timeout=17,
    )

    assert evaluator.check_trigger("make a chart", "chart-skill", "Creates charts") is True
    assert captured["client"] == {
        "base_url": "https://example.invalid/v1",
        "api_key": "test-key",
        "timeout": 17,
    }


def test_import_ignores_an_unrelated_top_level_scripts_package(monkeypatch):
    unrelated = ModuleType("scripts")
    unrelated.__path__ = []
    monkeypatch.setitem(sys.modules, "scripts", unrelated)

    run_eval = _load_run_eval()

    assert callable(run_eval.parse_skill_md)
    assert run_eval.parse_skill_md.__module__ == "_omni_skill_creator_run_eval_utils"


def test_trigger_evaluator_reads_shared_config_file(monkeypatch, tmp_path):
    import shared.env as env

    config = tmp_path / "config"
    config.write_text(
        "DASHSCOPE_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1\nDASHSCOPE_API_KEY=config-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("QWEN_MM_CONFIG", str(config))
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(env, "_config_cache", None)

    evaluator = _load_run_eval().TriggerEvaluator()

    assert evaluator.api_base == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    assert evaluator.api_key == "config-key"


def test_description_improver_reads_shared_config_file(monkeypatch, tmp_path):
    import shared.env as env

    config = tmp_path / "config"
    config.write_text(
        "DASHSCOPE_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1\nDASHSCOPE_API_KEY=config-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("QWEN_MM_CONFIG", str(config))
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(env, "_config_cache", None)
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="improved"))])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    result = _load_improve_description()._call_llm("prompt", "test-model", timeout=19)

    assert result == "improved"
    assert captured["client"] == {
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "api_key": "config-key",
        "timeout": 19,
    }


@pytest.mark.parametrize("script", ["run_eval", "improve_description"])
@pytest.mark.parametrize(
    "base, explicit_key, expected_key",
    [
        ("https://openrouter.ai/api/v1", None, "router-key"),
        ("https://api.orcarouter.ai/v1", None, "orca-key"),
        ("https://custom.example/v1", None, "EMPTY"),
        ("https://custom.example/v1", "explicit-key", "explicit-key"),
    ],
)
def test_description_scripts_select_key_for_effective_endpoint(monkeypatch, script, base, explicit_key, expected_key):
    from shared import env

    monkeypatch.setattr(env, "_config_cache", {})
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-key")
    monkeypatch.setenv("ORCAROUTER_API_KEY", "orca-key")
    clients = []

    def fake_client(**kwargs):
        clients.append(kwargs)
        return SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kw: SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content="yes"))]
                    )
                )
            )
        )

    monkeypatch.setattr("openai.OpenAI", fake_client)
    if script == "run_eval":
        evaluator = _load_run_eval().TriggerEvaluator(api_base=base, api_key=explicit_key)
        assert evaluator.check_trigger("query", "skill", "description")
    else:
        assert _load_improve_description()._call_llm("prompt", "test-model", base, explicit_key) == "yes"
    assert clients[0]["base_url"] == base
    assert clients[0]["api_key"] == expected_key
