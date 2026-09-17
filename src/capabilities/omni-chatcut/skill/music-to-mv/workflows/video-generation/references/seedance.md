# Seedance Adapter

Select `video.provider=seedance`. The adapter connects directly to the official Volcengine Ark API.
Use this connection under `video_providers.seedance` in the unified model config:

```json
{
  "base_url": "https://ark.cn-beijing.volces.com",
  "api_key_env": "ARK_API_KEY",
  "model": "doubao-seedance-2-5-260628"
}
```

The adapter appends `/api/v3`; do not include that suffix in `base_url`. Only the Ark API key is
needed. This workflow never uses AK/SK, creates asset groups, uploads private character assets, or
calls an asset-registration service.

## Fixed characters: existing Ark image assets

For every storyboard `cast_present` ID, bind an existing Ark image asset: use the user's supplied
public or uploaded/authorized asset first, or select from the bundled public pool when unspecified.
Follow [character selection](../../../references/seedance-character-search.md). Configure its URI
under `providers.seedance` in the per-run config (the existing `official_identity_assets` key is
retained for compatibility and accepts both sources):

```json
{
  "generate_audio": false,
  "watermark": false,
  "official_identity_assets": {
    "C1": "asset://asset-20260401123823-6d4x2"
  }
}
```

The ID above is an example from the official documentation, not a default for every cast member.
Align the storyboard's identity, appearance and continuity descriptions with the chosen asset.
For unspecified roles, select from the local pool and offer the user the upload/public-library
override paths without blocking the default selection. Never invent IDs.

[Official preset character library and examples](https://docs.volcengine.com/docs/82379/2608626?lang=zh#preset-avatar).

This Music-to-MV route accepts explicitly configured Ark image asset IDs from the public library
or a user's completed authorized upload. Ordinary HTTP(S) image URLs, local files, Base64 portraits
and generated Qwen Image/Seedream portraits are not fixed-identity inputs. It consumes existing
assets rather than implementing enrollment. Do not automatically reuse legacy registered IDs from
execution state; a user must explicitly select an existing asset for the current cast. Uploaded
real-person material follows the [official enrollment process](https://docs.volcengine.com/docs/82379/2315856).

`plan`, `base-assets`, and segment preparation check required bindings before remote work.
`base-assets` records the selected IDs locally and skips image generation entirely, even when an
image provider is configured. No local character image or image-generation key is required.
URI syntax can be checked offline, but only the official API can confirm availability; do not claim
that an ID is available, public, or authorized solely because it starts with `asset://`.

Legacy `identity_reference_urls` and `register_identity_assets` settings are rejected with a migration
message. Replace them with `official_identity_assets` and update the connection URL. Existing generated
portraits and registered IDs are never automatically migrated. Provider schema and reference signatures
invalidate incompatible old video tasks; changing a cast's asset also invalidates its affected shots.

## Video requests

Submit `POST /api/v3/contents/generations/tasks` and poll
`GET /api/v3/contents/generations/tasks/{task_id}`, both with `Authorization: Bearer <API key>`.
Read the task's `id`, `status`, and `content.video_url`; download completed results immediately.

Each request contains one text item, the shot's configured Ark image asset references, and the complete
local shot WAV as inline reference audio. Saved requests replace inline audio with a SHA-256 marker;
execution state retains the local audio path and hash. All cuts remain local.

The maintained scheduler starts at 60 submissions per minute and can step down through
`60 → 30 → 15 → 5` after widespread rate-limit evidence. These are local scheduling settings,
not a guarantee of the account's official quota.

Before paid submission the adapter enforces:

- integer duration `min(12, max(4, ceil(shot.duration_sec)))`; short shots are trimmed and shots above
  12 seconds but no longer than 13 seconds are slowed locally to their storyboard frame count,
  using the downloaded video stream's measured duration and preserving the original soundtrack speed
- resolution `480p`, `720p`, or `1080p`
- ratio `16:9`, `4:3`, `1:1`, `3:4`, `9:16`, or `21:9`; shared `adaptive` becomes `16:9`
- non-empty one-shot prompt, Ark image asset URIs, and inline audio

The 4–12 second request window and 13 second storyboard ceiling are Music-to-MV constraints, not
Seedance 2.5's full API limits. Keep `generate_audio=false`: local assembly restores the original song.
[Official video API](https://docs.volcengine.com/docs/82379/1520757?lang=zh).
