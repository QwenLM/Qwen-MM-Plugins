---
name: qwen-mm-plugins-edu-agent
description: |
  Generate step-by-step math problem-solving tutorial videos in Chinese (Mandarin).
  Use when: (1) a user provides a math problem and wants an explanation video,
  (2) someone says "make a math tutorial", "explain this equation", "create a
  teaching video for this problem", "讲解这道题", "生成解题视频",
  (3) the user wants a Chinese-language math lesson covering formulas, equations,
  or geometric figures, (4) the user shares a math problem in text or LaTeX and
  asks for a video walkthrough, (5) the input is an image_assets/ folder
  containing problem images — the skill will extract the problem via visual
  recognition, solve it, and generate a tutorial video. Teaching components are
  rendered as realistic objects (solid opaque panels, 3D cards, SVG figures) with a
  modern aurora mesh aesthetic.
---

# Math and Science Tutorials

Turn a math or science problem into a step-by-step Mandarin video tutorial. For an interactive page instead of a video, use the interactive workflow linked below. Teaching components — equations, diagrams, solution steps — are rendered as realistic objects: solid opaque panels (glass-look via borders + layered shadow, NO `backdrop-filter`, NO translucency), 3D metallic cards, animated SVG constructions, on themed backgrounds with animated gradient orbs and smooth ambient effects. The default theme is "Aurora Scholar" (blue wave texture + indigo/violet/cyan orbs); 4 alternative light themes are available (清雅湖蓝, 柔紫轻盈, 薄荷清新, 暖黄纸感) for visual variety across different problem types — see the Background Theme Catalog in design-system.md.

## Prerequisites (环境准备 — 开工前必查)

This is a **skill-only** capability (no MCP server), so its runtime dependencies are **NOT** auto-installed by `uvx`. Verify ALL of the following before Step 0 — a missing one silently breaks a later step:

| Dependency | Needed for | Install / check |
|------------|-----------|-----------------|
| **Node.js + npm/npx** | scaffold + render (`npx hyperframes`) | `node -v` (≥18) |
| **hyperframes CLI** | `init` / `lint` / `validate` / `render` | pulled on demand via `npx hyperframes` (needs npm-registry access at scaffold time; the project then pins a version in `dist/package.json`) |
| **Headless Chromium + OS libs** | `npx hyperframes render` (puppeteer) + post-render QA gates (`postcheck.py` / `precheck.py` drive headless Chrome) | the browser itself is auto-downloaded by puppeteer on first `npx hyperframes`; on **minimal Linux** you must also `apt install libnss3 libatk-bridge2.0-0 libgbm1 libasound2 libxkbcommon0 libgtk-3-0 fonts-noto-cjk` (else Chrome fails to launch, or CJK/formulas render as tofu boxes). Reuse a system Chrome via `export PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium`. |
| **Python 3 + pip** | TTS script | `python3 -m pip --version` |
| **`dashscope` `soundfile` `numpy` `requests`** | Step 3 TTS synthesis + assembly | `python3 -m pip install dashscope soundfile numpy requests` |
| **ffmpeg** | loudness normalization (`loudnorm`) + frame extraction for self-check | `brew install ffmpeg` / `apt install ffmpeg` |
| **`DASHSCOPE_API_KEY`** | Qwen-TTS (`qwen3-tts-flash`) | `export DASHSCOPE_API_KEY="sk-xxx"`, **or** put `DASHSCOPE_API_KEY=sk-xxx` in `~/.qwen-mm-plugins/config` for GUI-launched setups that don't inherit shell exports. `$EDU_SKILL_ROOT/scripts/generate_voice.py` reads it at runtime — **never `cat`/paste the key into the conversation.** |

> **Network boundary:** `npx hyperframes init` and the TTS calls need internet. The *render* itself is air-gapped — that is why fonts / KaTeX / GSAP must be self-hosted into `dist/` (see Step 5 Prerequisites). DashScope TTS may rate-limit under high concurrency; if you hit `Throttling.RateQuota`, lower the thread-pool worker count and add backoff (see step-3).

Before Step 0, resolve `EDU_SKILL_ROOT` to the **absolute directory containing this `SKILL.md`**. All
shipped scripts and assets must be addressed through that root; they are not in the user's project.
Shell tool calls do not necessarily share state, so set it in every command block that uses it (or
substitute the resolved absolute path directly); never rely on an earlier shell invocation:

```bash
EDU_SKILL_ROOT="<absolute directory containing qwen-mm-plugins-edu-agent/SKILL.md>"
test -f "$EDU_SKILL_ROOT/scripts/precheck.py" || { echo "invalid EDU_SKILL_ROOT"; exit 1; }
```

## Pipeline Overview

| Step | Name | Artifact | Reference |
|------|------|----------|-----------|
| 0 | Image Input & Problem Extraction | `PROBLEM.md` | [step-0-image-input.md](references/step-0-image-input.md) |
| 1 | Problem Analysis | `ANALYSIS.md` | [step-1-problem-analysis.md](references/step-1-problem-analysis.md) |
| 2 | Teaching Script | `SCRIPT.md` | [step-2-teaching-script.md](references/step-2-teaching-script.md) |
| 3 | Voice Generation | `narration.wav` + `transcript.json` + `captions.json` (sentence-by-sentence TTS, no Whisper) | [step-3-voice-generation.md](references/step-3-voice-generation.md) |
| 4 | Storyboard | `STORYBOARD.md` | [step-4-storyboard.md](references/step-4-storyboard.md) |
| 5 | Build Components | `compositions/*.html` | [step-5-build-components.md](references/step-5-build-components.md) |
| 6 | Compose & Render | `index.html` + MP4 | [step-6-compose-render.md](references/step-6-compose-render.md) |

## Step 0: Image Input & Problem Extraction

Read [references/step-0-image-input.md](references/step-0-image-input.md).

