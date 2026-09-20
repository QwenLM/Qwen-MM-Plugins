# External Dubbing Service

The plugin intentionally does not load IndexTTS2, Demucs, Torch, or TEN VAD inside the MCP process. Deploy
those GPU-heavy dependencies separately and configure `QWEN_MM_DUBBING_SERVER_URL`. The current compatibility
contract is:

This Skill includes `references/launch_dubbing_server.py` as a reference deployment matching this
contract.

- `GET /health` returns `model_loaded` and `status`.
- `POST /tts` accepts `text`, `voice_base64`, `voice_filename`, trim controls, and `return_audio=true`; the
  video-translation client leaves emotion controls at their service defaults. It returns `audio_base64`,
  duration, and sample rate.
- `POST /separate` accepts `audio_base64`, `audio_filename`, `two_stems="vocals"`, `mp3=false`, and
  `return_audio=true`; it returns base64 `vocals` and `no_vocals` stems.
- `POST /vad` accepts base64 audio plus TEN-VAD thresholds and returns ordered speech intervals.

The plugin never treats server-local output paths as portable artifacts. It decodes returned audio into the
project and records only project-local paths.

The client normally uses Python `requests`. If the host's endpoint-security or network-filter layer rejects
the Python socket with `EBADF` (`Bad file descriptor`), the client automatically retries the same request with
the system `curl` executable. Large JSON bodies are passed through a private temporary file rather than command
line arguments. Other connection and HTTP errors are not silently rerouted.

Production deployments should enforce authentication, request-size limits, isolated temporary directories,
cache expiry, GPU concurrency limits, and sanitized health/error responses. Keep checkpoints and third-party
model licenses outside the plugin package.
