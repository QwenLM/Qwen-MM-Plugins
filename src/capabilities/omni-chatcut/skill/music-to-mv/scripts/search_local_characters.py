#!/usr/bin/env python3
"""Filter the bundled portrait JSONL offline; return a bounded cast shortlist."""

import argparse
import json
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[1] / "assets/seedance-characters.jsonl"


def search_catalog(
    catalog=CATALOG,
    *,
    query="",
    gender=None,
    country=None,
    occupation=None,
    temperament=None,
    age_min=None,
    age_max=None,
    asset_id=None,
    limit=5,
    details=False,
):
    if not 1 <= limit <= 30:
        raise ValueError("limit must be between 1 and 30")
    if any(age is not None and not 0 <= age <= 100 for age in (age_min, age_max)):
        raise ValueError("ages must be between 0 and 100")
    if age_min is not None and age_max is not None and age_min > age_max:
        raise ValueError("age_min must not exceed age_max")
    terms = list(dict.fromkeys(query.casefold().split()))
    selected_id = asset_id.strip().removeprefix("asset://") if asset_id else None
    matches = []
    with Path(catalog).open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            character = json.loads(line)
            metadata = character["metadata"]
            if any(
                value is not None and metadata.get(key) != value
                for key, value in (
                    ("gender", gender),
                    ("country", country),
                    ("occupation", occupation),
                    ("temperament", temperament),
                )
            ):
                continue
            age = metadata.get("age")
            if age_min is not None and (age is None or age < age_min):
                continue
            if age_max is not None and (age is None or age > age_max):
                continue
            if selected_id and not any(image["asset_id"] == selected_id for image in character["images"]):
                continue
            fields = [
                (3, character["name"] + " " + " ".join(str(v) for v in metadata.values() if v is not None)),
                (2, character["description"]),
                (1, " ".join(character.get("search_attributes", [])) + " " + character.get("archival_meta", "")),
            ]
            scores = [max((weight for weight, text in fields if term in text.casefold()), default=0) for term in terms]
            if terms and not all(scores):
                continue
            matches.append((sum(scores), character))
    matches.sort(key=lambda item: item[0], reverse=True)
    entries = []
    for score, character in matches[:limit]:
        entry = (
            dict(character)
            if details
            else {key: character[key] for key in ("group_id", "name", "metadata", "description", "images")}
        )
        entry["keyword_score"] = score
        entries.append(entry)
    return {"total_matches": len(matches), "returned": len(entries), "characters": entries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG, help="Override the bundled JSONL path")
    parser.add_argument("--query", default="", help="Space-separated literal keywords; every keyword must match")
    parser.add_argument("--gender", choices=("男", "女"))
    parser.add_argument("--country", help="Exact official country tag")
    parser.add_argument("--occupation", help="Exact official occupation tag")
    parser.add_argument("--temperament", help="Exact official temperament tag")
    parser.add_argument("--age-min", type=int)
    parser.add_argument("--age-max", type=int)
    parser.add_argument(
        "--asset-id", help="Look up an image asset ID or asset:// URI; returns its whole character group"
    )
    parser.add_argument("--limit", type=int, default=5, help="Maximum results, 1–30 (default 5)")
    parser.add_argument("--details", action="store_true", help="Include full official appearance descriptions and tags")
    args = parser.parse_args()
    try:
        result = search_catalog(**vars(args))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Local catalog search failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
