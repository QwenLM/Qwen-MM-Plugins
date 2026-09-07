# Running CUA Basic with Claude Code

Use this directory as an evaluation harness, not as a coding task.

## Setup phase

You may use the shell to start `server.py` and `benchctl.py create`. Capture the printed `RUN_ID`,
`URL`, and task prompt. Open the URL in the chosen browser before the timed evaluation begins, then
confirm that the requested CUA MCP profile is loaded.

## Evaluation phase

After opening the run URL:

- Use only the `qwen-mm-plugins-cua` MCP tools to observe and operate the browser.
- Do not read the benchmark source, `cases.json`, server state, or verifier during the task.
- Do not use shell commands, curl, Playwright, browser developer tools, AppleScript, or another MCP
  server as a second interaction path.
- Do not change the task wording or use knowledge from a previous seed.
- Stop when the page reports `Case passed`, the case action limit is reached, or the caller's timeout
  expires.
- Treat an unverified action as uncertain. Observe or wait before deciding whether a retry is safe.

## Scoring phase

After the evaluation phase ends, shell access may be used to call `benchctl.py result` and archive
the trace. Record the actual CUA action route (`coordinate`, `element_token`, or Browser ref) rather
than inferring it from the selected profile.

Use a fresh Claude Code process after changing `QWEN_MM_CUA_TYPE` or
`QWEN_MM_CUA_COORDINATE_MODE`; MCP tool schemas are fixed when the server process starts.
