# Cookbook — Qwen-MM-Plugins Video Edit

`qwen-mm-plugins-video-edit` pairs a video-editing **skill** with DashScope **generation** tools —
image, TTS, digital human, and text/image→video. The model can generate missing assets (title cards,
voiceover, B-roll, transition stills) and stitch them together with the user's real footage into a
finished edit: vlogs, montages, intros, recaps, style replications, subtitled and voiced-over cuts.

Perception (actually *watching* the footage) comes from the sibling
[`core`](../core/usage.md) capability — install both for the full loop:
**watch → plan → generate → assemble → review**.

---

## Generation tools (DashScope)

| Tool | Model | Use it for | Modes / notes |
|------|-------|------------|---------------|
| `qwen_image` | Qwen-Image | Images: generate, edit, translate text in image | `text_to_image` · `image_edit` (1-3 input images) · `image_translate` (layout-preserving). Sync. |
| `qwen_tts` | Qwen3-TTS-Flash | Voiceover / narration | 10 languages, 44 system voices (`Cherry`, `Serena`, `Ethan`, …). ~512 tokens per call — split long scripts at sentence boundaries. |
| `wan_s2v` | Wan2.2-S2V | Digital-human lip-sync video from one portrait + audio | `detect` (always run first — checks the portrait) then `generate`. Audio ≤ 20 s; real humans and cartoon characters. |
| `wan_t2v` | Wan 2.7 | Text→video, or animate from a first (and last) frame | `text_to_video` · `first_frame` · `first_last_frame`. 2-15 s, up to 1080p. Sync — blocks until ready. |
| `happyhorse` | HappyHorse 1.0 | Video generation with reference fusion, and text-driven video editing | `text_to_video` · `image_to_video` · `reference_to_video` (fuse 1-9 reference images, cited as `[Image 1]`…) · `video_edit` (edit an existing ≤15 s clip by instruction). Async, 1-5 min typical. |

Generated asset URLs expire in 24 h — pass `output_dir` to download immediately. All tools accept a
`seed` for reproducibility; fix it while iterating prompts.

## The editing skill

The skill is an **editing director**, not a command runner: it watches the source material first,
writes a taste contract and a per-scene plan into a project log, generates assets sample-first
(one sample must pass before any batch), assembles scene by scene, and refuses to deliver until an
evidence-based review gate passes. Key behaviors:

- **Footage in → this skill.** Any deliverable built from real footage the user supplies is owned
  here; pure motion-graphics from a brief route to the HyperFrames pipeline directly.
