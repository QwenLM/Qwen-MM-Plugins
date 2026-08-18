"""Render URLs or local HTML/MHTML files via Playwright headless browser screenshot.

Long pages are split into viewport-sized pages; a short trailing strip is
merged into the last full page.
"""

from __future__ import annotations

import io
import os
from typing import Any

# Trailing strip shorter than this fraction of viewport merges into previous page.
_MERGE_THRESHOLD = 0.4
_ISOLATED_TIMEOUT = 150


def _render_in_process(path: str, opts: dict[str, Any]) -> list:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError('Missing dependency — install "qwen-mm-plugins[viz]", then: playwright install chromium')

    from PIL import Image

    from qwen_mm_plugins_core.renderers import DEFAULT_MAX_PAGES, render_image_block

    budget = opts.get("budget", "large")
    max_pages = opts.get("max_pages", DEFAULT_MAX_PAGES)
    viewport = {"small": (800, 600), "normal": (1280, 960), "large": (1920, 1080)}
    vw, vh = viewport.get(budget, (1280, 960))

    if path.startswith(("http://", "https://")):
        url = path
        label = path
    else:
        url = f"file://{os.path.abspath(path)}"
        label = os.path.basename(path)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, channel="chrome")
        except Exception:
            browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": vw, "height": vh})
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)

            # Scroll to trigger lazy-loaded content.
            page_height = page.evaluate("document.body.scrollHeight") or vh
            steps = min(int(page_height / vh) + 1, max_pages + 5)
            for i in range(1, steps):
                page.evaluate(f"window.scrollTo(0, {i * vh})")
                page.wait_for_timeout(800)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(1000)

            screenshot_bytes = page.screenshot(full_page=True, type="png")
        finally:
            browser.close()

    img = Image.open(io.BytesIO(screenshot_bytes))

    # Split into viewport-height pages; merge a short tail into the last.
    cuts = list(range(0, img.height, vh))
    pages = []
    for i, y in enumerate(cuts):
        bottom = min(y + vh, img.height)
        remaining = img.height - bottom
        # Extend to end if leftover is too short (avoid thin sliver).
        if 0 < remaining < vh * _MERGE_THRESHOLD:
            bottom = img.height
        pages.append(img.crop((0, y, img.width, bottom)))
        if bottom >= img.height:
            break

    pages = pages[:max_pages]

    # Page-range selection (same syntax as PDF: "1-3", "2", "1,3,5").
    pages_str = opts.get("pages")
    if pages_str:
        from qwen_mm_plugins_core.renderers import parse_pages

        selected = parse_pages(pages_str, len(pages))
        pages = [pages[i] for i in selected if i < len(pages)]

    content: list[dict[str, Any]] = [
        {"type": "text", "text": "[Web Start]"},
        {"type": "text", "text": f"Source: {label} | Total pages: {len(pages)}"},
    ]
    for i, page_img in enumerate(pages):
        block, w, h = render_image_block(page_img, budget)
        content.append({"type": "text", "text": f"[Page {i + 1} View] {h}x{w} (HxW)"})
        content.append(block)
    content.append({"type": "text", "text": "[Web End]"})

    return content


def render_isolated(arguments: dict[str, Any]) -> list:
    """Worker entry point for Playwright and its browser process tree."""
    path = arguments.get("path")
    opts = arguments.get("options", {})
    if not isinstance(path, str) or not path:
        raise ValueError("isolated web render requires a non-empty path or URL")
    if not isinstance(opts, dict):
        raise ValueError("isolated web render options must be an object")
    return _render_in_process(path, opts)


def render(path: str, **opts: Any) -> list:
    from shared.isolated_worker import run_isolated

    return run_isolated(
        __name__,
        "render_isolated",
        {"path": path, "options": opts},
        timeout=_ISOLATED_TIMEOUT,
    )
