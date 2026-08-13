---
name: qwen-mm-plugins-core
description: Local MCP tools to read and visualize any file — images, video, documents, code, data, 3D, NIfTI, notebooks, and more — plus image tools for cropping, annotating, and extracting frames.
---

# Qwen-MM-Plugins Core

You have `qwen-mm-plugins-core` MCP tools available. Use them to read and visualize any file (images, videos, documents, code, data, 3D models, NIfTI volumes, notebooks, etc.) and to crop/annotate images. Prefer these MCP tools over manual scripting.

Check the `qwen-mm-plugins-core` tools in your tool list for full schemas and parameters.

## When to Use Which Tool

Native reading (feeds content directly to you):
- **Inspect metadata FIRST** for any video/audio (duration, resolution, fps, codecs, bitrate, audio/video/subtitle tracks, rotation, chapters) → `media_info`. Run it before `read_video` and before any clip/edit — see *Metadata first* below.
- **See a file** (PDF, Office, CSV, code, notebook, 3D, ...) → `visualize`
- **Read an image** with dynamic resolution → `read_image`
- **Read a video** (extract frames) → `read_video`
- **Save specific frame(s)** of a video to file → `save_view` (pass `times=[...]`)

Producing / annotating (writes an image file):
- **Crop a rectangular region** from an image → `crop`
- **Draw bounding boxes** on an image → `draw_bbox`

## Visualize — Supported Formats

| Category | Extensions | Notes |
|----------|-----------|-------|
| Documents | `.pdf`, `.svg` | Built-in (pypdfium2 + resvg) |
| Office | `.docx`, `.pptx`, `.vsdx` | Needs `libreoffice` |
| Data | `.csv`, `.xlsx` | Text table + chart image |
| Code | `.js`, `.ts`, `.py`, `.go`, `.rs`, `.md`, ... | Returns text (markdown code block) |
| Plain text | `.txt`, `.text`, `.log` | Returns text (fenced block) |
| Web pages | `.html`, `.htm`, `.mhtml` | Screenshot; needs `playwright` |
| Diagrams | `.drawio` | XML → SVG rendering |
| Subtitles | `.srt`, `.vtt` | Returns text |
| 3D Models | `.obj`, `.stl`, `.glb`, `.gltf`, `.fbx`, `.ply`, `.step`, `.stp` | Built-in; `blender` for best quality |
| Medical volumes | `.nii`, `.nii.gz` | Local, read-only via `nibabel`; 3D center slices + metadata; 4D defaults to first volume |
| GIS/Geo | `.geojson`, `.kml`, `.shp` | Built-in |
| Notebooks | `.ipynb` | Text cells + embedded images |
| LaTeX | `.tex` | Compiles to PDF; falls back to source on failure |
| Images/Videos | `.jpg`, `.mp4`, ... | Delegates to `read_image`/`read_video` |

Use `pages` for page ranges, `budget` for resolution, `max_pages` to cap output.

NIfTI visualization produces axial, coronal, and sagittal center slices without uploading the
source. It uses the closest-canonical voxel axes and does not resample oblique volumes. Treat it as
a file-inspection aid, not a clinical diagnostic tool.

## Metadata First

Always run `media_info` on a video/audio file **before** `read_video` and before any clip/trim/concat/mux — it reads only the header, so it is fast even on huge files, and it tells you how to treat the source. `read_video` also samples smarter once you know the fps and duration.

When juggling heterogeneous assets (action-cam clips, VFX/stock footage, voiceover, SFX), `media_info` is how you catch the traps that silently corrupt an edit — check for:
- **VFR** (variable frame rate — common on phones/action cams): `media_info` flags it as `possibly VFR`. Frame-accurate cuts need a constant-fps conform first (a video-edit step) — never assume a fixed fps on VFR sources.
- **Rotation** (`rotation 90°/180°`): the display orientation differs from stored pixels; bake it in before cropping or overlaying.
- **Mismatched fps / resolution / DAR** across clips → pick one project timebase and conform the outliers.
- **Audio sample rate & channels** (voiceover vs music vs SFX often differ, e.g. 44.1k vs 48k, mono vs stereo) → resample to one project rate before mixing.
- **No audio track** / **odd container start_time** → `media_info` calls these out; both shift or break naive timestamp math.

## Tips

**Resolution budgets**: `small` for preview, `normal` (~1024) default, `large` for fine detail.

**Video strategy**: `fps=0` auto-selects. Skim with `fps=1, budget="normal"` in 5-min chunks, then detail with `fps=2, budget="large"` on interesting segments. Use `start_time`/`end_time` for windowing.

## Relationship to Other Capabilities

Core is local-only. Cloud model/API calls live in separate capabilities — install them if you need those:
- **Understand media with a model** → `qwen-mm-plugins-api`, grouped by model family: VL (`vision_chat`, `ocr`, `grounding`), Omni A/V (timestamped captioning, multi-speaker ASR, temporal grounding, event counting), plus `transcribe_audio` and `segmentation`. Annotate its `grounding` output with `draw_bbox` here.
- **Confirm a fact / identify an entity** (reverse image + web) → `qwen-mm-plugins-search`. Grab the frame with `save_view` here first.
