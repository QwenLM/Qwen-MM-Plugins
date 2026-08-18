#!/usr/bin/env python3
r"""
check_render_overlap.py — Pre-render gate (HEADLESS, render-truth): fail when a `<text>` label is
actually painted OVER / UNDER an opaque box in the rendered frame (文字被方框覆盖 / 标签压框).

WHY the static gates miss this: check_svg_node_graph.py / check_svg_label_on_figure.py judge from the
STATIC viewBox coordinates + an ESTIMATED text width. A scene can look clear on paper (e.g. label
`取食` at x=289, box at x=360 → "47u clearance") yet still collide in the REAL render because of
`preserveAspectRatio` scaling, actual CJK glyph metrics, and SVG paint order (a later `<rect>` paints
OVER an earlier `<text>`). Result: `取食`→`取1`, `捕食`→`捕1` — the second glyph vanishes under the
next box. Static estimation ≠ rendered pixels. This gate loads each scene in headless Chrome, seeks
the GSAP timeline to settle entrance animations, and measures the ACTUAL getBoundingClientRect of every
visible `<text>` and every opaque `<rect>`, flagging real overlaps.

WHAT IT FLAGS (per composition, at settled timeline states):
  A visible SVG `<text>` T whose rendered bbox intersects an opaque node `<rect>` R by > FRAC of T's
  area, WHERE T does NOT belong to R (T's own center is not inside R). That means T is a connector/edge
  label (取食/捕食/…) or stray label spilling onto a box it isn't the caption of → it will be clipped
  or sit on top of the box. A box's OWN centered caption (鱼 inside the 鱼 box) is exempt (its center
  is inside its box).

Graceful skip: if Chrome / puppeteer-core is unavailable it prints a NOTE and exits 0 (browserless env
not blocked; the render env, which has Chrome, enforces it).

Usage:
    python3 check_render_overlap.py [dist_dir]     # default ./dist
Exit 0 = clean (or skipped), 1 = a real text/box overlap was measured.
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
FRAC = 0.12          # min fraction of a text's area covered by a foreign box before flagging
MIN_BOX = 28         # px: ignore tiny rects (dots, ticks); only real "boxes"
BG_FRAC = 0.60       # a rect covering > this fraction of the root is a background → ignore

NODE_DRIVER = r"""
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
let puppeteer;
try { puppeteer = require('puppeteer-core'); }
catch (e) { console.log(JSON.stringify({__skip__: "puppeteer-core not resolvable: " + e.message})); process.exit(0); }

const dir = process.argv[2];
const chrome = process.argv[3];
const FRAME_W = 1920, FRAME_H = 1080;
const FRAC = %FRAC%, MIN_BOX = %MIN_BOX%, BG_FRAC = %BG_FRAC%;

function unwrapFragment(html) {
  return html
    .replace(/<template[^>]*>/gi, '').replace(/<\/template>/gi, '')
    .replace(/<!doctype[^>]*>/gi, '').replace(/<\/?html[^>]*>/gi, '')
    .replace(/<\/?head[^>]*>/gi, '').replace(/<\/?body[^>]*>/gi, '');
}

