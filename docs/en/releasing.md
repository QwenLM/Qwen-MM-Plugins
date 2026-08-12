# Plugin releases

**English** · [中文](../zh/releasing.md)

Qwen-MM-Plugins publishes one Python distribution but versions each capability independently. A
capability release covers its Skill, manifests, MCP configuration, server code, and the shared code
visible at that tag.

## Version model

| Version | Scope | Source of truth |
|---|---|---|
| Plugin version | One capability | [`plugin-versions.json`](../../plugin-versions.json) → `plugins.<cap>` |
| Distribution version | Repository snapshot and shared Python distribution | `distribution_version` in the same file |
| Marketplace metadata version | Catalog snapshot; not a claim that every plugin changed | Distribution version |
| Plugin tag | Immutable source snapshot for one capability | `qwen-mm-plugins-<cap>-v<semver>` |

Marketplace entries and MCP `uvx --from` specs pin the same plugin tag. `main` is development-only.
Although a tag contains the whole distribution, each plugin launches its own tagged environment;
releasing `search` does not update an installed `core`.

Use SemVer per capability: patch for compatible fixes, minor for additive tools or behavior, and
major for breaking schemas, removed tools, or incompatible configuration. Shared runtime changes
require releases for every affected capability.

## Release checklist

1. Prepare every affected capability on the PR branch:

   ```bash
   git fetch origin --tags --prune
   python3 scripts/prepare_plugin_release.py search 1.1.0 --distribution-version 1.0.2
   python3 scripts/check_manifests.py
   python3 -m pytest -m "not reachability" tests/
   ```

   The script updates release metadata and launch refs; it does not commit, tag, or push. When
   several capabilities share one release commit, prepare them with the same distribution version.

2. Commit the code and generated release metadata together, open the PR, and wait for it to merge.

3. Tag the exact commit now present on `origin/main`:

   ```bash
   git fetch origin main --tags
   git tag --list qwen-mm-plugins-search-v1.1.0   # must print nothing
   git show origin/main:plugin-versions.json     # confirm the intended release is present
   git tag -a qwen-mm-plugins-search-v1.1.0 origin/main \
     -m "qwen-mm-plugins-search 1.1.0"
   git push origin qwen-mm-plugins-search-v1.1.0
   ```

   Tagging after merge keeps releases on the main history even when GitHub uses squash or rebase
   merges. Never move a published tag; issue a patch release instead.

4. Smoke-test the published tag using the [installation guide](installation.md).

## Release cadence

Batch ready changes roughly weekly, skip empty weeks, and release critical fixes when needed.
Multiple capability tags may point to the same merged commit. The `example` capability is a
development template and is not published.
