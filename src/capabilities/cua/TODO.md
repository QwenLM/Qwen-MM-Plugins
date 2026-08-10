# CUA capability — remaining half

This is a WIP scaffold. Done so far: `screenshot` tool + thin skill + server package that
runs from source. Remaining:

## Action space (tools/)
- [ ] `click` — click at (x, y) in full-res screen pixels (left/right/double). Needs `pyautogui`
      (or platform-native). macOS also needs **Accessibility** permission (separate from Screen
      Recording) — add to SYSTEM_DEPS + skill guidance.
- [ ] `type_text` — type a string at the current focus.
- [ ] `key_press` — send a key / chord (e.g. cmd+c, enter, esc).
- [ ] `scroll` — scroll by dx/dy at a point.
- [ ] `move` / `drag` — optional.
- [ ] Decide coordinate contract: accept full-res pixels vs normalized 0–1000 (align with core
      `grounding` output so screenshot → grounding → click composes cleanly).

## Packaging (the repo's wiring)
- [ ] `pyproject.toml`: add `[project.scripts]` entry `qwen-mm-plugins-cua`; add the folder to
      `[tool.setuptools]` package-dir + `packages.find` where; add a `cua` extras group
      (`mss`, `pillow`, `pyautogui`) and fold into the `all` profile.
- [ ] `.claude-plugin/marketplace.json`: add a `cua` plugin entry.
- [ ] `src/capabilities/cua/.claude-plugin/plugin.json` (skill + inline mcpServers, key
      `qwen-mm-plugins-cua`).
- [ ] `.codex-plugin/plugin.json` + `.mcp.json` if codex install is wanted.
- [ ] docs/en/installation.md note; env-var table if any new config.

## Cross-platform / robustness
- [ ] Retina / DPI: capture is physical px (mss ok); when click lands, verify no logical↔physical
      mismatch on macOS.
- [ ] Wayland: raw grab blocked — detect and message (partly done in `_capture_hint`).
- [ ] Permission preflight: a cheap check that Screen Recording is granted, surfaced at startup.

## Open decision
- Native lightweight tools (this path) vs. depend on / point at **trycua/cua** (Cua Driver MCP +
  `@cua/driver` skill) for a batteries-included cross-OS driver and VM sandbox. Native = pure-Python,
  reuses core `grounding`; trycua = far more complete but Swift/Rust stack + heavier install.
