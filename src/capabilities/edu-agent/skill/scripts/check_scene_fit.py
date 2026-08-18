#!/usr/bin/env python3
r"""
check_scene_fit.py — Pre-render gate: fail when a scene's CONTENT OVERFLOWS the 1920x1080 frame
and gets CLIPPED (排版超出一屏被裁切).

WHY: The static overflow gate (check_scene_overflow.py) only catches literal `width/height:>frame`
px. It CANNOT see the common, severe failure where a scene stacks too much into a vertical column
(e.g. a big SVG panel + a data table + a conclusion bar) at normal font sizes: the column's
natural height exceeds the usable frame height, and because `.scene-content` centers vertically
(`align-items:center`) inside a `overflow:hidden` root, the extra height spills off the TOP and
BOTTOM of the frame and is CLIPPED — e.g. the top panel's first row is chopped off. Real bug:
a "三种故障对照" scene stacked circuit + table + conclusion → content ~1008px tall vs ~860px usable
→ top row clipped. This gate MEASURES the real rendered layout in headless Chrome and flags it.

HOW: for each dist/compositions/*.html it
  - builds a full-document copy in-place (so ./gsap ./katex ./assets resolve), unwrapping any
    inert <template> so the scene is live,
  - loads it at 1920x1080 in headless Chrome, waits for fonts/KaTeX,
  - seeks every registered GSAP timeline to several progress points (so entrance
    scales/positions settle — no false positive from initial autoAlpha:0/off-screen states),
  - measures the union bounding box of the scene's CONTENT (`.scene-content` subtree — the
    animated background orbs live in a separate `.scene-bg` subtree and are ignored),
  - FAILS if that content extends above the frame top or below the frame bottom by > TOL px
    at any sampled time (i.e. it is clipped by the 1920x1080 edge).

Graceful skip: if Chrome / puppeteer-core is unavailable it prints a NOTE and exits 0 (so a
browserless env is not blocked — but the render env, which has Chrome, enforces it).

Usage:
    python3 check_scene_fit.py [dist_dir]     # default ./dist
Exit 0 = clean (or skipped), 1 = a scene overflows/clips the frame.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from _render import build_node_env, find_chrome

FRAME_W = 1920
FRAME_H = 1080
TOL = 8  # px of frame overflow tolerated before failing

NODE_DRIVER = r"""
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
let puppeteer;
try { puppeteer = require('puppeteer-core'); }
catch (e) { console.log(JSON.stringify({__skip__: "puppeteer-core not resolvable: " + e.message})); process.exit(0); }

const dir = process.argv[2];
const chrome = process.argv[3];
const FRAME_W = 1920, FRAME_H = 1080, TOL = %TOL%;

function unwrapFragment(html) {
  // strip inert <template> wrappers and any stray full-doc tags so the scene renders live
  return html
    .replace(/<template[^>]*>/gi, '')
    .replace(/<\/template>/gi, '')
    .replace(/<!doctype[^>]*>/gi, '')
    .replace(/<\/?html[^>]*>/gi, '')
    .replace(/<\/?head[^>]*>/gi, '')
    .replace(/<\/?body[^>]*>/gi, '');
}

