# Contributing

Thanks for contributing to Qwen-MM-Plugins. Keep changes focused on a concrete
problem. For a new capability or an MCP interface change, open an issue first so
the scope and compatibility impact can be discussed.

## Development setup

Qwen-MM-Plugins supports Python 3.10 and newer. From a checkout, install the
dependencies needed for the area you are changing:

```bash
scripts/dev-install.sh          # base dependencies
scripts/dev-install.sh core     # core and visualization
scripts/dev-install.sh all      # full runtime profile
```

See [local development](docs/en/local_development.md) for source and harness
debugging, and [adding a capability](docs/en/how_to_add_new_capability.md) for
the repository layout and registration steps. Maintainers batch normal version
bumps and immutable capability tags in the weekly release train; see
[plugin releases](docs/en/releasing.md).

## Making changes

- Keep capability-specific code under `src/capabilities/<name>/`; put code in
  `src/shared/` only when multiple capabilities use it.
- Preserve existing MCP tool names, inputs, and outputs unless the change is
  required to fix functionality. Explain any interface change in the PR.
- Import optional dependencies lazily and declare non-Python tools in the
  capability's `SYSTEM_DEPS` table.
- Do not commit API keys, credentials, private media, generated artifacts, or
  machine-specific configuration.
- Add or update tests and documentation when behavior changes.

## Verification

Run the relevant targeted tests while developing, then run:

```bash
python3 -m pytest tests/
ruff format .
ruff check .
```

If a test needs credentials, a GUI application, GPU hardware, or another
environment not available to you, state what was not run in the PR.

## Pull requests

Describe the problem and the reason for the chosen fix, link related issues,
and include the commands and results used for verification. Keep unrelated
changes in separate PRs.

Report security issues according to [SECURITY.md](SECURITY.md), not through a
public issue. Contributions are licensed under the repository's Apache-2.0
license.