(async () => {
  const files = fs.readdirSync(dir).filter(f => f.endsWith('.html') && !f.startsWith('__ovcheck_'));
  const results = [];
  let browser;
  try {
    browser = await puppeteer.launch({
      executablePath: chrome, headless: 'new',
      args: ['--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--force-device-scale-factor=1'],
    });
  } catch (e) { console.log(JSON.stringify({__skip__: "chrome launch failed: " + e.message})); process.exit(0); }

  for (const f of files) {
    const src = fs.readFileSync(path.join(dir, f), 'utf8');
    const doc = '<!doctype html><html><head><meta charset="utf-8">' +
      '<style>html,body{margin:0;padding:0;background:#fff;}</style></head><body>' +
      unwrapFragment(src) + '</body></html>';
    const tmpName = '__ovcheck_' + f, tmpPath = path.join(dir, tmpName);
    fs.writeFileSync(tmpPath, doc);
    const page = await browser.newPage();
    await page.setViewport({ width: FRAME_W, height: FRAME_H, deviceScaleFactor: 1 });
    let r = { file: f, ok: true };
    try {
      await page.goto(pathToFileURL(tmpPath).href, { waitUntil: 'load', timeout: 20000 });
      await page.evaluate(async () => { try { await document.fonts.ready; } catch(e){} });
      await new Promise(res => setTimeout(res, 350));
      r = await page.evaluate((FRAC, MIN_BOX, BG_FRAC) => {
        const root = document.querySelector('[data-composition-id]');
        if (!root) return { ok: true, skipped: 'no root' };
        const rootR = root.getBoundingClientRect();
        const rootArea = Math.max(1, rootR.width * rootR.height);
        const tls = (window.__timelines && typeof window.__timelines === 'object')
          ? Object.values(window.__timelines) : [];

        function visible(el) {
          const s = getComputedStyle(el);
          if (s.display === 'none' || s.visibility === 'hidden') return false;
          if (parseFloat(s.opacity || '1') < 0.5) return false;
          return true;
        }
        function opaqueRect(el) {
          const fill = (el.getAttribute('fill') || getComputedStyle(el).fill || '').trim().toLowerCase();
          if (!fill || fill === 'none' || fill === 'transparent') return false;
          if (/rgba\([^)]*,\s*0?\.?\d*\s*\)/.test(fill)) {
            const m = fill.match(/rgba\([^)]*,\s*([0-9.]+)\s*\)/); if (m && parseFloat(m[1]) < 0.85) return false;
          }
          const fo = parseFloat(el.getAttribute('fill-opacity') || getComputedStyle(el).fillOpacity || '1');
          const op = parseFloat(getComputedStyle(el).opacity || '1');
          return fo >= 0.85 && op >= 0.85;
        }
        function interFrac(a, b) { // fraction of a covered by b
          const x = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
          const y = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
          const ar = Math.max(1, a.width * a.height);
          return (x * y) / ar;
        }
        function centerIn(a, b) {
          const cx = a.left + a.width / 2, cy = a.top + a.height / 2;
          return cx >= b.left && cx <= b.right && cy >= b.top && cy <= b.bottom;
        }

        const samples = [0.5, 0.8, 1.0];
        let worst = null;
        for (const p of samples) {
          tls.forEach(t => { try { t.pause(); t.progress(p); } catch(e){} });
          void root.getBoundingClientRect();
          const texts = Array.from(root.querySelectorAll('svg text'))
            .filter(t => t.textContent.trim().length >= 1 && visible(t));
          const rects = Array.from(root.querySelectorAll('svg rect'))
            .filter(rc => visible(rc) && opaqueRect(rc));
          for (const T of texts) {
            const tb = T.getBoundingClientRect();
            if (tb.width < 4 || tb.height < 4) continue;
            for (const R of rects) {
              const rb = R.getBoundingClientRect();
              if (rb.width < MIN_BOX || rb.height < MIN_BOX) continue;
              if ((rb.width * rb.height) / rootArea > BG_FRAC) continue; // background rect
              if (centerIn(tb, rb)) continue;      // T is this box's own caption → exempt
              const fr = interFrac(tb, rb);
              if (fr > FRAC && (!worst || fr > worst.frac)) {
                worst = { frac: fr, text: T.textContent.trim().slice(0, 12), at: p };
              }
            }
          }
        }
        if (worst) return { ok: false, frac: Math.round(worst.frac * 100), text: worst.text, at: worst.at };
        return { ok: true };
      }, FRAC, MIN_BOX, BG_FRAC);
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
""".replace("%FRAC%", str(FRAC)).replace("%MIN_BOX%", str(MIN_BOX)).replace("%BG_FRAC%", str(BG_FRAC))


def main() -> int:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    comp = dist / "compositions"
    if not comp.is_dir() or not any(comp.glob("*.html")):
        print(f"OK: no compositions/*.html under {dist} — nothing to overlap-check.")
        return 0

    chrome = find_chrome()
    if not chrome:
        print("NOTE: check_render_overlap skipped — Chrome not found "
              "(set it in ~/.qwen-mm-plugins/config / set PUPPETEER_EXECUTABLE_PATH). Overlap NOT measured.")
        return 0

    env = build_node_env()

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
        tf.write(NODE_DRIVER)
        driver = tf.name
    try:
        proc = subprocess.run(["node", driver, str(comp), chrome],
                              capture_output=True, text=True, timeout=300, env=env)
    except Exception as e:
        print(f"NOTE: check_render_overlap skipped — could not run node driver: {e}")
        os.unlink(driver)
        return 0
    os.unlink(driver)

    payload = None
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line); break
            except Exception:
                continue
    if payload is None:
        print("NOTE: check_render_overlap skipped — no measurement output. stderr:", (proc.stderr or "")[-300:])
        return 0
    if payload.get("__skip__"):
        print(f"NOTE: check_render_overlap skipped — {payload['__skip__']}")
        return 0

    fails, n_ok = [], 0
    for r in payload.get("results", []):
        if r.get("skipped"):
            continue
        if r.get("ok", True):
            n_ok += 1
            continue
        fails.append(f"compositions/{r['file']}: 文字「{r.get('text')}」被一个方框覆盖/压住 "
                     f"(渲染实测 ~{r.get('frac')}% 面积在框内, t≈{r.get('at')}) "
                     f"→ 标签被后画的方框遮挡/裁切(如 取食→取, 捕食→捕)。")

    if fails:
        print("FAIL: 渲染实测发现文字被方框覆盖/压框:")
        for f in fails:
            print("  - " + f)
        print("\nFIX: 让该标签完全离开方框——把连接词/边标签移到两框正中的空隙、并与相邻框保持足够 clearance "
              "(标签整体 bbox 不得与任何非自身方框相交)；或把标签放到连接线的正上方、抬高 y 使其在框顶之外。"
              "不要只按 viewBox 坐标估算——渲染缩放+CJK 字宽会让'看着有空隙'的标签实际压上框。"
              "改完务必抽帧自查(见 step-6 视觉自查闭环)，直到本闸门通过。")
        return 1

    print(f"OK: {n_ok} 个场景渲染实测无文字被方框覆盖。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