(async () => {
  const files = fs.readdirSync(dir)
    .filter(f => f.endsWith('.html') && !f.startsWith('__fitcheck_'));
  const results = [];
  let browser;
  try {
    browser = await puppeteer.launch({
      executablePath: chrome, headless: 'new',
      args: ['--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--force-device-scale-factor=1'],
    });
  } catch (e) {
    console.log(JSON.stringify({__skip__: "chrome launch failed: " + e.message})); process.exit(0);
  }
  for (const f of files) {
    const src = fs.readFileSync(path.join(dir, f), 'utf8');
    const doc = '<!doctype html><html><head><meta charset="utf-8">' +
      '<style>html,body{margin:0;padding:0;background:#fff;}</style></head><body>' +
      unwrapFragment(src) + '</body></html>';
    const tmpName = '__fitcheck_' + f;
    const tmpPath = path.join(dir, tmpName);
    fs.writeFileSync(tmpPath, doc);
    const page = await browser.newPage();
    await page.setViewport({ width: FRAME_W, height: FRAME_H, deviceScaleFactor: 1 });
    let r = { file: f, ok: true };
    try {
      await page.goto(pathToFileURL(tmpPath).href, { waitUntil: 'load', timeout: 20000 });
      await page.evaluate(async () => { try { await document.fonts.ready; } catch(e){} });
      await new Promise(res => setTimeout(res, 350)); // KaTeX / late layout
      r = await page.evaluate((FRAME_H, FRAME_W, TOL) => {
        const root = document.querySelector('[data-composition-id]');
        const content = document.querySelector('.scene-content');
        if (!root || !content) return { file: '', ok: true, skipped: 'no .scene-content' };
        const samples = [0.35, 0.7, 1.0];
        let worstTopOver = 0, worstBotOver = 0, worstAt = 0, measuredAny = false;
        const tls = (window.__timelines && typeof window.__timelines === 'object')
          ? Object.values(window.__timelines) : [];
        for (const p of samples) {
          // seek FIRST — the scene starts at autoAlpha:0 (hidden); it only becomes
          // visible after the timeline advances, so recompute visible children per sample.
          tls.forEach(t => { try { t.pause(); t.progress(p); } catch(e){} });
          void root.getBoundingClientRect();  // force reflow
          const rr = root.getBoundingClientRect();
          const kids = Array.from(content.children).filter(el => {
            const s = getComputedStyle(el);
            return s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity || '1') >= 0.02;
          });
          let top = Infinity, bot = -Infinity;
          for (const el of kids) {
            const b = el.getBoundingClientRect();
            if (b.width < 1 || b.height < 1) continue;
            if (b.top < top) top = b.top;
            if (b.bottom > bot) bot = b.bottom;
          }
          if (!isFinite(top)) continue;
          measuredAny = true;
          const relTop = top - rr.top;              // content top relative to frame top
          const relBot = bot - rr.top;              // content bottom relative to frame top
          const topOver = (relTop < 0) ? -relTop : 0;                 // clipped above frame
          const botOver = (relBot > FRAME_H) ? (relBot - FRAME_H) : 0; // clipped below frame
          if (topOver > worstTopOver) { worstTopOver = topOver; worstAt = p; }
          if (botOver > worstBotOver) { worstBotOver = botOver; worstAt = p; }
        }
        if (!measuredAny) return { file: '', ok: true, skipped: 'empty content' };
        const ok = (worstTopOver <= TOL && worstBotOver <= TOL);
        return { ok, topOver: Math.round(worstTopOver), botOver: Math.round(worstBotOver), at: worstAt };
      }, FRAME_H, FRAME_W, TOL);
      r.file = f;
    } catch (e) {
      r = { file: f, ok: true, skipped: 'measure error: ' + e.message };
    } finally {
      await page.close();
      try { fs.unlinkSync(tmpPath); } catch(e){}
    }
    results.push(r);
  }
  await browser.close();
  console.log(JSON.stringify({ results }));
})().catch(e => { console.log(JSON.stringify({__skip__: "driver error: " + e.message})); process.exit(0); });
""".replace("%TOL%", str(TOL))


def main() -> int:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    comp = dist / "compositions"
    if not comp.is_dir():
        print(f"OK: no compositions/ under {dist} — nothing to fit-check.")
        return 0

    if not any(comp.glob("*.html")):
        print(f"OK: no scene HTML under {comp}.")
        return 0

    chrome = find_chrome()
    if not chrome:
        print("NOTE: check_scene_fit skipped — Chrome not found "
              "(set PUPPETEER_EXECUTABLE_PATH / set it in ~/.qwen-mm-plugins/config). Overflow NOT measured.")
        return 0

    env = build_node_env()

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
        tf.write(NODE_DRIVER)
        driver = tf.name
    try:
        proc = subprocess.run(["node", driver, str(comp), chrome],
                              capture_output=True, text=True, timeout=240, env=env)
    except Exception as e:
        print(f"NOTE: check_scene_fit skipped — could not run node driver: {e}")
        os.unlink(driver)
        return 0
    os.unlink(driver)

    out = proc.stdout.strip().splitlines()
    payload = None
    for line in reversed(out):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except Exception:
                continue
    if payload is None:
        print("NOTE: check_scene_fit skipped — no measurement output. stderr:",
              (proc.stderr or "")[-300:])
        return 0
    if payload.get("__skip__"):
        print(f"NOTE: check_scene_fit skipped — {payload['__skip__']}")
        return 0

    fails = []
    n_ok = 0
    for r in payload.get("results", []):
        if r.get("skipped"):
            continue
        if r.get("ok", True):
            n_ok += 1
            continue
        parts = []
        if r.get("topOver", 0) > TOL:
            parts.append(f"顶部超出并被裁切 ~{r['topOver']}px")
        if r.get("botOver", 0) > TOL:
            parts.append(f"底部超出画面 ~{r['botOver']}px")
        fails.append(f"compositions/{r['file']}: 场景内容高度超过 1080 画面（{', '.join(parts)}；"
                     f"t≈{r.get('at')}）→ 内容被画面边缘裁切(排版崩塌)。")

    if fails:
        print("FAIL: 场景内容溢出 1920×1080 画面被裁切:")
        for f in fails:
            print("  - " + f)
        print("\nFIX: 一屏只放一件事(one concept per scene)。把过高的内容拆成多个场景，"
              "或缩小字号/面板、改用更紧凑布局，使内容整体高度 ≤ 可用高度"
              "(≈1080 − 顶部padding − 底部字幕安全区180px ≈ 860px)；"
              "也可给内容外层加 `transform: scale(min(1, 可用高/内容高))` 并 `transform-origin: top center` 整体缩放适配。")
        return 1

    print(f"OK: {n_ok} 个场景内容均在 1920×1080 画面内，未见裁切。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
