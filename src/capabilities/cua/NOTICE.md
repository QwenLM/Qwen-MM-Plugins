# Third-party attribution — CUA capability

This capability is a passthrough to **trycua/cua**'s Cua Driver, and **vendors its Agent Skill
documentation** into `skill/`.

- Upstream: cua (trycua) — https://github.com/trycua/cua
- Copyright: (c) 2025 Cua AI, Inc.
- License: MIT

Vendored / derived files (from the `cua-driver` skill pack, release `cua-driver-rs-v0.19.3`):

- `skill/SKILL.md` — the upstream cross-platform core skill, **modified**: frontmatter `name`
  changed to `qwen-mm-plugins-cua` (to match the plugin) and a "Qwen-MM-Plugins integration"
  section added at the top. The rest is upstream text.
- `skill/MACOS.md`, `skill/WINDOWS.md`, `skill/LINUX.md`, `skill/BROWSER.md`,
  `skill/RECORDING.md`, `skill/EMBEDDING.md` — verbatim companion files from the same pack.

No source code from cua is vendored — the MCP server is the external `cua-driver` binary,
installed separately (see `README.md` / `docs/en/installation.md`).

The MIT license text of the upstream project applies to the vendored portions above:

```
MIT License

Copyright (c) 2025 Cua AI, Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
