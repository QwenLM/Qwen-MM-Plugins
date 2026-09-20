# Qwen Image 3.0 image provider

Select it with `image.provider=qwen_image_3`. The adapter uses the synchronous Model Studio endpoint:

```text
POST {base_url}/api/v1/services/aigc/multimodal-generation/generation
```

The default `base_url` is `https://dashscope.aliyuncs.com` and requires a Beijing-region API key,
with no workspace ID setup. Workspace-specific endpoints remain optional; see
[model configuration](../../../references/model-configuration.md).
Put the key only in the environment variable named by
`api_key_env`; never put its value in config or state.

The provider maps character identity prompts to `input.messages[].content`. Optional input images are placed before the text and enable image-to-image generation. Music2MV intentionally requires `n=1`: each cast ID owns exactly one resumable identity record. Qwen supports at most three input images; `prompt_extend_mode=agent` is rejected when an input image is present.

Supported provider controls are `model`, `size`, `character_size`, `prompt_extend`, `prompt_extend_mode`, `enable_thinking`, `negative_prompt`, `character_negative_prompt`, `seed`, `watermark`, `workers`, and `max_attempts`. `character_size` optionally overrides the default `size` for identity portraits, and `character_negative_prompt` overrides `negative_prompt`. Sizes are normalized to `WIDTH*HEIGHT`; total pixels must remain between `512*512` and `2048*2048`, with an aspect ratio between 1:8 and 8:1. Seed must be between 0 and 2147483647.

Qwen returns PNG URLs that expire after 24 hours, so the pipeline downloads every successful image immediately and records the local `.png` path. Provider name, schema, endpoint, model, generation parameters, prompt, input references, and upstream asset IDs are included in the image execution signature. Changing any of those values invalidates the previous image for reuse without deleting it.

Run `base-assets` for the cast required by the selected shots. Review identity fidelity, exact portrait person count, face quality, and absence of text or watermarks. Wan uses each downloaded identity PNG directly. When Seedance is selected, the pipeline skips Qwen Image generation and uses the configured Ark image asset IDs.
