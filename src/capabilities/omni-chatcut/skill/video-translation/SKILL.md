---
name: qwen-mm-plugins-omni-chatcut-video-translation
description: "Translate an existing video's speech and optionally produce a speaker-preserving dubbed video: analyze speech and visible subtitles, author a timed translation plan, synthesize reference-guided voices, replace the vocal track, resume an existing project, or validate a final delivery."
---

# Video Translation

Own one durable project and route it by state:

```text
source video → Agent-reconciled Omni/VAD transcript → Agent-authored dubbing plan → dubbed render → Agent listening review
```

Inspect first:

```bash
python3 <skill-root>/scripts/inspect_video_translation_state.py <project-dir>
```

Use the first applicable route: review or validate an existing delivery; resume a plan/render; translate an
accepted transcript; or analyze a new source. Read [pipeline-contract.md](references/pipeline-contract.md) before
accepting artifacts and [dubbing-service.md](references/dubbing-service.md) when configuring the external
GPU service. Never infer success from filenames alone.

## Internal workflows

- Source speech, subtitles, speakers, and timing: [source-analysis/WORKFLOW.md](workflows/source-analysis/WORKFLOW.md)
- Translation and duration-aware adaptation: [translation-authoring/WORKFLOW.md](workflows/translation-authoring/WORKFLOW.md)
- Voice references, TTS, mix, remux, and QA: [dubbing-rendering/WORKFLOW.md](workflows/dubbing-rendering/WORKFLOW.md)

Load only the current workflow. Reuse the bundled `omni_call` for audio-video understanding. Use the
video-translation MCP tools for the external dubbing service and deterministic local media execution.

## Authorization and configuration

Analysis and translation-only targets stop before TTS. A full or resumed dubbing request authorizes calls
to the configured external dubbing service. The service URL comes from `QWEN_MM_DUBBING_SERVER_URL`. Never
write it to artifacts.

Do not use server-local paths as portable outputs. Tools upload local inputs and save returned audio into
the project. Preserve the source video stream during final remux. A delivery is complete only after the
final file fully decodes and the measured QA contract passes.

## User-facing completion

After a successful render, surface the returned summary directly to the user. Report the segment count,
the number of automatic risk flags, and each segment that needs manual listening review with its time range,
translated text, and reason. Link `full/translation_summary.md` for a readable report and
`full/translation_diagnostics.json` for complete machine-readable details. Even when no automatic flags are
found, instruct the user to perform one end-to-end check for ordering, overlaps, pronunciation, and mix balance.
