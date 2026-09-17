# Model Connection Configuration

Use one JSON file for every remote model connection in Music-to-MV. Copy
`assets/model-config.example.json` outside the installed Skill, edit its endpoints, model IDs, and
credential environment-variable names, then store its path through the shared Qwen-MM-Plugins
configuration:

```bash
bash install.sh --setup
```

Select **Omni ChatCut** and set `QWEN_MM_OMNI_CHATCUT_MODEL_CONFIG` to the absolute JSON path. This
writes the value to `~/.qwen-mm-plugins/config`, so GUI-launched harnesses can read it. A process
environment variable with the same name may override that shared value.

The same file is read by direct `omni_call` requests, caption analysis, image generation, video
generation, and optional Omni shot QC. The pipeline CLI also accepts
`--model-config /absolute/path/to/model-config.json`; `omni_call` accepts the equivalent
`model_config_path` argument. An explicit path wins over
`QWEN_MM_OMNI_CHATCUT_MODEL_CONFIG`.

## Schema

- `omni` configures the endpoint, model, and credential variable used for music analysis and shot QC.
- `image_providers.<name>` configures each selectable image provider.
- `video_providers.<name>` configures each selectable video provider.
- `base_url` is the provider endpoint. It may contain `{workspace_id}` when the adapter supports it.
- `workspace_id_env` optionally names the environment variable used to replace `{workspace_id}`;
  the shared-domain defaults do not need it.
- `api_key_env` names the environment variable that contains the credential.
- `model` is the provider model ID.

This file stores only environment-variable names, never credential values. Fields named `api_key`,
`key`, `token`, or `access_token` are rejected when the file is loaded. Export the named credential
variables in the harness environment or place them in the shared Qwen-MM-Plugins user config.

## Default DashScope connections

Omni uses `https://dashscope.aliyuncs.com/compatible-mode/v1`. Qwen Image and Wan use
`https://dashscope.aliyuncs.com`; their adapters append the native `/api/v1/...` paths. Do not add
`/compatible-mode/v1` or `/api/v1` to the image/video `base_url`.
These Beijing-region defaults need only `DASHSCOPE_API_KEY`, with no workspace ID setup.

For an optional workspace-specific Qwen Image or Wan endpoint, set its `base_url` to
`https://{workspace_id}.cn-beijing.maas.aliyuncs.com`, add
`"workspace_id_env": "DASHSCOPE_WORKSPACE_ID"`, and provide that variable and a key belonging to
the workspace. Model, endpoint, and key must be in the same region.
See the [official Base URL reference](https://www.alibabacloud.com/help/en/model-studio/base-url).

## Precedence and compatibility

For pipeline execution, explicit values in the per-run `--config` file override the unified model
file, which overrides built-in defaults. This preserves older run configs that contain
`image_providers` or `providers`. New configs should keep connection settings in the unified model
file and use the run config only for provider selection, generation controls, rate limits, QC policy,
subtitles, and assembly.

To migrate an existing project to the shared domain, update its copied model file and any legacy
run-config endpoint overrides. Repository default changes do not rewrite user-owned config files.

For direct Omni calls, explicit `model`, `base_url`, or `api_key` arguments override the unified
file. Without a unified file, the existing `QWEN_MM_API_OMNI_MODEL`, `DASHSCOPE_BASE_URL`, and
`DASHSCOPE_API_KEY` fallbacks remain available.

## Official Seed providers

Seedream and Seedance use `https://ark.cn-beijing.volces.com` as `base_url`; adapters append `/api/v3`.
Use an official Ark API key. Replace any older gateway URL in both the unified model file and legacy
run configs, since run-config connection fields take precedence.

Seedance fixed characters use user-provided Ark image assets (public or uploaded/authorized),
or the local public pool when unspecified. Bind them per cast ID in
`providers.seedance.official_identity_assets` in the run config. An empty mapping is only usable when
no shot requires a character. Seedance skips portrait generation and asset registration; no image key
or AK/SK is required. See [Seedance](../workflows/video-generation/references/seedance.md).
