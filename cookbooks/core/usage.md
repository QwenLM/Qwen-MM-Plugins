# Cookbook — Qwen-MM-Plugins Core

`qwen-mm-plugins-core` is the local file capability: it reads media at model-optimized resolution,
visualizes documents and structured files, and writes crops, annotations, document pages, and video
frames back to disk. It makes no cloud API call and needs no API key.

Cloud understanding lives in [`qwen-mm-plugins-api`](../api/usage.md). Web and reverse-image
verification lives in [`qwen-mm-plugins-search`](../search/usage.md).

---

## Tools

**Read and inspect**

- `read_image` — read an image at dynamic resolution
- `read_video` — sample video frames with automatic FPS and resolution
- `media_info` — inspect container, video, and audio metadata with ffprobe
- `visualize` — render PDF, Office, CSV, code, SVG, DrawIO, 3D, NIfTI, GIS, notebook, and LaTeX files

**Write image views**

- `crop` — crop a normalized `0–1000` box from an image
- `draw_bbox` — draw normalized `0–1000` boxes and labels on an image
- `save_view` — save document pages or selected video frames as standalone image files

For exact schemas, check the installed Skill or the MCP tool list.

---

## Install

```bash
claude plugin marketplace add https://github.com/QwenLM/Qwen-MM-Plugins.git
claude plugin install qwen-mm-plugins-core@qwen-mm-plugins
```

`core` needs no API key. Video tools require ffmpeg/ffprobe; NIfTI visualization uses nibabel; other
formats may need LibreOffice, TeX, Chromium, Blender, or FreeCAD. Run `bash install.sh verify` to
check system dependencies for the installed capability.

### NIfTI volumes

`visualize` opens `.nii` and `.nii.gz` files locally and read-only. For a 3D volume it returns
metadata plus three uniformly spaced interior slices from source voxel axis 2 by default. For a 4D
image it treats the first item on the fourth dimension as a 3D volume unless `pages` explicitly
selects other volumes. Source voxel axes are not guaranteed to be anatomical axes; the affine is
used only to orient each 2D view and report its closest anatomical plane, without resampling.

The default `auto_volume` intensity mode computes one P1–P99 display range from the selected 3D
volume and applies it consistently to every rendered slice. Small volumes use exact statistics;
large volumes use a deterministic volume-wide sample to limit memory. The result reports resolved
slice indices, volume selection, statistics method, and effective display range so the operation can
be checked. Missing spatial units default to millimetres. The source is never uploaded. This feature
is for inspection and visualization, not clinical diagnosis.

```text
@scan.nii.gz  Show the default three source-axis-2 slices and effective configuration.
@scan.nii.gz  Show five uniformly spaced interior slices from source voxel axis 1.
@scan.nii.gz  Show source-axis-2 indices 40, 80, and 120 using window center 40, width 400.
@scan.nii.gz  Show the default slices with the ct_lung display preset.
@series.nii.gz  Show volumes 1 and 3; use the same default 3D treatment for each.
```

The corresponding NIfTI-only `visualize` arguments are:

- Sampling: `nifti_slice_axis`, `nifti_num_slices`, `nifti_slice_indices`, and
  `nifti_slice_positions` (normalized from 0 to 1).
- Intensity: `nifti_intensity_mode` (`auto_volume`, `window`, or `preset`),
  `nifti_percentile_low`, `nifti_percentile_high`, `nifti_window_center`,
  `nifti_window_width`, and `nifti_window_preset`.
- Fourth dimension: the existing 1-based `pages` range selects volumes; `max_pages` caps how many
  volumes are returned.

Available CT display starting points are `ct_brain`, `ct_soft_tissue`, `ct_lung`, and `ct_bone`.
They are never selected automatically because a NIfTI header does not reliably identify modality or
intensity units.

---

## Cases

### Case 1 — read a video, then extract Figure 2 from a PDF

This Claude Code session reads a full promotional video, opens a 35-page PDF, and saves a specific
figure for closer inspection.

▶ **[View the detailed trace](https://qianwen-res.oss-accelerate.aliyuncs.com/Qwen-MM-Plugins/asserts/core/case-core-cc-basic-use.html)**

<p align="center">
  <img src="assets/cc-basic-use.png" alt="Claude Code trace — video and PDF figure" width="520">
</p>

### Shared Case: local views, cloud grounding, and web verification

The following Codex trace locates cakes, annotates the image, identifies a photographed place, and
cross-checks the result on the web. It is shared with the
[API](../api/usage.md#shared-case-local-views-cloud-grounding-and-web-verification) and
[Search](../search/usage.md#shared-case-local-views-cloud-grounding-and-web-verification)
cookbooks because the workflow crosses all three capabilities:

| Current capability | Part of the workflow |
|---|---|
| `core` | Read/save the image view and render annotations |
| `api` | Ground objects and reason about the image |
| `search` | Verify candidates with web search and page extraction |

▶ **[View the shared detailed trace](https://qianwen-res.oss-accelerate.aliyuncs.com/Qwen-MM-Plugins/asserts/core/case-core-codex-api-use.html)**

> The trace was recorded before `api` and `search` were split out of `core`. Tool names carrying the
> old `qwen_mm_plugins_core` namespace map to the current capabilities shown above; the demonstrated
> workflow and outputs are retained because the session cannot currently be re-recorded.

<p align="center">
  <img src="assets/codex-api-use.png" alt="Shared Core, API, and Search workflow" width="520">
</p>

### Case 3 — ask a GUI harness to install Core and Edu Agent

The agent is asked to install `core` and `edu-agent` from this repository:

```text
hello 帮我装一下 https://github.com/QwenLM/Qwen-MM-Plugins 的 core 和 edu 插件
```

<p align="center">
  <img src="assets/qwenwork-install.png" alt="GUI harness installing Core and Edu Agent" width="520">
</p>

> This screenshot also predates the capability split. Current `core` advertises seven local MCP
> tools; cloud and search tools are installed separately.
