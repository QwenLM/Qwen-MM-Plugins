# Plugin versions and releases

Qwen-MM-Plugins has one Python distribution but independent plugin releases. A plugin version covers
its skill, manifests, MCP configuration, server code, and the shared code visible at its Git tag.

## Version model

- [`plugin-versions.json`](../../plugin-versions.json) is the distribution and latest-plugin index.
- Published tags use `qwen-mm-plugins-<cap>-v<semver>`.
- Marketplace entries and `uvx --from` pin the same tag; `main` is development-only.
- The wheel contains all server packages, but each plugin starts its own tagged `uvx` environment,
  so releasing `search` does not upgrade an installed `core`.
- `mcp_framework.__version__` is the one-distribution/release-train version. Each server package's
  `__version__` is its plugin version; they are expected to diverge after independent releases.

Use SemVer per capability: patch for compatible fixes, minor for new tools/backends or additive
behavior, and major for breaking tool schemas, removed tools, or incompatible configuration.

## Prepare a release

Prepare every capability affected by the code or skill change:

```bash
git fetch origin --tags --prune
python scripts/prepare_plugin_release.py search 1.1.0 --distribution-version 1.0.2
python scripts/check_manifests.py
python -m pytest tests/
```

The script updates the index, manifests, MCP refs, server version, marketplace, and installer. It
does not commit, tag, or push. Because every tag builds the same distribution, its version advances
separately; capabilities sharing one release commit use the same `--distribution-version`.

Commit code and release metadata together. After CI passes, tag that exact commit:

```bash
git tag -a qwen-mm-plugins-search-v1.1.0 -m "qwen-mm-plugins-search 1.1.0"
git push origin <release-branch>
git push origin qwen-mm-plugins-search-v1.1.0
```

Never move a published tag; publish a patch version instead.

## Weekly release train

Batch ready changes roughly weekly; skip empty weeks and release critical fixes immediately.
Multiple capability tags may share a commit. Shared runtime changes require bumping every affected
capability.

The `example` capability is a development template and is intentionally absent from the stable
index and marketplace.
