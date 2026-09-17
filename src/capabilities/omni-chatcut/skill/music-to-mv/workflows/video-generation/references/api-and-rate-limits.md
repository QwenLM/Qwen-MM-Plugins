# API Configuration and Rate Limits

Use the root Skill's `assets/model-config.example.json` for endpoints, model IDs, optional workspace-variable
names, and credential-variable names. Select `image.provider=qwen_image_3` or
`image.provider=seedream`, and select `video.provider=wan3` or `video.provider=seedance` in the
per-run `assets/api-config.example.json`. That run config owns generation controls, concurrency,
transport, retries, QC policy, subtitles, and assembly. Qwen Image and Wan remain the defaults.
Never store a credential value in either JSON file.

`quality_control.enabled` is the sole switch for Omni shot review and defaults to `false`; profiles
do not override it. Enable it only when the user explicitly requests semantic QC or supplies a run
config that already sets it to `true`. When enabled, it inherits the unified `omni`
connection. A legacy run config may still override its `api_key_env`, `model`, or `base_url`.
`strategy` is `reject_major` or `accept_all`; `max_rounds` counts total generated candidates and
defaults to three. `workers` limits concurrent Omni calls and defaults to eight. `fps`, `max_pixels`,
`temperature`, and `max_tokens` control the review call. `prompt_path` replaces the maintained rubric
when explicitly configured, while `extra_requirements` appends project-specific visible checks.

`subtitles.enabled` controls deterministic lyric burn-in after assembly. `source_srt` and
`source_evidence` override project auto-discovery; `font` overrides the bundled Noto font; `preset`
and `crf` control only the final subtitle render. Subtitle rendering is local and has no provider
rate limit or credential.

## Separate limits

Treat these as independent controls:

- selected image-provider identity-generation workers
- selected video-provider task submission and query rate
- parallel result downloads
- Omni semantic-QC calls and any provider regenerations they trigger

Parallelism is an execution invariant, not merely a tuning suggestion. Submit a whole authorized
generation set through the shared rate limiter, keep all accepted provider tasks in flight together,
and download completed results with the download worker pool. When semantic QC is enabled, review all
candidates available in the same round with the Omni worker pool, then send rejected segments through
one shared rate-limited regeneration batch. Do not create a serial per-segment
generate-review-regenerate chain or independent regeneration schedulers that bypass the provider RPM
limit.

Read [qwen-image30.md](qwen-image30.md) or [seedream.md](seedream.md) according to the configured image provider. For Wan, both image routes generate exactly one resumable identity image per cast ID and download temporary result URLs immediately; changing the image provider changes the image execution signature and invalidates identity reuse.

`current_rpm` is a read-only current-window counter. Do not put `current_rpm` or `rpm_limit` into request payloads. Each provider owns its submission ladder: the example uses `5 → 3 → 2 → 1` for Wan and `60 → 30 → 15 → 5` for Seedance. An isolated 429 requeues only that task; a recent-window error ratio or consecutive burst moves to the next configured rate.

## Wan 3.0 behavior

Wan defaults to the shared `https://dashscope.aliyuncs.com` endpoint and `Authorization: Bearer <key>`, with no workspace ID setup. A workspace-specific endpoint is optional. Submission also requires `X-DashScope-Async: enable`. If any input uses a temporary `oss://` URL, submission adds `X-DashScope-OssResourceResolve: enable`.

The adapter maps output controls to `parameters`: uppercase `resolution`, `ratio` (default `adaptive`), integer `duration`, `audio`, `prompt_extend`, optional `seed`, and `watermark`. The default keeps `audio=false`, because assembly replaces generated audio with the untouched source track, and `prompt_extend=false`, because Music2MV already supplies a detailed one-shot prompt.

Wan input checks happen before paid submission:

- output duration is an integer from 2 through 30 seconds
- the complete editorial-shot reference audio is 1 through 15 seconds
- identity references are at most 10 images
- resolution is `480P`, `720P`, or `1080P`
- ratio is `adaptive`, `16:9`, `4:3`, `1:1`, `3:4`, or `9:16`
- prompt length is at most 20,000 characters
- reference audio is an `http(s)` or `oss://` URL

Local identity images are legal inline Base64 image inputs. Local audio is not sent inline. Resolve audio in this order:

1. run config `providers.wan3.reference_audio_urls[segment_index]`
2. run config `providers.wan3.reference_audio_url_template`, formatted with `{index}`, `{filename}`, and `{source_hash}`
3. `reference_audio_mode=dashscope_oss`, which runs `dashscope oss.upload --model <model> --file <segment.wav>` and caches the returned temporary URL

The DashScope upload path requires CLI/SDK version 1.24.0 or later. Temporary uploads are associated with the same account and model; the executor passes the API key only through the subprocess environment, never the command line.

Wan tasks use `output.task_id`, are polled at `/api/v1/tasks/{task_id}`, and map `PENDING/RUNNING/SUCCEEDED/FAILED/CANCELED/UNKNOWN` into local state. A successful `output.video_url` is downloaded immediately because both task lookup and result URLs expire after 24 hours.

## Seedance behavior

Read [seedance.md](seedance.md) when `video.provider=seedance`. Seedance uses the official Volcengine task endpoint, configured Ark image asset IDs and inline Base64 audio in the live request, and a 4–12 second integer reference-video request duration. Shorter storyboard shots request 4 seconds and are trimmed; shots over 12 and up to 13 seconds request 12 seconds and are slowed uniformly during exact-frame local normalization; the original soundtrack speed is preserved. Saved requests replace inline media with hash markers, while execution state retains only local paths and hashes. The shared scheduler, polling, immediate download, optional Omni review, resume signature, normalization, and assembly behavior remain unchanged.

## Retry and completion invariant

Persist every request, response, task ID, provider identity, and rate slowdown event. Retry transient submit/generation failures within explicit ceilings. The run succeeds only when every selected segment reaches `succeeded`; stop rather than skipping a segment.

When Seedance is selected, skip image generation and asset registration. Every fixed character must
have a binding in `providers.seedance.official_identity_assets`; see [seedance.md](seedance.md).
