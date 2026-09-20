# Hub authoring

**English** · [中文](../zh/hub.md)

The Hub currently lives in **[QwenLM/qwen-mm-plugins-hub](https://github.com/QwenLM/qwen-mm-plugins-hub)**
and is published at [Qwen MM Plugins Hub](https://qwenlm.github.io/qwen-mm-plugins-hub/).
Start with [Add a new plugin](how_to_add_new_capability.md) to implement and register a capability.

Keep each kind of content in its owning repository:

| Content | Where to maintain it |
|---|---|
| Plugin summary | Capability manifests, marketplace description, and installer `CAP_DESC` in Qwen-MM-Plugins; the Hub reads the Codex manifest |
| Skill instructions and supporting files | `src/capabilities/<cap>/skill/` in Qwen-MM-Plugins |
| Tool and argument descriptions | Handler docstrings in Qwen-MM-Plugins; types and validation stay in Pydantic |
| General English guides | `docs/en/` in Qwen-MM-Plugins; the Hub imports them on each build |
| Cookbook, category, tags, title, contributors | `content/cookbooks/<cap>/usage.md` in the Hub |
| Demo videos, images, interactive cases | `public/cases/<cap>/<case>/` in the Hub |

The Hub discovers plugins from `plugin-versions.json`, reads their actual MCP registries and
Skills, and computes token estimates. Generated `data/*.json` is ignored by Git and exists only
locally or in build artifacts; the Hub does not commit a second copy of source or guides. Do not
hand-edit it, maintain another plugin list, or duplicate cookbooks in this repository.

## Author descriptions once

Every tool uses the same format: `TOOL` contains only `name` and `args`; `handle` has a Google-style docstring.

```python
from pydantic import BaseModel, Field


class EchoArgs(BaseModel):
    message: str
    repeat: int = Field(default=1, ge=1, le=10)


TOOL = {"name": "echo", "args": EchoArgs}


def handle(arguments: dict) -> list[dict]:
    """Echo text back to the caller.

    Args:
        message: Text to repeat.
        repeat: Number of repetitions, from 1 to 10.
    """
    return [{"type": "text", "text": arguments["message"] * arguments.get("repeat", 1)}]
```

Write the tool's purpose in the introductory prose. Document every argument exactly once under `Args:`, including inherited fields. Put nested object details in their argument description. Optional `Examples:` content is included in the public tool description. Zero-argument tools do not need `Args:`.

Types, defaults, aliases, constraints and validators belong in Pydantic; prose belongs in docstrings. There are no explicit-description overrides or format switches. Registration rejects missing/duplicate argument documentation. The same enriched schema reaches both FastMCP and the Hub exporter without mutating the original model.

For a Skill, use its frontmatter `description` to say when to use it and what it does. Give it a
task-specific H1 instead of repeating the repository name. Keep supporting scripts, references,
and assets under `skill/`; the Hub links their tracked file hierarchy and shows the first 50
lines of `SKILL.md`, expandable to the full text. Do not shorten the source just for the preview.

## Cookbook and cases

Create `content/cookbooks/<cap>/usage.md` in the Hub. This file is required for every registered
plugin; its YAML metadata is optional. For example, a `my-plugin` cookbook can start with:

```markdown
---
title: My Plugin
category: Understanding
tags: [image, video]
contributors: [QwenLM]
order: 10
---

# My Plugin

## Workflow

Describe the input, setup, steps, and expected result.

## Cases

[Demo](../../../public/cases/my-plugin/demo/assert/demo.mp4)

[Interactive case](../../../public/cases/my-plugin/demo/index.html)
```

Contributors are GitHub account names, not URLs; their profiles and avatars are derived
automatically. They default to `QwenLM`. Prefer one or two useful task/modality tags. Without
metadata, the title comes from the capability ID, category is `Other`, and order is `99`.

Keep each case self-contained:

```text
public/cases/my-plugin/demo/
├── index.html          # optional interactive case
└── assert/
    ├── demo.mp4
    ├── screenshot.png
    └── ...             # other files used by this case
```

Use the existing directory name **`assert`**, not `assets`. Put a video or HTML link in its own
paragraph, as above: the Hub replaces it with a player or sandboxed iframe. Inline links remain
links; images use normal Markdown image syntax. Do not add a duplicate thumbnail, “view
recording,” or download prompt beside the embed. Use relative `assert/...` URLs inside the HTML
case. Cookbook paths are rewritten for the website, so no separate public media host is needed.

For video, use MP4 with H.264/YUV420P, AAC audio if present, and faststart. Keep every case file
below 25 MiB, the Hub's current build limit. Commit actual files, not symlinks or Git LFS pointer
files. Review recordings for credentials, personal data, and sharing rights before committing.

## Validate locally

Use Node 24, Git, and [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
git clone https://github.com/QwenLM/qwen-mm-plugins-hub.git
cd qwen-mm-plugins-hub
npm ci
SITE_BASE_PATH=/qwen-mm-plugins-hub npm run build
npm test
npm run dev
```

Both `dev` and `build` regenerate content first. The first run clones the configured source into
ignored `.sources/upstream`; `uv` provides Python 3.12 and exporter dependencies. Later runs reuse
that checkout. Run `npm run content:sync` to fetch the latest configured branch and tags, or
`npm run content` to regenerate without fetching. `npm test` uses the generated files and remains
offline, so generate content before testing a fresh clone. Omit `SITE_BASE_PATH` for root-domain
builds, including the isolated PR build check.

To preview your plugin changes, commit them first and keep the checkout clean. Set your checkout
path and branch explicitly; the Hub never modifies a checkout supplied through `HUB_SOURCE_DIR`:

```bash
HUB_SOURCE_DIR=../Qwen-MM-Plugins HUB_SOURCE_REF=my-branch npm run dev
```

The checkout's HEAD must match that branch. CI instead sets `HUB_SOURCE_COMMIT` to the exact
checked-out SHA, allowing detached PR heads. Source links always pin that commit.

If you prefer an existing Python environment over `uv`, install the dependencies and point
`HUB_PYTHON` at it. Use a clean checkout of the selected source branch:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '../Qwen-MM-Plugins[omni-memory]' -r scripts/requirements-export.txt
HUB_PYTHON="$PWD/.venv/bin/python" HUB_SOURCE_DIR=../Qwen-MM-Plugins npm run build
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
npm test
```

## Publish and refresh

1. Merge the plugin-side changes into the remote branch selected in the Hub's
   [`source.config.json`](https://github.com/QwenLM/qwen-mm-plugins-hub/blob/main/source.config.json),
   currently `main`. For a new plugin, prepare its Hub cookbook alongside that change so the
   next build has both halves. A local commit or an unmerged PR does not update the public Hub.
2. Push or merge the cookbook and case files into Hub `main`. That push runs
   [Build and deploy plugin directory](https://github.com/QwenLM/qwen-mm-plugins-hub/actions/workflows/pages.yml).
   For upstream-only changes, the Hub checks the selected branch and capability tags every
   30 minutes, without a cross-repository secret. It builds only when those inputs or the Hub
   commit differ from the last successful deployment. **Run workflow** on Hub `main` forces a
   rebuild, but still waits if the catalog references release tags that are not published yet.
3. Wait for the build and deployment to pass, then check the plugin, cookbook, and Docs pages on
   [the public Hub](https://qwenlm.github.io/qwen-mm-plugins-hub/). Builds regenerate the catalog,
   cookbooks, English docs, and token estimates together. Failed builds leave the published site
   unchanged; the next scheduled check retries changed inputs. Fix build errors before retrying.

The schedule is a fallback, not a precise delivery deadline: GitHub can delay scheduled runs and
[disables them after 60 days of repository inactivity](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/disabling-and-enabling-a-workflow?tool=cli)
in public repositories. Re-enable the workflow when necessary.

For immediate refreshes, optionally add a repository Actions secret named `HUB_DISPATCH_TOKEN`
to **Qwen-MM-Plugins**. Use a fine-grained token limited to **QwenLM/qwen-mm-plugins-hub** with
**Actions: write** permission, subject to organization approval. The plugin-side
`hub-refresh.yml` dispatches Hub `pages.yml` after `main` and capability-tag pushes. Without the
secret it skips dispatch successfully and the scheduled fallback still works. Never put the
token in either repository. The ordinary `GITHUB_TOKEN` is scoped to its own repository and
cannot supply this cross-repository access.

Keep English guides in this repository, not a second Hub docs folder. Each `docs/en/**/*.md`
needs an H1 title and a unique route: underscores become hyphens, and nested path segments are
joined with hyphens. Relative links between imported English guides stay inside the Hub.

## PR build checks

Once the workflows and their helpers are merged into both repositories' default branches,
plugin PRs run **Hub documentation check** against the exact PR head and Hub `main`. The check
builds and tests the site with a read-only token and no secrets; it does not call model services
or deploy a website. New plugins need their cookbook available in Hub `main` for this check.

Read the result and logs directly in the PR's **Checks** tab. There is no comment bot, preview
package, or preview hosting service. The workflow summary links to
[the published Hub](https://qwenlm.github.io/qwen-mm-plugins-hub/), not a preview of the PR.
PR changes appear there only after merge and a successful automatic deployment. Fork PRs use
the same read-only check; GitHub may require a maintainer to approve their build before it runs.

## Branch and release

The page displays the configured source branch and pins source links to its commit. Publishing
the Hub does not merge plugin branches or publish release tags. The default installer uses
published releases, which may differ from the documented development snapshot. Test branch code
through the [local-development workflow](local_development.md), not an unpublished release tag.

Keep the Hub's `source.config.json` pointed at plugin `main`. When preparing a release, publish
the referenced capability tags through the independent [release process](releasing.md) before
refreshing the Hub's release links. Cookbook and case-only edits need only a Hub deployment.
