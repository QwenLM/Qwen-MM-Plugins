# Seedance character selection

Use existing Ark image assets for Seedance cast identities; skip identity-image generation.
The skill selects from the local catalog or uses user-provided asset IDs. Public-library browsing,
searching, preview retrieval and uploads on the website are performed manually by the user.
Do not access the website or its console interfaces to discover or inspect characters on the user's behalf.

## Selection order and default behavior

1. Preserve a user-specified asset or existing project selection. Accept a bare `asset-...` ID or
   `asset://asset-...` URI and normalize it for the run config. It may come from the public library
   or the user's completed authorized upload; it need not appear in the bundled catalog.
2. If no character is specified, derive metadata filters and literal keywords from the outline and
   search the local 300-character catalog. Read a few candidates, select a suitable image asset,
   and explain the choice using the official tags and biography. Do not ask permission to search
   the local pool or add a separate confirmation step to the existing storyboard review flow.
3. If no suitable candidate exists locally, broaden local filters or suggest that the user manually
   select an image in the public library and provide its asset ID. Do not substitute another asset
   silently when an explicit user selection is missing from the pool or is rejected during execution.

When proposing a default candidate, briefly offer the user these optional alternatives:

> 我先从人物池选了「角色名称」（asset ID），因为它的形象和台本中的角色设定相符。
> 你也可以直接提供已有的 asset ID；或自行在火山完成素材上传/授权后把 ID 给我；
> 或自行到火山公共虚拟人像库挑选人物，复制该图片的 asset ID 给我。

This is an optional override notice, not a blocking question. If the user wants to choose, present
local candidates with their tags, biographies and IDs, plus any user-supplied previews.
Use the selected image's asset ID, never an asset group ID or a video/audio asset ID.

## User-operated asset sources

For a public-library choice, suggest that the user manually browse the
[Ark experience center](https://console.volcengine.com/ark/region:cn-beijing/experience/vision),
select a portrait and provide its image asset ID or URI. The
[official public-library guide](https://docs.volcengine.com/docs/82379/2223965) is available for the
user's own reference. The skill only offers this option and consumes the ID the user provides.

For the user's own material, suggest that they complete the supported upload and any required
personal authorization in Ark, then provide the available image asset ID. Refer the user to the
[official enrollment guide](https://docs.volcengine.com/docs/82379/2315856) if needed. A local image
sent to the agent is not an enrolled asset. The skill does not operate the website, upload material,
perform enrollment or authorize use on the user's behalf.

Use user-provided descriptions/previews when an asset is outside the local catalog. Request missing
character information only when needed for the storyboard. Never infer authorization or availability
from ID syntax; the generation API ultimately checks access. Keep user assets within the project.

## Local character catalog

[seedance-characters.jsonl](../assets/seedance-characters.jsonl) contains 300 character groups and
600 image asset IDs, with one complete character per line. This is a dated public-library snapshot, not the complete library or a recommended cast.

Use the standard-library-only [local search script](../scripts/search_local_characters.py) to filter
records outside model context. From this skill directory:

```bash
python3 scripts/search_local_characters.py --gender 女 --age-min 20 --age-max 30 --query '古装' --limit 5
python3 scripts/search_local_characters.py --asset-id asset-20260804202332-gcmkm --details
```

Use an absolute script path when running from another directory; the default catalog path resolves
relative to the script, not the working directory. Optional exact metadata filters are `--gender`,
`--country`, `--occupation`, `--temperament`, `--age-min` and `--age-max`.
`--query` takes space-separated literal keywords, all of which must appear somewhere in the title,
tags, biography or official appearance descriptions. Keywords are case-insensitive; this is not
semantic search and does not interpret a natural-language request. Convert the outline into a few
concrete keywords; broaden them or suggest that the user manually select an asset in the public library if nothing matches.

Results prioritize title/basic-tag matches over biography matches, then detailed appearance tags;
ties retain catalog order. `keyword_score` measures text matches, not role suitability. Output
reports the total match count and returns at most `--limit` candidates (default 5, maximum 30),
including their basic tags, biography and all image IDs. `--details` adds the full official search
descriptions and archival tags. An exact `--asset-id` lookup accepts a bare ID or `asset://` URI
and returns its whole character group, including the other image variant. Local search is entirely
offline. Any additional preview or description must be supplied by the user.
Do not load the entire catalog into model context.

Each entry preserves the following official fields:

- `name` and `description`: group title and unmodified biography.
- `metadata`: basic tags (type, age, gender, country, occupation, temperament).
- `search_attributes`: the original `AdditionalInfo.SearchAttributes` descriptions.
- `archival_meta`: the original `AdditionalInfo.ArchivalMeta` string, including detailed setting,
  appearance and clothing tags. These are provider-authored virtual-character descriptions,
  not observations made by this skill; retain their wording and any inconsistencies.
- `group_id` and `images`: the group ID and every image's `asset_id` / `asset_uri` in that group.

The catalog contains text and asset IDs, not images. Use its official descriptions as textual
evidence and inspect images only when supplied by the user. Do not claim visual inspection from
catalog text alone. The snapshot does not verify current availability or generation access.
An image asset ID, not its group ID, is the value used for generation bindings.

## Binding the selected asset

Bind the selected image's `asset_uri` in `providers.seedance.official_identity_assets[cast_id]`.
Record its source (local pool or user-provided asset), selection rationale and available appearance
evidence in the cast planning notes. Align prompts with that evidence and preserve user choices.