- **Two engines.** Mechanical work (trim, mux, technical fixes) runs on FFmpeg; designed
  deliverables (titles, transitions, overlays, beat-synced cuts) are handed to the
  [HyperFrames](https://www.npmjs.com/package/hyperframes) pipeline for assembly and rendering.
- **Built-in style library.** Named looks (paper-collage, freeze-punch, film-reel, neon-ui-tech, …),
  bundled fonts / SFX / BGM, and end-to-end workflows for multi-source vlogs and style replication.
- **Verified delivery.** Loudness, black-frame, and scene gates are shell scripts, not vibes —
  outputs land under `<videos_dir>/edit/` as versioned files (`final.mp4`, `final_v2.mp4`, …).

---

## Install

```bash
claude plugin marketplace add https://github.com/QwenLM/Qwen-MM-Plugins.git
claude plugin install qwen-mm-plugins-video-edit@qwen-mm-plugins

# recommended alongside — perception for watching the footage
claude plugin install qwen-mm-plugins-core@qwen-mm-plugins
```

Or use the guided installer (`bash install.sh install`), or launch the MCP server straight from a
source checkout:

```bash
uv run --extra video-edit qwen-mm-plugins-video-edit
```

## Prerequisites

The **generation** tools call remote APIs — `uvx` installs their Python deps and they need no system
tools beyond `DASHSCOPE_API_KEY`. The **editing** side runs locally:

```bash
# ffmpeg + ffprobe — every local edit (cuts, frame ops, loudness / black-frame gates)
apt install ffmpeg                      # macOS: brew install ffmpeg

# Python previews (timeline / contact sheet); librosa+scipy only for music-beat-sync
pip3 install pillow numpy
pip3 install librosa scipy              # optional — beat-sync work only

# Node.js ≥ 22 + npm/npx — designed deliverables (HyperFrames handoff)
# Linux (apt ships an old Node — use NodeSource):
curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt install -y nodejs
# macOS: brew install node@22
# or portable, via nvm:
#   curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash && nvm install 22
node -v                                 # verify: must be >= 22
npx -y hyperframes doctor               # fetches + verifies the hyperframes CLI

# HyperFrames agent skills — the video-edit handoff routes designed deliverables into these
# (hyperframes, hyperframes-core, hyperframes-creative, hyperframes-registry, hyperframes-cli, media-use);
# installs into Claude Code & other AI tools
npx -y hyperframes skills               # install; `npx -y hyperframes skills check` verifies latest

# Headless Chrome for HyperFrames check/render (or set PUPPETEER_EXECUTABLE_PATH)
npx hyperframes browser ensure
# minimal Linux only — Chrome OS libs + CJK font:
apt install libnss3 libatk-bridge2.0-0 libgbm1 libasound2 libxkbcommon0 libgtk-3-0 fonts-noto-cjk

# GSAP, vendored per project — seek-safe motion in the render
npm install gsap && mkdir -p assets && cp node_modules/gsap/dist/gsap.min.js assets/gsap.min.js
```

One command checks all of it, reporting OK / MISSING / WARN per item with install hints. Run it
from the skill directory — the installed plugin's skill folder, or
`src/capabilities/video-edit/skill/` in a source checkout:

```bash
bash scripts/check_env.sh
```

📖 Full dependency table, install commands, and intranet-CA notes: the skill's [SKILL.md § Environment & dependencies](../../src/capabilities/video-edit/skill/SKILL.md#environment--dependencies). General setup: [installation.md](../../docs/en/installation.md).

## Environment variables

| Variable | Description |
|----------|-------------|
| `DASHSCOPE_API_KEY` | Required — all generation tools (`qwen_image`, `qwen_tts`, `wan_s2v`, `wan_t2v`, `happyhorse`) call DashScope. |
| `DASHSCOPE_BASE_URL` | Optional — override the DashScope base URL (proxies/gateways). |

> Set these via env vars, `~/.qwen-mm-plugins/config`, or the guided installer **`bash install.sh`** (`bash install.sh verify` checks what's set).

---

## Cases

Each recording shows the full loop: the prompt goes in, the agent works (watch → plan → generate →
assemble → review), and the final cut is played back at the end.

### Case 1 — family vlog from raw father-and-son clips

Raw home footage in a directory → a warm, playful family vlog with chapter titles, stickers, cute
subtitles, split screens, photo-collage moments, and soft transitions.

> 请将当前目录中的家庭父子互动素材剪成一支温暖、活泼、可爱且富有设计感的家庭 Vlog，围绕父子陪伴与欢乐互动组织叙事，精选自然真实的表情和动作，运用轻快剪辑、趣味分屏、照片拼贴、手绘贴纸、可爱字幕与柔和转场丰富画面层次，搭配明亮温暖的调色和轻松音乐，最终呈现真实、有爱又充满童趣的家庭时光。

▶ **[View the session recording](https://qianwen-res.oss-accelerate.aliyuncs.com/Qwen-MM-Plugins/asserts/video-edit/case-video-edit-cc-family-vlog.mp4)**

### Case 2 — install the plugins, then cut a tech-style city promo

One session, two prompts: the agent first installs `core` + `video-edit` from the repo URL, then
turns city-night / traffic-timelapse footage into a fast-paced, futuristic promo with split screens,
multi-cam grids, picture-in-picture, and animated data overlays.

> 帮忙安装一下这个 https://github.com/QwenLM/Qwen-MM-Plugins/ 里面的 core 和 video-edit 吧
>
> 请将当前目录中的城市夜景和车流延时素材剪成一支节奏快速、科技感与未来感强烈的城市宣传片，通过分屏、动态拼贴、画中画、多镜头网格及丰富的科技动效提升视觉层次，最终呈现高速运转、充满活力的未来都市。

▶ **[View the session recording](https://qianwen-res.oss-accelerate.aliyuncs.com/Qwen-MM-Plugins/asserts/video-edit/case-video-edit-cc-city-promo.mp4)**
