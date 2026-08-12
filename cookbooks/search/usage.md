# Cookbook — Qwen-MM-Plugins Search

`qwen-mm-plugins-search` verifies facts that cannot be established from media alone. Web search and
page extraction support Serper, Exa, and Tavily; reverse-image search always uses Serper Lens.

Use [`core`](../core/usage.md) to save a clear image or video frame first. Use
[`api`](../api/usage.md) when the task needs model-based OCR, grounding, or visual reasoning before
search.

---

## Tools

- `web_search` — run one or more text queries and return titles, snippets, dates, and URLs
- `web_extractor` — extract the main text from one or more URLs, focused by a required `goal`
- `image_search` — reverse-search a public image URL or an explicitly approved local upload;
  optionally crop a normalized `0–1000` bbox before searching

`image_search` needs a public URL for Serper Lens. A local file is uploaded to the third-party public
host `uguu.se`, so the tool requires explicit user consent through `allow_public_upload=true`. Do not
upload private or sensitive images. A public URL with no crop can be searched without re-uploading.

For exact schemas, check the installed Skill or MCP tool list.

---

## Install

```bash
claude plugin marketplace add https://github.com/QwenLM/Qwen-MM-Plugins.git
claude plugin install qwen-mm-plugins-core@qwen-mm-plugins    # save local views/frames
claude plugin install qwen-mm-plugins-search@qwen-mm-plugins
```

Set one or more of `SERPER_API_KEY`, `TAVILY_API_KEY`, and `EXA_API_KEY`. With
`QWEN_MM_SEARCH_BACKEND` unset or set to `auto`, text tools choose the first configured provider in
that order. Set the selector to `serper`, `tavily`, or `exa` to pin a provider; an explicit choice
does not fall back when its key is missing. `image_search` ignores the selector and always requires
`SERPER_API_KEY`. Use the installer's **Configure** action, an environment variable, or
`~/.qwen-mm-plugins/config`; environment variables take precedence. `core` itself needs no key.

---

## Verification workflow

1. Obtain a clear source image or save a representative video frame with core's `save_view`.
2. For a local image, ask the user before uploading it publicly; then call `image_search` with
   `allow_public_upload=true`. Use `bbox` when the subject occupies only part of the frame.
3. Treat visual matches as candidates, not proof. Cross-check names and facts with `web_search`.
4. Open the best sources with `web_extractor` and answer from the corroborated evidence.

For text-only questions, begin at step 3. For a specific place, person, object, species, or event,
avoid committing to an identification from appearance or one search result alone.

---

## Example requests

```text
@building.jpg
Reverse-search this building, then verify its name and architect using reliable web sources.

@product.jpg
Crop to the logo, find likely matches, and check the manufacturer's official page.

Search for the current documentation of this API, then extract the authentication requirements
from the primary source.
```

---

## Shared Case: local views, cloud grounding, and web verification

This Codex session locates cakes, identifies a photographed place, and verifies candidates with web
search and page extraction. Frame/image handling belongs to
[`core`](../core/usage.md#shared-case-local-views-cloud-grounding-and-web-verification), while
grounding and vision reasoning belong to
[`api`](../api/usage.md#shared-case-local-views-cloud-grounding-and-web-verification).

▶ **[View the shared detailed trace](https://qianwen-res.oss-accelerate.aliyuncs.com/Qwen-MM-Plugins/asserts/core/case-core-codex-api-use.html)**

> The trace predates the capability split, so `web_search` and `web_extractor` appear under the old
> `qwen_mm_plugins_core` namespace. They are now provided by `qwen-mm-plugins-search`; the recorded
> search and verification workflow is otherwise unchanged.

<p align="center">
  <img src="../core/assets/codex-api-use.png" alt="Shared Core, API, and Search workflow" width="520">
</p>
