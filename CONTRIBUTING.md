# Contributing

Thanks for contributing to Qwen-MM-Plugins. Keep changes focused on a concrete
problem. For a new capability or an MCP interface change, open an issue first so
the scope and compatibility impact can be discussed.

## Development setup

Qwen-MM-Plugins supports Python 3.10 and newer. From a checkout, install the
dependencies needed for the area you are changing:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[core]'
```

See [local development](docs/en/local_development.md) for source and harness
debugging, and [Add a new plugin](docs/en/how_to_add_new_capability.md) for
implementation, installer registration, and the Hub contribution steps.

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
- Keep tool and argument prose in handler docstrings and Skill instructions under the capability.
  Maintain general English guides in `docs/en/`; the Hub imports them instead of keeping copies.
- Contribute cookbooks and reviewed public case files to
  [JJJYmmm/qwen-mm-plugins-hub](https://github.com/JJJYmmm/qwen-mm-plugins-hub), not this repository.
  Follow [Hub authoring](docs/en/hub.md) for metadata, media paths, and publication order.

## Verification

Run relevant targeted tests while developing. Before opening a PR, run the offline checks:

```bash
python3 -m pytest -m "not reachability" tests/
python3 scripts/check_manifests.py
ruff format --check .
ruff check .
```

See [Testing](docs/en/testing.md) for live-provider and component-specific checks.

If a test needs credentials, a GUI application, GPU hardware, or another
environment not available to you, state what was not run in the PR.

## Pull requests

Describe the problem and the reason for the chosen fix, link related issues,
and include the commands and results used for verification. Keep unrelated
changes in separate PRs.

Report security issues according to [SECURITY.md](SECURITY.md), not through a
public issue. Contributions are licensed under the repository's Apache-2.0
license.

Maintainers: follow [Plugin releases](docs/en/releasing.md) after a release PR merges.
