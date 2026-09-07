# CUA Basic browser benchmark

CUA Basic is a local, deterministic smoke benchmark for comparing the CUA plugin's nested tool
profiles and screenshot coordinate modes. Every task starts in Chromium, uses the same local web
surface, and ends in a server-side verifier rather than a visual self-assessment.

This fixture is intended for internal iteration. It does not replace OSWorld, BrowserGym, or a
platform-wide evaluation.

## What it measures

The ten cases are split by the interface they stress:

| Cases | Category | Main signal |
|---|---|---|
| 01, 06, 07 | Visual | Screenshot grounding, scrolling, coordinate conversion, and dragging. |
| 02–05 | AX-friendly | Semantic hierarchy, text input, selects, checkboxes, and exact state changes. |
| 08 | Recovery | Waiting for delayed state and avoiding an unsafe duplicate action. |
| 09–10 | Browser | Tab identity, page state, upload, delayed processing, and download. |

The same prompt should be used for every configuration. Do not tell the evaluated agent which
profile or coordinate mode is active.

## Start the fixture

From the repository root:

```bash
python3 benchmarks/cua_basic/server.py --port 8765
```

Open <http://127.0.0.1:8765/> to inspect the case catalog or start a run manually. The server has no
third-party dependencies and stores run state in memory. Restarting it clears all runs.

In another terminal, list or create cases with:

```bash
python3 benchmarks/cua_basic/benchctl.py list
python3 benchmarks/cua_basic/benchctl.py create 01 --seed 42
```

The create command prints a unique URL, the task, and a ready-to-send agent prompt. Before sending
that prompt, the runner opens the URL in the chosen browser outside the evaluated action loop. A new
run id is also the reset mechanism; never reuse a completed run across configurations.

After the agent stops, read the verifier:

```bash
python3 benchmarks/cua_basic/benchctl.py result <run-id> --json
```

Case 10 uses this repository fixture:

```text
benchmarks/cua_basic/fixtures/benchmark-upload.txt
```

If the browser tool reports a downloaded path, validate its bytes with:

```bash
python3 benchmarks/cua_basic/benchctl.py verify-download <run-id> /absolute/path/to/downloaded-file
```

## Suggested ablation matrix

Use the labels below in results while retaining the plugin's actual config values:

| Report label | Plugin config |
|---|---|
| `visual` | `QWEN_MM_CUA_TYPE=native` |
| `ax` | `QWEN_MM_CUA_TYPE=ax` |
| `full` | `QWEN_MM_CUA_TYPE=full` |

Combine them with both coordinate modes:

```text
visual-absolute   visual-relative
ax-absolute       ax-relative
full-absolute     full-relative
```

Set one configuration in `~/.qwen-mm-plugins/config`, then start a fresh Claude Code or Codex
session so the MCP schemas reload:

```dotenv
QWEN_MM_CUA_TYPE=native
QWEN_MM_CUA_COORDINATE_MODE=relative
```

For a quick internal run, use all ten cases with three seeds:

```text
10 cases × 6 configurations × 3 seeds = 180 runs
```

When cost matters, start with cases `01, 02, 04, 06, 07, 08, 09, 10` and seed `0`.

## Fair-run protocol

For every run:

1. Start from a new run id and its exact URL.
2. Outside the evaluated action loop, open that URL in a clean browser window and wait for the task
   page to finish loading. Begin timing only after this setup step.
3. Keep the model, reasoning effort, prompt, viewport, browser version, action budget, and timeout
   fixed.
4. After the task begins, allow the evaluated agent to use only the CUA MCP tools. Do not let it
   inspect this directory, call the fixture API, use browser developer tools, or invoke shell/browser
   automation as a second control path.
5. Stop when the page reports `Case passed`, the action budget is exhausted, or the timeout expires.
6. Query `/api/runs/<run-id>/result` outside the evaluated agent and save the CUA/Codex/Claude trace.

The suggested limits live in `cases.json`. The server's `event_count` is a fixture transition count,
not an agent action count; compute action count, latency, tool errors, action routes, stale snapshot
errors, and pointer fallbacks from the MCP trace.

## Interpreting the ablations

Use all ten cases for end-to-end task success. Also report focused subsets:

- Coordinate comparison: cases 01, 06, and 07, plus only trace steps that used screenshot
  coordinates. Full may use page refs and legitimately avoid coordinates.
- AX gain: compare `ax` with `visual` on cases 02–05 under the same coordinate mode.
- Browser gain: compare `full` with `ax` on cases 09–10.
- Recovery behavior: report case 08 separately; its verifier requires exactly one Start event.

Recommended primary and diagnostic fields:

```json
{
  "task_success": true,
  "actions": 7,
  "elapsed_seconds": 18.4,
  "visual_coordinate_actions": 3,
  "ax_token_actions": 0,
  "browser_ref_actions": 0,
  "tool_errors": 0,
  "stale_snapshot_errors": 0,
  "unverified_actions": 0,
  "global_pointer_fallbacks": 0,
  "unintended_state_changes": 0
}
```

Do not infer success from the final screenshot. The JSON verifier is authoritative.

## HTTP API

The CLI is a thin wrapper around these endpoints:

```text
GET  /api/cases
POST /api/runs                         {"case_id":"01","seed":42}
GET  /api/runs/<run-id>/view           page fixture data
POST /api/runs/<run-id>/events         used by the fixture page
GET  /api/runs/<run-id>/result         authoritative result
GET  /api/runs/<run-id>/download       case-10 artifact
```

`view` contains data required to render the page and must not be used as an agent-side shortcut.
