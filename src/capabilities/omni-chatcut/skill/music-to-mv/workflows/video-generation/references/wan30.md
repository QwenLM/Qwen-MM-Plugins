# Wan 3.0 Adapter

Select the adapter with `video.provider=wan3`. Configure endpoint, model, and `api_key_env` under
`video_providers.wan3` in the unified model config, then export the named credential variable.
The default `base_url` is `https://dashscope.aliyuncs.com` and requires a Beijing-region API key,
with no workspace ID setup. Submission uses `/api/v1/services/aigc/video-generation/video-synthesis`;
polling uses `/api/v1/tasks/{task_id}`. Workspace-specific endpoints remain optional; see
[model configuration](../../../references/model-configuration.md). Model, endpoint, and key must
belong to the same region.

The adapter submits one asynchronous task per storyboard segment, and every segment contains exactly
one continuous editorial shot:

```json
{
  "model": "wan3.0-video",
  "input": {
    "prompt": "图1……图2……音频1……",
    "media": [
      {"type": "reference_image", "url": "data:image/jpeg;base64,..."},
      {"type": "reference_audio", "url": "oss://dashscope-instant/.../segment.wav"}
    ]
  },
  "parameters": {
    "resolution": "720P",
    "ratio": "adaptive",
    "duration": 10,
    "audio": false,
    "prompt_extend": false,
    "watermark": false
  }
}
```

Wan numbers image, video, and audio references independently. Music2MV therefore renders identity portraits as `图1`, `图2`, and so on, while the single-shot soundtrack is `音频1`. The prompt supplies the complete scene description rather than a scene image.

The Wan adapter reads the selected image provider's identity portraits from execution state and sends them as ordinary reference images. Each request generates exactly one continuous editorial shot. Semantic review checks that shot for identity consistency, exact cast count, absence of unrequested internal cuts or scene changes, action adherence, and audio-conditioned motion.

The official protocol and current parameter limits are maintained at:

- <https://help.aliyun.com/zh/model-studio/wan3-video-generation-api-reference>
- <https://help.aliyun.com/zh/model-studio/wan3-video-generation-guide>
- <https://help.aliyun.com/zh/model-studio/get-temporary-file-url/>
