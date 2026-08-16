# Vision Chat

## vision_chat

Calls a vision-language model via DashScope.

### Environment variables

| Variable | Fallback order |
|---|---|
| `base_url` | `DASHSCOPE_BASE_URL` → DashScope URL |
| `api_key` | `DASHSCOPE_API_KEY` → `EMPTY` |

### DashScope setup

```
base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
api_key: your DASHSCOPE_API_KEY
```

### Tips

- Use `dry_run=true` to inspect the request payload without making a call
- Local video files are auto-extracted into frames (controlled by `video_max_frames`, default 128)
- `vl_high_resolution_images=true` requests an image token limit of 16384 (up to 16M pixels); if
  the endpoint rejects this optional hint with 400/422, the call retries at its default resolution

### Model selection

| Model | Use case |
|---|---|
| `qwen3.7-plus` | Default (vision_chat / ocr / grounding) |