Read all images from the `image_assets/` folder using the `Read` tool (Claude's multimodal vision directly interprets image content). Extract the complete problem text, convert all math expressions to LaTeX, and describe any figures or diagrams. If JSONL metadata is available (subject, sub_subject, question_type, stepwise_explanation), use it as context hints but treat the image as ground truth.

<HARD-GATE>
`PROBLEM.md` must exist with: complete problem text in Chinese, all math expressions in LaTeX, and figure descriptions (if applicable). All images in `image_assets/` must have been read.
</HARD-GATE>

## Step 1: Problem Analysis

Read [references/step-1-problem-analysis.md](references/step-1-problem-analysis.md).

Parse the input math problem, classify its type, extract knowledge points, and produce a complete solution outline with numbered steps. When `PROBLEM.md` exists from Step 0, use it as the primary input source.

<HARD-GATE>
`ANALYSIS.md` must exist with: problem statement, type classification, solution steps, and final answer — all verified for correctness.
</HARD-GATE>

## Step 2: Teaching Script

Read [references/step-2-teaching-script.md](references/step-2-teaching-script.md).

Write the Chinese narration script with scene markers. Apply math symbol pronunciation rules. Target pacing: 3.5-4.0 Chinese characters per second.

<HARD-GATE>
`SCRIPT.md` must exist with scene-separated narration text in Chinese. All math symbols converted to spoken Chinese.
</HARD-GATE>

## Step 3: Voice Generation

Read [references/step-3-voice-generation.md](references/step-3-voice-generation.md). TTS strategy: **DashScope Qwen-TTS via the official SDK** (`dashscope.MultiModalConversation`, model `qwen3-tts-flash`, HTTP — returns a WAV URL) — no self-hosted TTS node, no URL wiring. **Speed + accuracy:** synthesize every sentence **concurrently** through a thread pool (wall-clock ≈ the slowest single sentence, not the sum), then measure each returned clip's real duration for exact per-sentence timestamps. No Whisper dependency.

Generate standard Mandarin TTS audio. Each sentence's exact duration is measured from its returned audio clip, producing both `narration.wav` and `transcript.json` in one pass with 100% accurate timestamps. No Whisper transcription needed.

<HARD-GATE>
`narration.wav`, `transcript.json`, and `captions.json` must exist. Audio must be loudness-normalized with `ffmpeg loudnorm` (EBU R128, -16 LUFS). Audio is generated with DashScope Qwen-TTS (`dashscope.MultiModalConversation`, model `qwen3-tts-flash`) — `DASHSCOPE_API_KEY` required. Timestamps are measured from TTS output (not estimated). Text in transcript/captions comes from the original script. Timestamps mapped to scene boundaries.
</HARD-GATE>

## Step 4: Storyboard

Read [references/step-4-storyboard.md](references/step-4-storyboard.md). Read [design-system.md](design-system.md) for visual tokens.

Design per-scene visual layout, assign component templates from [math-components.md](references/math-components.md), and plan transitions. Check [assets/ASSET_CATALOG.md](assets/ASSET_CATALOG.md) to identify which pre-built visual components can be reused in each scene. **Select a background theme** from the Background Theme Catalog in design-system.md — set it in the Global Direction block (default: `aurora-scholar`).

<HARD-GATE>
`STORYBOARD.md` must exist with per-scene direction: component type, layout, animation choreography, and transition choice.
</HARD-GATE>

## Step 5: Build Components

Read the `hyperframes` skill — every composition authoring rule applies. Read the `gsap` skill for animation patterns. Read [references/step-5-build-components.md](references/step-5-build-components.md). Read [references/math-components.md](references/math-components.md) for component templates. Read [assets/ASSET_CATALOG.md](assets/ASSET_CATALOG.md) for pre-built visual components. **For geometry diagrams (几何图形)**, read [references/geometry-construction-guide.md](references/geometry-construction-guide.md) for coordinate computation patterns, angle arc construction, and complete worked examples (triangles, quadrilaterals, circles, rotations, reflections); also read [references/golden-example-geometry.md](references/golden-example-geometry.md) for a complete golden pipeline (PROBLEM → HTML compositions) showing the proven split layout, draw-on animation choreography, and narration-synced note blocks from a top-quality geometry proof video. **For circuit schematic diagrams (电路图)**, read [references/circuit-schematic-guide.md](references/circuit-schematic-guide.md) for physics rules and SVG symbol templates. **For any scene that must ANIMATE A PROCESS with real motion (生物过程/受力矢量/波动/滴定/运动演示 — things that split, move, get pulled, or a quantity that changes across stages), read [references/example-process-animation.md](references/example-process-animation.md)** — a top-scoring few-shot (洋葱有丝分裂) with two complete reference scenes and copy-me techniques: staged phase reveal, split-and-move, **connectors that track a moving object and shorten (纺锤丝/绳/矢量牵引)**, live count-up, and a stepped quantity-vs-stage chart (plus the no-360°-spin rule). **🧬 MANDATORY for ANY 染色体/细胞分裂题目 (有丝分裂/减数分裂/染色单体/着丝点/纺锤丝/移向两极): you MUST open and copy [references/examples/mitosis-anaphase.scene.html](references/examples/mitosis-anaphase.scene.html)** — the spindle fibers must be animated `<line>`s that TRACK the moving chromatids and SHORTEN (pull them to the poles), with a live 染色体数 count-up, and NO 360° spin on chromosomes. This is enforced at render time by `scripts/check_chromosome_example.py` (a chromosome cell-division scene without the fiber-shorten tween, or with a 360° spin, FAILS precheck).

Scaffold the project with `npx hyperframes init dist --non-interactive --example blank` (the `--example blank` flag is required — hyperframes ≥0.7.77 rejects a bare `--non-interactive`). **The project root is the current workspace and the scaffold dir MUST be exactly `dist/` at that root — never nest it inside another folder (e.g. NOT `math-tutorial-output/dist`); the renderer and all gates assume `./dist`.** **Build ONE composition per scene listed in `STORYBOARD.md` — one special ray / one solution step / one concept per scene; NEVER cram several planned scenes into a single composition (that is the #1 cause of under-rendered, blank, or half-empty scenes and of run-to-run instability).** **Before writing custom HTML for any visual object**, check the [assets/](assets/) directory — it contains 83 pre-built K12 components (motion, optics, circuit, mechanics, fluid, chemistry, wave, indicators, math), including 6 circuit schematic symbols (`sch-battery`, `sch-ammeter`, `sch-voltmeter`, `sch-switch`, `sch-bulb`, `sch-resistor`). If a matching component exists, copy its CSS + HTML + JS hooks verbatim into the composition instead of building from scratch.

<HARD-GATE>
**>>> MANDATORY: run precheck and loop until it passes <<<**

```bash
EDU_SKILL_ROOT="<absolute directory containing qwen-mm-plugins-edu-agent/SKILL.md>"
python3 "$EDU_SKILL_ROOT/scripts/precheck.py" dist
```

This script auto-fixes LaTeX escaping, then runs all validation checks. **You MUST see `ALL CHECKS PASSED` in the output before proceeding to Step 6.** If any check shows `FAIL`:

1. Read the FAIL message — it prints the exact file, line, and fix instruction
2. Apply the fix to the offending file(s)
3. Re-run `python3 "$EDU_SKILL_ROOT/scripts/precheck.py" dist`
4. **Repeat steps 1-3 until the output contains `ALL CHECKS PASSED`**

**Do NOT proceed to Step 6 with failing checks. Do NOT skip precheck. Do NOT treat precheck failures as warnings.**

---

The precheck validates all of the following (each is also a standalone script in `scripts/`):

- **Asset mirror** — GSAP/KaTeX/fonts mirrored into `compositions/` (sub-compositions resolve `./` relative to their own directory; without the mirror, GSAP/KaTeX/fonts silently fail → blank scenes)
- **Composition format** — sub-compositions are HTML fragments, not full `<!doctype>` documents (full docs → blank panels)
- **No CDN URLs** — no `cdn.jsdelivr.net` or external `src`/`href` (render sandbox is air-gapped → all external loads fail silently)
- **KaTeX CJK** — no Chinese inside `katex.render()` or `data-tex` (KaTeX fonts lack CJK → tofu)
- **CJK font on Chinese text (中文必须用中文字体)** — any SVG `<text>`/`<tspan>` or inline-styled element whose text contains Chinese MUST use a stack including `Noto Sans SC` (e.g. `font-family="Noto Sans SC, Inter, sans-serif"`); `Inter`/`sans-serif` alone has no CJK glyphs so Chinese renders as **NO GLYPH / 豆腐块**. Don't mix scripts in one `<text>` — split Latin (Inter) and Chinese (Noto Sans SC) into separate `<tspan>`/`<text>`. Gate: `scripts/check_cjk_font.py`
- **KaTeX escaping** — LaTeX in JS strings double-escaped (`\\\\dfrac`, not `\\dfrac`; JS eats single `\\d` → "dfrac12" instead of fraction)
- **Unrendered fractions** — no literal "dfrac"/"frac{" leaked into visible HTML text
- **Caption size** — font-size 36–40px (not 64px giant subtitles)
- **Caption pinned to bottom (字幕固定在视频下方)** — every caption bar lives ONLY in `index.html`'s root track and is styled `position:absolute; bottom:48px; left:50%; transform:translateX(-50%)`; NEVER use `top:`, and NEVER put a caption inside a scene composition (it would drift to the top/middle). Gate: `scripts/check_caption_position.py`
- **Caption safe zone** — every scene reserves the bottom ~180px (content in the top ~900px) so the subtitle never covers content (字幕遮挡)
- **Caption overflow** — the caption bar is width-bounded (`max-width:1600px`, never `white-space:nowrap`), uses `width:max-content` (so it uses the full width before wrapping instead of shrinking to the ~960px half-frame), and each cue is short (split long sentences into sequential one-line cues); the subtitle never runs off the frame edge (字幕超出边界). Gate: `scripts/check_caption_overflow.py`
- **Caption always on top (字幕必须在最顶层)** — the caption bar has an unbeatable `z-index` (`2147483647`) so it is ALWAYS above every scene layer/panel and can never be covered (字幕被遮挡); scenes must keep their z-index small (<100). Gate: `scripts/check_caption_overflow.py`
- **Scene layout** — `.scene-content` is a centering box that fills the frame (`position:absolute;inset:0;display:flex;align-items:center;justify-content:center`); filling alone (no flex-center trio) piles a single panel at the TOP with the bottom half empty (排版问题). Gate: `scripts/check_scene_layout.py`
- **No oversize/overflow (排版过大)** — every element fits within 1920×1080 (content within the 1920×900 safe area with edge margins); no CSS `width`>1920px / `height`>1080px, keep `scale()` ≤ ~1.5; size by container (%/max-width/flex/grid), scale SVG via viewBox. Hero text ≤ ~96px is guidance (decorative watermarks may be larger). Gate: `scripts/check_scene_overflow.py`
- **SVG height must be bounded (SVG高度必须有界 — 防止场景突然变大)** — never size a content `<svg>` as `width:100%; height:auto` with a tall/near-square viewBox: `height:auto` ties the rendered height to the viewBox aspect ratio, so at full panel width a `1000×760` viewBox becomes ~1050px tall and overflows the ~860px usable height — the scene appears to "suddenly get big" (场景突然变大). Bound the height: give the SVG `max-height:<usable>px` (e.g. `max-height:760px`), or size it by height inside a bounded flex/grid box with `preserveAspectRatio="xMidYMid meet"`, or choose a viewBox whose aspect ratio matches the available box. Deterministic gate: `scripts/check_svg_height_bound.py`; the headless-measured `scripts/check_scene_fit.py` is the runtime backstop.
- **No overlapping SVG labels (SVG标签禁止重叠)** — inside a single `<svg>`, no two `<text>` labels may collide. Don't stack a fulcrum/center label and its arm/segment labels on one shared line; put the center label above and arm labels below (anchored `end`/`start`, pushed outward past the shape edge) with a gap of ≥ ~1 label-height; stagger point labels that share an axis and never let a point label bury an axis tick number (see Rule #28). Gate: `scripts/check_svg_label_overlap.py`
- **Node-graph diagrams clean (关系图/流程图)** — in a boxes+arrows+in-shape-label diagram: every in-shape label is in the same `<g>` as its shape (else it slides off → white字消失), white SVG text only ever sits inside a dark shape, arrow endpoints stop before the 方框 (no arrowhead poking into a box), edge labels stay in the gaps (see Rule #31). Gate: `scripts/check_svg_node_graph.py`
- **No CSS-hidden content** — no `opacity:0` / `visibility:hidden` in CSS; GSAP handles via `autoAlpha:0` in JS
- **No frosted glass** — no `backdrop-filter`, panels are opaque `#ffffff`
- **Scene coverage** — one composition per storyboard scene, all wired with `data-composition-src` and `window.__timelines[...]`
- **Root refs** — `index.html` uses `data-composition-src` (not `data-src` or `src`) for all scenes
- **Geometry verification** — every geometry scene (SVG with 3+ labeled points) has a `<!-- GEOMETRY VERIFICATION -->` block; all `ASSERT` lines verified mathematically (coordinates satisfy parallel/perpendicular/midpoint/intersection/ratio constraints)

Additional requirements NOT checked by precheck (manual verification):
- Self-hosted fonts + KaTeX + GSAP copied into `dist/` AND mirrored into `dist/compositions/`
- Chinese font NOT subsetted — one full `NotoSansSC-Bold.woff2` per weight
- KaTeX CSS inlined with local font URLs
- No gradient text on KaTeX formulas (use solid `color`, not `background-clip:text`)
- Grid/lattice figures generated programmatically with deduped edges (not hand-typed coordinates)
</HARD-GATE>

## Step 6: Compose & Render

Read [references/step-6-compose-render.md](references/step-6-compose-render.md). Invoke the `hyperframes-cli` skill for CLI commands.

Assemble root `dist/index.html`, run lint + validate + inspect, preview, and render. **All CLI commands (lint, validate, render) must run from inside `dist/`.**

**🔎 MANDATORY after rendering — visual overlap self-check loop (渲染后抽帧自查，看到重叠必须改坐标重渲，直到不重叠):** `precheck.py` now includes `check_render_overlap.py`, a headless render-truth gate that measures REAL bounding boxes and FAILs when a `<text>` is painted under/over a box it doesn't belong to (文字被方框覆盖/压框 — e.g. 取食→取, 捕食→捕; static coordinate gates miss this because scaling + CJK glyph width + SVG paint order make a "clear on paper" label collide in the render). That gate must pass. **In addition, you MUST look at the pixels:** for every scene with boxes/connectors/labels/charts, extract a settled frame (`ffmpeg -ss <scene_midpoint> -i output.mp4 -frames:v 1 /tmp/ov.png`), open it with the Read tool, and check by eye for **文字被框压/裁切、框和线重叠、框对不齐，以及方向/朝向/语义错误**（figure 画反/朝向错：燃着木条火焰须在瓶内而非瓶口外、斜面朝向、箭头矢量方向、仪器插入端、倾倒口朝向、电池极性/电流方向、图表是否真的体现所述趋势）. If you see ANY overlap/misalignment/wrong-orientation, **edit the offending coordinates (move labels into the gap with clearance / shorten connectors / align boxes / draw highlight rings inside the box / flip the reversed figure), re-render, and look again — repeat until every frame is visually clean and physically correct.** Never claim "visually verified" without having actually extracted and read the frames. Full loop in [references/step-6-compose-render.md](references/step-6-compose-render.md) → "Post-Render Visual Overlap Self-Check".

**🚫 MANDATORY after rendering — line/curve render-truth gate (线/曲线漏画检测):** run `python3 "$EDU_SKILL_ROOT/scripts/postcheck.py" dist` and loop until it prints `ALL POST-RENDER CHECKS PASSED`. Its `check_curves_rendered.py` is renderer-agnostic: it renders a KNOWN-GOOD reference of each scene (GSAP + fonts injected by absolute path), then checks that every solid line/curve that scene should draw is **actually painted in `output.mp4`**. This catches the most deceptive render bug — a graph that shows its dots/axes/labels but drops the connecting curves (**"有点、没线"**) because JS-generated path `d` never ran (typically a hand-rolled renderer that stripped GSAP without re-injecting it by absolute `file://`). Pre-render gates cannot catch this — the composition is correct; only the rendered pixels reveal the loss. **Prevention: render with `npx hyperframes render`; do NOT hand-roll a CDP/puppeteer renderer — and if you must, re-inject GSAP/KaTeX/fonts via absolute `file://` paths (as you already do for bg-texture), never strip `./gsap/gsap.min.js` and leave it.** Full section in [references/step-6-compose-render.md](references/step-6-compose-render.md) → "Post-Render Line/Curve Render-Truth Gate".

**🌊 MANDATORY after rendering — smooth-curve render-truth gate (曲线不能用折线逼近，看到折线必须改代码重渲，直到通过):** the same `postcheck.py` now also runs `check_smooth_curve_render.py`. It loads each continuous-curve scene (抛物线/双曲线/正弦/指数/反比例衰减 …), seeks the animation to its settled state, then **walks the ACTUAL rendered curve geometry** (`getPointAtLength`) and FAILs when a curve is drawn as a handful of straight chords — a jagged zig-zag with sharp kinks and long dead-straight runs between them (`<path d="M488,585 L500,540 L530,480 …">` 这类 ~7 段直线拼出来的"曲线"). This is a **visual/render-truth** check: it sees the rendered shape, so it also catches curves whose `d` is generated by JS (which the static pre-render `check_smooth_curve.py` cannot see). **When it FAILs, you MUST rewrite that curve's code and re-render, looping until it passes** — do NOT ship a jagged curve. **Fix:** build the path `d` with the Catmull-Rom `smoothPath()` helper (sparse control points → cubic Béziers), OR emit a densely-sampled point list (~1 pt per ≤10 px, ~120+ pts across a wide axis). Genuinely piecewise-linear graphs (`y=|x|`, 分段函数, 折线统计图, 匀速距离-时间) are exempt. See references/step-5-build-components.md → "Smooth curves (顺滑曲线)".

<HARD-GATE>
**>>> Pre-render gate: precheck must have passed <<<**

Before rendering, confirm that `python3 "$EDU_SKILL_ROOT/scripts/precheck.py" dist` was already run in Step 5 and printed `ALL CHECKS PASSED`. If you skipped it or are unsure, run it now — it takes seconds. Do NOT render with failing prechecks.

`npx hyperframes lint` and `npx hyperframes validate` pass with zero errors (run from `dist/`). Video rendered to MP4 via `npx hyperframes render` inside `dist/`. The output `.mp4` file must exist inside `dist/` and be reported to the user. Preview-only is NOT sufficient — rendering is mandatory. **After rendering: `check_render_overlap.py` passes AND you have extracted+viewed a frame from every box/connector/label/chart scene and confirmed no clipped/covered text, no line-into-box, no misaligned boxes — fixing coordinates and re-rendering until clean. AND `python3 "$EDU_SKILL_ROOT/scripts/postcheck.py" dist` prints `ALL POST-RENDER CHECKS PASSED` (every line/curve the scene should draw is actually painted in output.mp4 — 线/曲线没漏画).**
</HARD-GATE>

## Design System

Read [design-system.md](design-system.md) before writing ANY HTML. It defines the "Aurora Scholar" light-themed visual identity. Use its exact color tokens, font specs, and component styles. Do not invent colors.

## Problem Type Reference

| Problem Type | Typical Duration | Scenes | Key Components |
|---|---|---|---|
| Single equation | 30-60s | 4-5 | Problem Card + 2-3 Formula Panels + Conclusion |
| Multi-step algebra | 60-120s | 6-8 | Problem Card + Analysis + Steps + Summary |
| Geometry proof | 90-150s | 7-10 | Problem Card + Geometry Canvas + Proof Steps |
| Word problem | 60-90s | 5-7 | Problem Card + Modeling + Solve + Verify |

## Non-Negotiable Rules

1. **Caption text from original script (字幕必须用原始脚本文本).** Video captions/subtitles MUST use text from `captions.json` (which contains the original narration script text with timestamps measured from TTS output). The timestamps are 100% accurate because they are measured from each sentence's actual TTS audio duration during sentence-by-sentence synthesis. The OpenCC `t2s` conversion is no longer needed — transcript text comes directly from the original script.
2. **KaTeX for layout-dependent math; plain HTML/Chinese for simple symbols (复杂公式用KaTeX，简单符号用HTML或中文).** Use KaTeX only when the expression needs math layout features (fractions, roots, summations, matrices). For simple comparisons and standalone symbols (≤, ≥, °, ×), prefer HTML entities or Chinese text directly — e.g., write `<span>OP' ≤ 1</span>` not `katex.render("OP' \\leq 1", ...)` — this avoids the fragile JS backslash-escaping pipeline entirely (see Rule #25). Never display raw LaTeX source code. **When you DO use KaTeX in JS strings, ALWAYS double-escape LaTeX backslashes** — write `"\\\\dfrac{1}{2}"` not `"\\dfrac{1}{2}"`. The `\\d` escape is silent: JS turns `\\d` → `d`, so `\\dfrac` becomes `dfrac` and KaTeX renders italic text "dfrac12" instead of a fraction. This is the single most common rendering bug. After building all compositions, run `python3 "$EDU_SKILL_ROOT/scripts/check_katex_escaping.py" dist` and fix every reported line BEFORE proceeding to Step 6.
3. **Chinese pacing.** Narration at 3.5-4.0 characters/second. Leave 0.5-1.0s pauses between steps.
4. **Three layers per scene.** Background treatment (wave texture + aurora mesh orbs) + content layer + accent elements. No flat single-layer scenes. See design-system.md "Background Treatment" for the exact CSS pattern and aurora palette guide.
5. **Solid opaque panels — frosted glass is FORBIDDEN (禁止毛玻璃，完全避免遮挡).** All content panels use the solid panel style from design-system.md — OPAQUE `background:#ffffff` (or white alpha ≥ 0.92), depth from borders + layered box-shadow + inset top highlight. **NEVER** use `backdrop-filter` / `-webkit-backdrop-filter`, and **NEVER** a see-through translucent panel background — a blurred/see-through panel washes out and OCCLUDES the problem text / diagram / formulas behind it (this is a hard defect). Enforced by `scripts/check_no_glass.py` (in `precheck.py`): render is blocked if any glass/translucent panel is found.
6. **SVG geometry.** Geometric figures use SVG path drawing animation, never static images.
7. **Deterministic.** No `Math.random()`, `Date.now()`, or async timeline construction.
8. **Delegate.** Use the `hyperframes` skill for composition rules, `hyperframes-cli` for CLI commands. TTS uses **DashScope Qwen-TTS via the official SDK** (`dashscope.MultiModalConversation`, model `qwen3-tts-flash`, HTTP) — no self-hosted node, no URL wiring; needs `DASHSCOPE_API_KEY`. Synthesize sentences **concurrently** (thread pool) for speed, and take per-sentence timestamps from each returned clip's measured duration (see step-3-voice-generation.md). This skill defines the math-and-science tutorial domain logic only.
9. **Dark text on light backgrounds (浅色背景深色文字).** This is a light-theme design system. All text — KaTeX equations, SVG labels, Chinese body text, HTML table cells — MUST use dark colors (`#0f172a` or darker). Every composition MUST include the full "Mandatory Global Color Reset" block from design-system.md: root selector with `color: #0f172a`, `.katex, .katex * { color: #0f172a; }`, `.katex-mathml { display: none !important; }`, and `table, th, td { color: #0f172a; }`. Never use `#fff`, `#f8fafc`, `#e8ecf4`, or any light color for text on the light background.
10. **Pre-built assets first.** Before writing custom HTML/CSS for any visual object (car, train, candle, battery, lens, etc.), check [assets/ASSET_CATALOG.md](assets/ASSET_CATALOG.md). If a matching component exists, copy its CSS and HTML verbatim. If NO match exists, follow the "Quality Fallback Template" in ASSET_CATALOG.md — every custom object must have ≥3 gradient layers, inset shadows, ground shadow, glow halo, and no CSS @keyframes. Compare against the candle component for quality level.
11. **No emoji.** Never use emoji characters (🔧⚙️🔵🔴🟢⭕📐✅❌⚡💡 etc.) anywhere in visible text — titles, labels, captions, formula notes, phase titles, badge text, or any string rendered in the video. Headless Chromium has no emoji font installed; all emoji render as □ (tofu boxes) in the final video. Use plain Chinese text or SVG/CSS shapes instead.
12. **Math symbols: simple via HTML entities, complex via KaTeX — NO Chinese inside KaTeX (简单符号用HTML实体，复杂公式用KaTeX，KaTeX禁止包含中文).** Common math comparison and operator symbols are safe as HTML entities in Noto Sans SC / Inter: ≤ (`&le;`), ≥ (`&ge;`), ≠ (`&ne;`), ° (`&deg;`), × (`&times;`), ÷ (`&divide;`), ± (`&plusmn;`), ² (`&sup2;`), ³ (`&sup3;`) — use these directly in HTML text instead of KaTeX when no layout structure is needed (see Rule #25). **Greek letters (α, β, γ, δ, ε, θ, λ, μ, π, φ, ω) are safe as Unicode characters in HTML text** — Inter-Variable.woff2 contains all Greek glyphs (Noto Sans SC does NOT). The font-family stack `"Noto Sans SC", Inter, sans-serif` will fall back to Inter for Greek. **NEVER write the English word** ("alpha", "beta", "theta") **— always use the Unicode character** (α, β, θ). Do NOT insert other specialized Unicode math symbols (∠, △, ⊥, ∥, √, ∞, ₁, ₂, etc.) directly into HTML text — use KaTeX for these in formulas, or Chinese equivalents in non-formula context ("角ABC" not "∠ABC", "三角形" not "△"). For inline subscripts/superscripts in labels, use HTML `<sub>`/`<sup>` tags (e.g., `F<sub>1</sub>` not `F₁`). The only safe non-ASCII characters in plain text are standard CJK Unified Ideographs (U+4E00–U+9FFF), common CJK punctuation, the HTML-entity math symbols listed above, and Greek letters (U+0370–U+03FF). **CRITICAL: Never put Chinese text inside KaTeX `\text{...}` or any KaTeX command.** KaTeX's math fonts (KaTeX_Main, KaTeX_Math, etc.) do NOT contain CJK glyphs — any Chinese character inside `\text{}` renders as □ tofu boxes. When a formula needs to be mixed with Chinese text, break it into separate HTML elements:
    ```html
    <!-- WRONG — Chinese in \text{} renders as □□□ -->
    <span id="eq" data-tex="n \text{ 还是 } V"></span>

    <!-- CORRECT — Chinese in HTML, math in KaTeX -->
    <span>判断操作对 </span><span id="eq-n" data-tex="n"></span><span> 或 </span><span id="eq-v" data-tex="V"></span><span> 的影响</span>
    ```
13. **KaTeX CSS must be inlined, fonts self-hosted (字幕和公式的CSS必须内联，字体离线).** The HyperFrames compiler processes CDN `<link rel="stylesheet">` tags by extracting ONLY `@font-face` rules and discarding all other CSS. This silently breaks KaTeX layout (fractions, subscripts, spacing render as flat plain text). **Never** use `<link rel="stylesheet" href="...katex.min.css">`. Instead: copy the shipped `assets/katex/katex.min.css` into `dist/katex/`, rewrite its font URLs to the **local** self-hosted path (`url(./katex/fonts/`), and paste the full CSS as `<style id="katex-inline-css">` in every composition and in `index.html`. Load `katex.min.js` from the local `dist/katex/` copy, **not** a CDN. See step-5 for the exact procedure.
14. **Circuit schematic physics accuracy (电路原理图物理正确性).** When drawing circuit schematic diagrams (电路图) in SVG: (a) battery symbol — **长线 = 正极(+), 短线 = 负极(-)**, never reverse the labels; (b) current direction — conventional current flows from battery `+` terminal through external circuit to `-` terminal, all arrows must form a consistent closed loop; (c) ammeter — must be wired in **series**, current enters `+` terminal; (d) voltmeter — must be wired in **parallel** with dashed branch wires (`stroke-dasharray`), `+` terminal toward higher potential; (e) **wire segmentation (导线分段)** — wires must STOP at component terminals, never draw a continuous wire that passes through components (components are NOT decorative overlays on wires); (f) **switch must break the circuit (开关必须断路)** — incoming wire ends at switch pivot, outgoing wire starts at contact terminal, switch orientation must match wire direction (vertical wire → vertical switch), no zero-length line segments; (g) **layout distribution (布局分布)** — distribute series components across multiple sides of the rectangular loop, do NOT stack all components on one side; **an empty side of the loop may be JUST a wire (导线) — never add/duplicate a component only to "fill" or "balance" a side**; (h) **no duplicate wires** — each wire gap between components is drawn exactly once; (h2) **component inventory correctness (元件清单正确性) — 每个真实元件恰好出现一次** — the set of components must match the problem's actual circuit; single-instance instruments (**变阻器/滑动变阻器, 电流表, 电压表, 开关, 电源**) must each appear EXACTLY ONCE — never invent or duplicate a component (drawing two 变阻器 in series is a physics error). Only true multiples in the problem (e.g. 两个灯泡 L₁/L₂, 两个电阻 R₁/R₂) may repeat. `check_circuit_inventory.py` gates this; (h3) **circuit loop MUST be closed / 电源两端必须都接线 (回路闭合)** — the main series loop is a single closed path: trace it from the 电源 `+` terminal through every component back to the 电源 `-` terminal — **every gap must have a wire, and the power source's BOTH terminals must each connect to a wire**. No unwired component terminal, no **dangling wire stub** (悬空导线端点 — a wire end that lands in empty space instead of on a component terminal or another wire at a corner). In physical wiring diagrams whose wire coordinates live in a JS array (`var wires=[["w1",x1,y1,x2,y2],…]`), it is easy to forget the segment that closes the left side back up to the source — **do not**. `check_circuit_closed.py` gates this; (i) **physical wiring diagram layout (实物连接图布局)** — "连接电表" and "连接主回路" operation scenes must use a **rectangular loop** layout (NOT a flat horizontal line) with ammeter IN the series loop (on the bottom return wire) and voltmeter on a visually separate dashed parallel branch; wire routing stays compact (≤100px margin beyond components); use Circuit Wiring Operation (C12) template from math-components.md, NOT Chemistry Operation Flow (C9); see circuit-schematic-guide.md Section 12; (j) **voltmeter connection wires must not protrude (电压表连接导线禁止突出)** — the `sch-voltmeter` template has NO built-in wire stubs; terminal endpoints are at the circle edge (±30 from center, not ±50); all dashed connection wires must be drawn as separate `<line>` elements from the main circuit T-junction point to the voltmeter circle edge — wires must start at the junction and end at the circle edge, never extending past either point; (k) **voltmeter branch junctions must have dots and clean routing (电压表分支点必须有圆点且路由干净)** — every T-junction where a voltmeter dashed wire meets the main circuit wire must have a filled junction dot (`<circle r="4-5">`); the dashed wire endpoint coordinates must be exactly ON the main wire (no offset); position the voltmeter so its terminals align with junction points to allow straight dashed lines; if L-shaped routing is unavoidable, use a single `<path>` with `stroke-linejoin="round"` — NEVER two separate `<line>` elements (they create gaps/protrusions at corners). Use `sch-*` templates from [assets/ASSET_CATALOG.md](assets/ASSET_CATALOG.md) and read [references/circuit-schematic-guide.md](references/circuit-schematic-guide.md) for the full pre-flight checklist.
15. **Caption formulas use KaTeX inline rendering (字幕公式必须用KaTeX渲染).** Video captions must display proper math notation — never show spoken-form Chinese like "G M m除以R的平方". When assembling caption `<div>` elements in `index.html`, replace all spoken-form math expressions from `captions.json` with `<span class="cm">LaTeX code</span>` elements. The root `index.html` must load KaTeX JS and include a script that renders all `.cm` spans with `katex.render()` in inline mode. Use `\tfrac` for fractions (compact, fits single-line captions). **KaTeX angle pitfall:** for degrees use `^\circ`, for arcminutes use a plain ASCII `'` (prime), for arcseconds use `''`. **NEVER** use `\'` or `\"` in KaTeX math — these are text-mode accent commands that cause the entire formula to render as red error text. See step-6 for the full template, common replacements table, KaTeX pitfalls, and examples.
16. **Self-hosted offline fonts — Chinese MUST NOT be tofu (中文字体必须离线内嵌).** The font files are shipped with this skill under `assets/fonts/` and MUST be copied into `dist/assets/fonts/` (see step-5 Prerequisites). Every sub-composition file AND the root `index.html` MUST embed them via an inline `@font-face` `<style>` block (the exact block is in step-6 for the root and `assets/k12-scholar-font-template.css` for compositions). **Do NOT use a Google Fonts / CDN `<link>`** and do NOT rely on the compiler's built-in embedding or system fonts (PingFang SC): the render sandbox is air-gapped, so a CDN font that fails to load makes ALL Chinese render as garbled boxes (乱码). Use a **CJK-first** stack everywhere — `font-family: "Noto Sans SC", Inter, sans-serif` — and never `Inter, sans-serif` alone. **This applies no matter HOW font-family is set** — SVG `<text>` attr, inline `style=`, AND CSS **class rules**. The #1 miss is a small label/eyebrow/badge class (e.g. `.block-number{font-family:Inter,sans-serif}`) that the design intends for a Latin number like "01" but the model fills with Chinese (对称性/题目所给) → those 3–4 characters render as 豆腐块 while the rest of the card is fine. Any class that could ever hold Chinese must include `"Noto Sans SC"`. Enforced by `scripts/check_cjk_font.py` (now also scans class-based font-family, not just SVG/inline).
17. **No CDN/external URLs at render time (渲染时禁止任何CDN/外部URL).** Every `<script src>`, `<link href>`, and CSS `url()` in compositions and `index.html` MUST use local relative paths. The AP render sandbox is **air-gapped** with no internet access — CDN URLs that load successfully on a local machine will **silently fail** on AP, breaking GSAP timelines (no animations), KaTeX rendering (raw LaTeX in captions), and font loading (tofu boxes). Specifically: (a) GSAP — load from `./gsap/gsap.min.js` (shipped in `assets/gsap/`, copied to `dist/gsap/` AND `dist/compositions/gsap/` in step-5); (b) KaTeX JS — load from `./katex/katex.min.js`; (c) KaTeX CSS — inline as `<style>`, fonts from `./katex/fonts/`; (d) body fonts — inline `@font-face` from `./assets/fonts/`. **Never** use `cdn.jsdelivr.net`, `cdnjs.cloudflare.com`, `unpkg.com`, or any other external host in a `src` or `href` attribute. **CRITICAL: sub-compositions in `dist/compositions/` resolve `./` relative to their own directory — the assets MUST be mirrored into `dist/compositions/` (gsap/, katex/, assets/) or all `./` paths silently 404 and GSAP/KaTeX/fonts fail to load (blank scenes, no animations, tofu).** Enforced by `scripts/check_asset_mirror.py` in `precheck.py`.
18. **Defensive rendering — no CSS-hidden content (防御性渲染 — CSS禁止隐藏内容).** The #1 cause of blank videos is CSS `opacity: 0` on `.scene-content` or content elements, combined with a JS error that prevents GSAP from revealing them. **Rules:** (a) **Never** set `opacity: 0`, `visibility: hidden`, or `display: none` on any content element in CSS — GSAP handles the initial hidden state dynamically via `autoAlpha: 0` in `fromTo()`; (b) Wrap the **entire** GSAP timeline construction in a `try-catch` block — if any tween fails, the timeline still registers and content stays visible; (c) Each `katex.render()` call must have its **own** `try-catch` — one bad LaTeX string must not crash the entire composition; (d) Always pass `throwOnError: false` to `katex.render()`. See step-5 "Defensive Script Pattern" for the complete code template.
19. **Reproducibility — stable structure every run (稳定复现 — 结构固定).** The same problem must yield the same well-formed video every time; the biggest source of run-to-run quality swing is the BUILD step deviating from the plan. **Rules:** (a) **Fixed output path** — scaffold and render in exactly `./dist` at the workspace root; never nest it (`math-tutorial-output/dist` etc.) — the renderer and gates assume `./dist`. (b) **One composition per storyboard scene** — build exactly the scenes listed in `STORYBOARD.md`, one concept/ray/step per scene; never merge several planned scenes into one file, never skip a planned scene. `index.html` must wire ≥ the planned scene count. (c) **Every scene self-contained & complete** — each composition draws its full content (e.g. an optics scene MUST include the horizontal principal axis through O with 2F/F/O/F′/2F′ marked) and registers `window.__timelines[...]`. (d) **Determinism** — no `Math.random()` / `Date.now()` / async timeline construction (see rule #7). (e) These are enforced deterministically by `$EDU_SKILL_ROOT/scripts/check_scene_coverage.py` and `$EDU_SKILL_ROOT/scripts/check_no_hidden_content.py` inside `$EDU_SKILL_ROOT/scripts/precheck.py` — `precheck.py dist` must print `ALL CHECKS PASSED` before rendering.
20. **Root MUST reference scenes via `data-composition-src` (根合成引用名固定 — 否则全白).** In `dist/index.html`, every scene clip MUST use the EXACT attribute **`data-composition-src="compositions/scene-X.html"`** — never `data-src`, `src`, or a missing src. HyperFrames only loads/compiles an external scene when it sees `data-composition-src`; any other name means **no scene loads and the whole video renders white** (only the caption bar shows). Also: each clip's `data-composition-id` must match that scene's registered `window.__timelines["<id>"]`, and scenes are laid out directly inside the root composition div (do NOT wrap them in an extra `data-composition-id="mt-root"` container). Enforced by `scripts/check_root_compositions.py` in `precheck.py`.
21. **几何图形坐标必须数学精确 (Geometry SVG coordinates must be mathematically exact).** When drawing geometric figures in SVG (Geometry Canvas scenes), all vertex coordinates MUST be computed from the problem's mathematical constraints (perpendicularity, parallelism, similarity ratios, length ratios, etc.) — **never placed by visual estimation**. Mandatory steps: (a) solve the problem's equations to get exact proportional relationships between all segments before writing any SVG; (b) choose a base unit in the SVG viewBox, then compute every point's coordinates from the ratios; (c) **verify perpendicularity** — if two lines are perpendicular, compute the dot product of their direction vectors and confirm it equals 0; (d) **verify length ratios** — compute Euclidean distances and confirm they match the problem's given ratios; (e) write a `<!-- GEOMETRY VERIFICATION -->` block above the SVG with a `POINTS:` line declaring all coordinates and `ASSERT` lines for every problem constraint (parallel, perpendicular, midpoint, on_line, on_segment, intersection, ratio, collinear) — the pre-render gate `scripts/check_geometry_verification.py dist` mathematically verifies every assertion against the declared coordinates and exits 0; (f) **right angle marks (直角符号)** must be drawn as small L-shaped squares (not V-shapes or arcs), with edges computed from the actual line direction unit vectors — see step-5-build-components.md "Geometry Coordinate Accuracy" for the verification block format, assertion types, and construction algorithms.
22. **角度弧线必须用叉积确定扫描方向 (Angle arcs must use cross-product to determine sweep direction).** When marking a non-right angle at vertex V with arms pointing to adjacent vertices P1 and P2 in SVG, the arc MUST sweep through the **interior** of the angle being marked. The sweep direction is determined by the cross product of the two arm vectors — never by visual estimation or guesswork. Algorithm:
    ```
    // 1. Direction vectors from vertex V to each arm endpoint
    dx1 = P1.x - V.x;  dy1 = P1.y - V.y;
    dx2 = P2.x - V.x;  dy2 = P2.y - V.y;

    // 2. Cross product (in SVG screen coordinates, y-down)
    cross = dx1 * dy2 - dy1 * dx2;

    // 3. Arc start/end points on each arm at radius distance
    angle1 = Math.atan2(dy1, dx1);
    angle2 = Math.atan2(dy2, dx2);
    startX = V.x + R * Math.cos(angle1);
    startY = V.y + R * Math.sin(angle1);
    endX   = V.x + R * Math.cos(angle2);
    endY   = V.y + R * Math.sin(angle2);

    // 4. Sweep flag: cross > 0 → interior is CW → sweep=1
    //                cross < 0 → interior is CCW → sweep=0
    sweepFlag = cross > 0 ? 1 : 0;

    // 5. SVG path (large-arc-flag = 0 for angles < 180°)
    d = `M ${startX},${startY} A ${R},${R} 0 0,${sweepFlag} ${endX},${endY}`;
    ```
    **Worked example — rhombus with ∠A = 60°, ∠B = 120°:**
    ```
    // Vertices: A(200,400) B(400,180) C(600,400) D(400,620), R=30

    // ∠A = 60° — arms AB and AD
    dx1=200, dy1=-220; dx2=200, dy2=220;
    cross = 200*220 - (-220)*200 = 88000 > 0 → sweep=1
    // Arc sweeps CW through interior (rightward between AB and AD) ✅

    // ∠B = 120° — arms BA and BC
    dx1=-200, dy1=220; dx2=200, dy2=220;
    cross = (-200)*220 - 220*200 = -88000 < 0 → sweep=0
    // Arc sweeps CCW through interior (downward between BA and BC) ✅
    ```
    **Common mistakes:** (a) Hardcoding sweep-flag=1 or sweep-flag=0 without computing cross product — causes arcs to appear on the exterior (reflex) side of the angle; (b) Drawing the arc between the wrong pair of arms (e.g., marking ∠ABC but using arms BA and BD); (c) Using a right-angle L-shaped mark for non-90° angles; (d) Forgetting that SVG y-axis points down, which reverses the visual rotation direction. **Always verify:** visually, the arc must curve TOWARD the interior of the polygon, not away from it.
23. **坐标分隔符只用逗号，禁止分号 (Coordinate separators: comma only, never semicolon).** When writing coordinate pairs, point notation, or ordered tuples in KaTeX (e.g., vertex coordinates, intersection points), use **only a comma** as the separator between components — never a semicolon, and never both. Add a KaTeX thin space `\,` after the comma for readability. **JS string escaping pitfall:** in a JavaScript string passed to `katex.render()`, write `\\,` (double backslash) so KaTeX receives `\,`. Writing `\;` in a JS string does NOT produce a KaTeX thick space — JS treats the unknown escape `\;` as a literal semicolon character `;`, so KaTeX renders a visible `,;` (comma followed by semicolon). This is a confirmed rendering bug. Correct examples:
    ```
    // In JS strings (double-escaped for JS → KaTeX):
    katex.render("(1,\\, b-1)", el);          // ✅ renders (1, b−1)
    katex.render("(x_0,\\, y_0)", el);        // ✅ renders (x₀, y₀)

    // WRONG:
    katex.render("(1,\; b-1)", el);           // ❌ renders (1,; b−1) — JS turns \; into literal ;
    katex.render("(1,\\; b-1)", el);          // ❌ renders (1, b−1) with excessive space

    // In HTML (e.g., <span class="cm"> for caption KaTeX):
    <span class="cm">(1,\, b-1)</span>        // ✅
    <span class="cm">(1,\; b-1)</span>        // ❌ overly wide space, inconsistent
    ```
    General rule: coordinates are `(a,\, b)` in LaTeX, `(a,\\, b)` in JS strings. No semicolons anywhere in coordinate notation — this is Chinese math convention (中国数学用逗号分隔坐标，不用分号).
24. **Sub-composition files must be HTML fragments, NOT full documents (子合成文件必须是HTML片段，不能是完整HTML文档).** Every file in `dist/compositions/*.html` must be an HTML fragment — a root `<div data-composition-id="...">` with inline `<style>` and `<script>` blocks. **NEVER** wrap sub-compositions in `<!doctype html>`, `<html>`, `<head>`, or `<body>` tags. HyperFrames loads sub-compositions by injecting their content into the parent document; a full HTML document wrapper prevents the renderer from properly executing GSAP scripts inside the sub-composition, causing all content elements to remain invisible (blank white panels with only background texture showing — audio and captions play normally, making the bug deceptive). Only the root `index.html` is a full HTML document. Enforced by `scripts/check_composition_format.py` in `precheck.py`.
25. **简单符号优先用中文或HTML实体，避免不必要的KaTeX (Prefer Chinese/HTML entities over KaTeX for simple math symbols).** KaTeX rendering in JS strings requires double-escaped backslashes (`\\leq` not `\leq`); a single-backslash error is **silent** — JS eats `\l`→`l`, `\a`→`a`, `\c`→`c`, producing garbled text like "OP'leq1" or "angleMPNleq90^circ" instead of proper symbols. **To avoid this fragile pipeline, prefer plain HTML or Chinese text whenever the expression does NOT need math layout features** (fractions, roots, matrices, complex sub/superscripts). Decision table:
    | Expression type | Display goal | Method | Code |
    |---|---|---|---|
    | Simple comparison | OP' ≤ 1 | HTML entity | `<span>OP' ≤ 1</span>` or `OP' &le; 1` |
    | Angle expression | 角MPN ≤ 90° | Chinese + entity | `<span>角MPN ≤ 90°</span>` |
    | Degree symbol | 90° | HTML entity | `90&deg;` or `90°` |
    | Multiplication | 3 × 4 | HTML entity | `3 &times; 4` |
    | Simple inequality | x ≥ 5 | HTML entity | `<span>x ≥ 5</span>` |
    | Fraction layout | ½ | **KaTeX** | `katex.render("\\\\frac{1}{2}", ...)` |
    | Complex formula | quadratic | **KaTeX** | `katex.render("\\\\frac{-b\\\\pm\\\\sqrt{b^2-4ac}}{2a}", ...)` |
    | Root expression | √(a²+b²) | **KaTeX** | `katex.render("\\\\sqrt{a^2+b^2}", ...)` |
    | Subscript variable | x₁, y₂ | HTML `<sub>` | `x<sub>1</sub>`, `y<sub>2</sub>` |
    | Greek letter | α, β, θ | Unicode char | `<span>α</span>` — NEVER write "alpha" |
    Safe HTML entities: `≤` (`&le;`), `≥` (`&ge;`), `≠` (`&ne;`), `°` (`&deg;`), `×` (`&times;`), `÷` (`&divide;`), `±` (`&plusmn;`), `²` (`&sup2;`), `³` (`&sup3;`).
    Safe Unicode Greek letters (in Inter font): α, β, γ, δ, ε, θ, λ, μ, π, φ, ω — use directly, never write English names.
    Chinese replacements for geometric symbols: "角" (∠), "三角形" (△), "垂直于" (⊥), "平行于" (∥).
    **Rule of thumb: if the expression is a simple comparison, a standalone symbol, or a Chinese-friendly geometric term, use HTML/Chinese. If it has fractions, roots, summations, or nested structure, use KaTeX.**
26. **SVG 坐标轴箭头必须用 orient="auto" + 朝右三角形 (Axis arrows: always use a single right-pointing marker with orient="auto").** When drawing coordinate axes in SVG, define ONE arrowhead marker with its polygon pointing in the **+x direction** of the marker's local coordinate system, and set `orient="auto"`. This same marker works for ALL axis directions (x-axis, y-axis, or any angle) because `orient="auto"` rotates the marker to match the stroke direction at the endpoint. **NEVER define a separate "upward" marker with a polygon pre-rotated to point up** — `orient="auto"` will rotate it an additional -90°, making the arrowhead point LEFT instead of UP. Correct pattern:
    ```html
    <defs>
      <!-- ONE marker for ALL axes — polygon points right (+x), orient="auto" rotates it -->
      <marker id="axis-arrow" markerWidth="10" markerHeight="7" refX="10" refY="3.5" orient="auto">
        <polygon points="0 0, 10 3.5, 0 7" fill="#0f172a"/>
      </marker>
    </defs>
    <!-- x-axis: line goes right → arrow points right ✅ -->
    <line x1="50" y1="400" x2="570" y2="400" marker-end="url(#axis-arrow)"/>
    <!-- y-axis: line goes up → orient="auto" rotates arrow to point up ✅ -->
    <line x1="300" y1="470" x2="300" y2="30" marker-end="url(#axis-arrow)"/>
    ```
    **Common mistake (y-axis arrow points LEFT):**
    ```html
    <!-- ❌ WRONG — polygon already points up, orient="auto" rotates it -90° more → points LEFT -->
    <marker id="arrowU" orient="auto"><polygon points="0 10, 3.5 0, 7 10"/></marker>
    ```
    **Why:** `orient="auto"` aligns the marker's local +x axis with the line's tangent direction. For a line from (300,470) to (300,30), the tangent is (0,−1) = upward, so the local +x rotates −90°. A right-pointing triangle (+x direction) correctly becomes upward. An already-upward triangle (−y direction) gets rotated to point left.
27. **受力箭头/坐标轴禁止加辉光滤镜 (NEVER apply a glow/blur `filter` to an axis-aligned `<line>`).** A perfectly horizontal or vertical `<line>` — every force arrow (重力 G↓ / 支持力 N↑ / 摩擦力 f / 拉力), coordinate axis, horizontal light ray, or wire — has a **degenerate bounding box** (zero height when horizontal, zero width when vertical). SVG filters default to `filterUnits="objectBoundingBox"` with a region of `-10%…120%` of the bbox, so a zero dimension makes the **filter region zero-area → the line AND its `marker-end` arrowhead render completely invisible.** The `<text>` label carries no filter, so it still shows — producing the deceptive symptom **"受力示意图里只有力的字(G/N/v)，没有力的箭头线条."** This bug hides on slanted rays (a diagonal line has a nonzero-area bbox and survives), so it only surfaces on vertical/horizontal force arrows. **Fix:** give force arrows a solid `stroke` (width 4–6) + `marker-end` and **NO `filter`** — a force arrow reads perfectly without a glow. If a glow on straight strokes is truly needed, make the filter absolute so it ignores the bbox: `<filter id="fGlow" filterUnits="userSpaceOnUse" x="0" y="0" width="<viewBoxW>" height="<viewBoxH>">…</filter>`. Glow filters remain safe on circles/particles/ions/text (non-degenerate bbox). Enforced by `scripts/check_svg_filter_bbox.py` in `precheck.py`.
28. **SVG 标签禁止相互重叠 (SVG `<text>` labels must NOT overlap each other).** SVG `<text>` is positioned by hand (absolute `x`/`y` in viewBox units), so labels crammed around a shared point or along a short line collide and the text mashes together (文字重叠). The classic failure is a pulley/lever diagram that places the fulcrum label `支点 O` and the two arm labels `动力臂 r` / `阻力臂 r` on the SAME horizontal diameter through the circle center — three centered labels in ~90px of room overlap into "动力臂 支点O 阻力臂 r". This class of overlap is invisible to the other layout gates (`check_scene_overflow.py` ignores SVG user coordinates; `check_scene_layout.py` only checks the DOM wrapper). **Rules:** (a) **never stack multiple labels on a shared point or a short segment** — give each label its own side with a clear gap of **≥ ~1 label-height** (≥ the label's `font-size` in viewBox units) between neighbouring boxes; (b) for a center/fulcrum + two arm labels, put the **center label ABOVE the line** and the **arm labels BELOW it** (or vice-versa) so their vertical bands don't collide; (c) **anchor arm/segment labels `text-anchor="end"` (left side) / `"start"` (right side)** and push them **outward past the shape edge** rather than toward the crowded center; (d) offset a label **perpendicular to the line/arm it names**, not along it; (e) when two point labels sit on the same axis (e.g. `A(-3,0)` and `E(-5,0)`), stagger their `y` or shorten to bare letters so their boxes clear. Do NOT let a point label sit on top of an axis tick number — move the tick or drop the redundant one. Enforced by `scripts/check_svg_label_overlap.py` in `precheck.py` (conservative: flags only substantial 2-D overlap, accounts for `<g transform="translate">` and GSAP-repositioned elements).
29. **受力/矢量箭头必须正确 (SVG force/vector arrows — fixed-size head, full-length line).** Hand-rolled SVG arrows (`<line marker-end>`) fail in three severe ways at once, as seen in a 蹦床受力 scene (giant sideways arrowheads with no shaft): (a) **箭头太大** — a `<marker>` with no `markerUnits` defaults to `markerUnits="strokeWidth"`, so the arrowhead is multiplied by the (thick, 6–8) force stroke → a ~72–96px triangle. **Fix:** `<marker markerUnits="userSpaceOnUse" markerWidth="14" markerHeight="14" refX=… refY=… orient="auto">` sized in absolute px. (b) **方向不对 + (c) 线没画出来** — drawing the `<line>` collapsed to a point (`x1==x2 && y1==y2`) and growing it via GSAP `attr:{y2}`: a zero-length line has no shaft, and `orient="auto"` on it is undefined so the head points sideways; if the attr tween doesn't seek at render, the shaft never appears. **Fix:** draw the line at its **full, correct length** (so orient is right and the shaft always exists) and reveal it with `stroke-dashoffset` draw-on — never animate a marker line's length from zero. **BEST:** reuse the prebuilt CSS `force-arrow` component (`assets/components/mechanics/force-arrow.html`) — fixed head, rotate for direction, `--arrow-length` for magnitude. Enforced by `scripts/check_svg_arrow.py` in `precheck.py`.
30. **图形标签禁止压在图形上 (SVG labels must NOT overlap the drawing — 文字不压线不压点).** A point/vertex/curve letter (A/B/O/F/M…) must never sit ON a line, axis, symmetry line, curve, or vertex dot — it must be **offset OUTWARD, away from the figure, by ~18–24 units**. Classic failure: a point `M` on the symmetry axis gets its label placed straddling that axis's x, so the dashed axis line runs straight through the "M". **Rules:** (a) place a small dot at the point and the **letter ~20u beside it, on the side away from the shape centre** (never at the exact point coordinate); (b) a point on the **x-axis** → label ~22u **below/above** the axis, not on it; the **origin `O`** → offset into a quadrant (`x−14, y+20`); (c) a label near a **vertical/symmetry line** → anchor `end` and push left (or `start`/right) so the stroke clears the glyphs; (d) a **curve** label sits in the empty area beside the curve. Nudging a label a little to clear the figure is expected and encouraged. This is distinct from label-vs-label overlap (Rule #28) — here the collision is label-vs-geometry. Enforced by `scripts/check_svg_label_on_figure.py` in `precheck.py`.
31. **关系图/流程图节点必须打组、箭头不压框、图形内白字必显 (SVG node-and-connector diagrams).** When a scene draws a food chain / feedback loop / flow diagram from SVG boxes + arrows + in-shape labels, three failures ruin it (seen in a 生态平衡 case: white `负反馈` invisible, arrows poking into the 方框). **Rules:** (a) **a label inside a shape MUST be in the SAME `<g>` as that shape** — `<g><circle/><text/></g>`, never independent siblings; otherwise a GSAP `scale`/pulse/`back.out` overshoot or `svgOrigin` residue moves the shape but not the text, so the label **slides off the shape onto the panel and disappears** (the "白色文字没显示" bug). Animate/position the group, not the shape alone. (b) **white SVG `<text>` is allowed ONLY inside a dark shape that contains it** (white-on-light is invisible; `#ffffff` text is otherwise forbidden — see Rule on light-theme colors), and it must be grouped with that shape per (a); size the shape so the text fits with margin. (c) **arrow/connector endpoints stop ≥ ~10u before the target box edge** (account for `markerWidth`) — never end a `marker-end` line/path inside or flush against a `<rect>` node, or the arrowhead hides under / pokes into the 方框. (d) **edge labels ("取食"/"捕食") go in the gap between boxes with clearance**, not against a box; space boxes so connector + label + clearances fit. Enforced by `scripts/check_svg_node_graph.py` in `precheck.py`.
32. **SVG 高度必须有界，禁止 `width:100%;height:auto` 撑爆场景 (SVG height must be bounded — never let width drive an unbounded height).** A content `<svg>` styled `width:100%; height:auto` sizes its width to the panel (~1380px in a full-width panel) and then, because `height:auto`, sets its **height from the viewBox aspect ratio** — a near-square viewBox like `0 0 1000 760` becomes **~1050px tall**, far past the ~860px usable height (1080 − top padding − 180px caption safe zone). The SVG overflows the frame and the scene looks like it **"suddenly got bigger" (场景突然变大)** than the earlier text scenes. This is invisible to the literal-px overflow gate (there is no `px > frame`). **Rules:** (a) **never** write `width:100%; height:auto` on a content SVG with a tall/near-square viewBox (and equally never `width:100%; aspect-ratio:1` — a CSS `aspect-ratio` drives the height from the width just the same); (b) **bound the height** — either add `max-height:<usable>px` (e.g. `max-height:760px`) so the SVG letterboxes, or size it **by height** (`height:100%` inside a bounded flex/grid box) and let `preserveAspectRatio="xMidYMid meet"` shrink the width to fit; (c) alternatively choose a **viewBox aspect ratio that matches the available box** (wide/landscape, ratio ≲ 0.5) so `width:100%` keeps the height under ~820px. The origin of the "后面的渲染场景突然变大" bug was exactly a 图象验证 (graph) scene with `#svg{width:100%;height:auto}` on a `1000×760` viewBox. Enforced deterministically by `scripts/check_svg_height_bound.py` (browserless) and measured at runtime by `scripts/check_scene_fit.py`, both in `precheck.py`.
33. **禁止对带定位 `transform` 属性的 SVG 元素补间 x/y/rotation/scale (never GSAP-tween a transform prop on a transform-positioned SVG element — 否则物体飞出画面).** An SVG element placed with a hard-coded transform attribute, e.g. `<g transform="translate(390,364) rotate(-30)">` (a block on an incline, a rotated arrow, a positioned symbol), must **NOT** be the target of a GSAP tween that animates a transform shorthand (`x`, `y`, `rotation`, `rotationX/Y/Z`, `scale`, `scaleX/Y`, `xPercent`, `yPercent`, `skewX/Y`). GSAP parses the element's existing `transform` into its own model, then the tween **overwrites** it — e.g. `fromTo("g[transform]", {y:10}, {y:0})` drives translateY from its real 364 down to 0, so the element **jumps ~364px off its position and flies out of frame** (物体飞出画面). The classic failure is a broad selector like `g[transform]` or a bare `g`/`rect` selector that happens to hit a positioned group. **Rules:** (a) on a transform-positioned SVG element, animate **ONLY** `opacity`/`autoAlpha` (never x/y/rotation/scale); (b) to also slide/scale it, **NEST** — keep the positioning transform on a **static outer `<g transform=...>`** and put the fade/slide tween on an **inner `<g>`** (or wrapper); (c) if you truly must animate its transform, use `attr:{transform:'translate(...) rotate(...)'}` with the **full** transform written out at both ends, never a bare `x`/`y` shorthand; (d) never target positioned groups with a broad `g[transform]` / bare-tag selector. Animating x/y/scale on elements **without** a transform attribute (plain circles, HTML panels positioned by CSS) is fine. **(e) 对 SVG 元素做 `scale`/`rotation` 缩放/旋转时，绝不要用 px 单位的 `transformOrigin`（如 `transformOrigin:"470px 270px"`）——在 SVG 上它按元素包围盒本地坐标换算，对一个子元素坐标远离 SVG 原点的 `<g>` 缩放会附带一个很大的平移量，把元素甩飞/错位（即使该 `<g>` 没有 transform 属性、纯靠子元素绝对坐标定位，也会飞——这正是"变阻器缩短动画把整个 R₁ 甩到画面外"的真实事故）。改用 GSAP 专给 SVG 的 `svgOrigin:"x y"`（用户坐标系，如 `svgOrigin:"470 270"`），或把元素套进 `<g transform="translate(...)">` 再对内层缩放。`transformOrigin` 用百分比（如 `"50% 50%"`）在 SVG 上是安全的。** Enforced by `scripts/check_svg_transform_anim.py` in `precheck.py`.

## Golden Examples (Few-Shot References)

Before building compositions for a new problem, **read the matching golden example** to see the complete pipeline (PROBLEM → ANALYSIS → SCRIPT → STORYBOARD → HTML compositions → index.html) executed at top quality. These are condensed from real high-scoring videos — no binary files, just the essential pipeline artifacts and representative composition code.

| Example | Problem Type | When to Read |
|---|---|---|
| [references/golden-example-geometry.md](references/golden-example-geometry.md) | Geometry proof (旋转、等边三角形、垂直平分线) | **Any geometry proof problem** — shows coordinate computation, GEOMETRY VERIFICATION blocks, split layout (SVG + note blocks), progressive draw-on animations synced to narration, proof-step overlay patterns |
| [references/golden-example-interactive-transform.md](references/golden-example-interactive-transform.md) | **Interactive HTML explorer (交互式网页, 不是视频)** | **When the user asks for an interactive page / 交互式界面 / 让用户拖动体验 (sliders/controls that update a graph AND the equation live), instead of an MP4.** Ships a complete offline `index.html` (Canvas graph + KaTeX live equation + dropdown/sliders/checkboxes/reset, Aurora theme) plus the 6 rules to reproduce it exactly. Skip the Step 0–6 video pipeline for this. |

> ⚙️ **Two output modalities.** Most requests → a narrated **MP4** via the Step 0–6 pipeline (use the
> geometry / process-animation examples above). But when the user explicitly wants an **interactive
> web page** (拖动参数、实时联动，不是视频), switch to the **interactive** golden example above — same
> Aurora look & offline assets, but the deliverable is a self-contained `index.html` opened by
> double-click, not a rendered video.

34. **KaTeX 必须在合成加载时【同步】渲染，并按 `id`/`data-composition-id` 作用域选择；禁止无 `id` 的 `#合成id` 选择器，禁止用 setTimeout 延迟渲染 (render KaTeX synchronously at load, scoped correctly; never a `#composition-id` selector without a matching `id`, never defer with setTimeout).** 已确认两类"公式空白"坑（画面空白，选项/步骤/公式看不到）：**(1) 缺根 `id`（确定性、最常见）** — 根 div 是 `<div data-composition-id="mt-x">`（`data-composition-id` 是 data 属性，不是 DOM id），但渲染循环用 `document.querySelectorAll("#mt-x [data-tex]")`；`#mt-x` **匹配 0 个** → 不渲染 → 公式全空。**修复**：给根 div 同时加 `id="mt-x"`（保留 `data-composition-id`），或用 `[data-composition-id="mt-x"]` / `document.currentScript.closest("[data-composition-id]")` 作 id 无关作用域。由 `scripts/check_composition_root_id.py` 拦截（q067 弧长公式只显示"="就是此因，加 `id` 后重渲已修复）。**(2) 必须同步渲染，切勿延迟** — HyperFrames 渲染器按 seek GSAP 时间轴的方式**确定性抓帧**，**不保证执行 `setTimeout`/`requestAnimationFrame` 等异步回调**；因此 KaTeX 要在合成内联 `<script>` **加载时同步渲染**（此时 index.html 已在 `<head>` 同步载入 katex，全局可用），**不要**把 `katex.render` 包进 `setTimeout`/轮询，也不要依赖 `onload`/末尾追加脚本（追加在根 `</div>` 之后的 `<script>` 注入时会被丢弃、根本不执行）。优先用静态 `data-tex` 属性 + 同步 `querySelectorAll` 渲染循环（见 step-5 Rule 4）。**已知未解边缘情形**：个别场景在 `hyperframes validate`/单独打开时公式正常、但最终 MP4 里仍空白（该场景的命令式 KaTeX 未被抓帧保留，如 q062 的标注框）——遇到务必以**渲染出的 MP4** 为准复核，必要时重写该场景标记。

## References Index

| File | When to Read |
|---|---|
| [design-system.md](design-system.md) | Before writing any HTML |
| [references/golden-example-geometry.md](references/golden-example-geometry.md) | **Few-shot example** — geometry proof pipeline (PROBLEM → HTML compositions) |
| [references/golden-example-interactive-transform.md](references/golden-example-interactive-transform.md) | **Few-shot example** — interactive HTML explorer (交互式网页, 非视频): live graph + live equation via sliders, offline self-contained; full reference index.html + 6 reproduce-rules |
| [references/example-process-animation.md](references/example-process-animation.md) | **Few-shot example** — process/motion animation (生物过程/受力/波动/演示): split-and-move, connectors that shorten & pull (纺锤丝/绳/矢量), live count-up, stepped chart; 2 complete reference scenes |
| [references/step-0-image-input.md](references/step-0-image-input.md) | Step 0 — when input is image_assets/ |
| [references/math-components.md](references/math-components.md) | When building scene compositions (Step 5) |
| [references/step-1-problem-analysis.md](references/step-1-problem-analysis.md) | Step 1 |
| [references/step-2-teaching-script.md](references/step-2-teaching-script.md) | Step 2 |
| [references/step-3-voice-generation.md](references/step-3-voice-generation.md) | Step 3 |
| [references/step-4-storyboard.md](references/step-4-storyboard.md) | Step 4 |
| [references/step-5-build-components.md](references/step-5-build-components.md) | Step 5 |
| [assets/ASSET_CATALOG.md](assets/ASSET_CATALOG.md) | 生成视觉对象前必读，83 个预置组件索引 |
| [references/circuit-schematic-guide.md](references/circuit-schematic-guide.md) | 电路原理图（电路图）绘制规范 — 电池极性、电流方向、电表接法 |
| [references/geometry-construction-guide.md](references/geometry-construction-guide.md) | 几何图形构造 — 坐标计算模式、角度弧线、三角形/四边形/圆/旋转/翻折实例 |
| [references/step-6-compose-render.md](references/step-6-compose-render.md) | Step 6 |
