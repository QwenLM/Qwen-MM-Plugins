"""Offline selection checks for the skill's standalone catalog search script."""

import importlib.util
import json
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[2] / "src/capabilities/omni-chatcut/skill/music-to-mv"
SPEC = importlib.util.spec_from_file_location(
    "search_local_characters", SKILL_DIR / "scripts/search_local_characters.py"
)
SEARCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SEARCH)


@pytest.fixture
def catalog(tmp_path):
    records = []
    for index, age, gender, occupation, description in (
        (1, 25, "女", "教练", "短发，开朗。"),
        (2, 28, "女", "演员", "短发，开朗，扮演教练。"),
        (3, 45, "男", "教练", "短发，开朗。"),
    ):
        records.append(
            {
                "group_id": f"group-{index}",
                "name": occupation,
                "metadata": {"age": age, "gender": gender, "occupation": occupation, "country": "中国"},
                "description": description,
                "images": [
                    {"asset_id": f"asset-{index}-{v}", "asset_uri": f"asset://asset-{index}-{v}"} for v in (1, 2)
                ],
                "search_attributes": ["古装造型"],
                "archival_meta": "气质:开朗",
            }
        )
    path = tmp_path / "characters.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    return path


def test_filters_ranking_and_bounded_output(catalog):
    result = SEARCH.search_catalog(catalog, gender="女", age_min=20, age_max=30, query="短发 教练", limit=1)
    assert result["total_matches"] == 2
    assert result["returned"] == 1
    assert result["characters"][0]["group_id"] == "group-1"
    assert "archival_meta" not in result["characters"][0]
    assert SEARCH.search_catalog(catalog, query="短发 不存在")["total_matches"] == 0


def test_exact_image_lookup_preserves_full_group(catalog):
    result = SEARCH.search_catalog(catalog, asset_id="asset://asset-2-2", details=True)
    character = result["characters"][0]
    assert result["total_matches"] == 1
    assert len(character["images"]) == 2
    assert character["archival_meta"] == "气质:开朗"
    assert character["search_attributes"] == ["古装造型"]


@pytest.mark.parametrize("arguments", [{"limit": 0}, {"limit": 31}, {"age_min": 31, "age_max": 20}])
def test_invalid_limits(catalog, arguments):
    with pytest.raises(ValueError):
        SEARCH.search_catalog(catalog, **arguments)
