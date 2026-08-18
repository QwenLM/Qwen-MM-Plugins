#!/usr/bin/env python3
r"""
check_smooth_curve_render.py — POST-RENDER (visual) gate: a continuous/smooth curve must NOT be
drawn as a handful of straight chords (不要用分段直线逼近曲线) — verified on the RENDERED geometry.

WHY A RENDER-BASED VERSION (in addition to the static check_smooth_curve.py):
  The static gate reads the `d`/`points` string in the source. It catches source-authored
  polylines (e.g. `<path d="M488,585 L500,540 L530,480 ...">` — a hyperbola drawn as 7 straight
  segments). But it is BLIND to curves whose geometry is generated at runtime by JS
  (`el.setAttribute('d', ...)`, canvas-style point loops, GSAP): in the source the `d` is empty
  or a placeholder, so the static parser sees nothing. Only the RENDERED path reveals the shape.
  This gate loads the scene in headless Chrome, seeks GSAP timelines to settled state, then walks
  the ACTUAL rendered path with getPointAtLength and measures its turning profile.

DETECTION (per candidate curve, low false-positive):
  A candidate is a stroked, open, fill:none `<path>`/`<polyline>` long enough to be a plotted
  curve, in a scene whose text has a continuous-curve cue (抛物线|双曲线|正弦|指数|衰减|种群|
  振荡|y=x²|1/x|sin|…), and NOT in a genuinely piecewise-linear scene (分段函数|绝对值|折线图|
  阶梯|距离-时间 → exempt). We densely sample the rendered path (~1 pt / 5 user-units) and count
  "corners" — vertices whose local turning angle exceeds CORNER_DEG. A smooth curve (Bézier or
  dense sampling) turns GRADUALLY: almost every step is < CORNER_DEG, so corners ≈ 0. A polyline
  of K chords concentrates all its turning at the K−1 joints, straight in between → several
  corners with straight runs. FAIL when a candidate has ≥ MIN_CORNERS sharp joints.

  (Turning angles are computed in the path's own user space; under the uniform viewBox scale — and
  even a group rotate — angular DIFFERENCES are preserved, so this equals the on-screen jaggedness.)

Graceful skip: if Chrome / puppeteer-core is unavailable it prints a NOTE and exits 0 (the render
env has Chrome and enforces it).

Usage: python3 check_smooth_curve_render.py [dist_dir]     # default ./dist
Exit 0 = clean / skipped, 1 = a continuous curve is rendered as straight chords.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from _render import build_node_env, find_chrome

CORNER_DEG = 14.0     # a windowed turn (~8px before vs after) larger than this is a kink (chord joint)
MIN_CORNERS = 2       # this many sharp kinks (with straight runs between) ⇒ straight-chord polyline
SAMPLE_STEP = 3.0     # user-units between path samples (dense; kinks measured over an ~8px window)

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
const CORNER_DEG = %CORNER_DEG%, MIN_CORNERS = %MIN_CORNERS%, STEP = %STEP%;

function unwrapFragment(html) {
  return html.replace(/<template[^>]*>/gi, '').replace(/<\/template>/gi, '')
    .replace(/<!doctype[^>]*>/gi, '').replace(/<\/?html[^>]*>/gi, '')
    .replace(/<\/?head[^>]*>/gi, '').replace(/<\/?body[^>]*>/gi, '');
}

function PAGE_FN(CORNER_DEG, MIN_CORNERS, STEP) {
  // textContent (not innerText) so SVG <text> labels & legends count toward the cue
  const txt = ((document.body && document.body.textContent) || '')
      + ' ' + Array.from(document.querySelectorAll('text,tspan')).map(function(t){return t.textContent;}).join(' ');
  const CUE = /(抛物线|双曲线|正弦|余弦|三角函数|指数|对数|衰减|增长曲线|种群|振荡|波动|阻尼|生态|平滑曲线|光滑曲线|连续曲线|正态|钟形|反比例|parabola|hyperbol|sine|cosine|logarithm|exponential|decay|oscillat|gaussian|y\s*=\s*x\s*[\^²2]|x²|y\s*=\s*1\s*\/\s*x|y\s*=\s*\\?sin|y\s*=\s*\\?cos|y\s*=\s*e\s*\^|k\s*\/\s*x|k\}\{x)/i;
  const PIECEWISE = /(分段函数|分段线性|绝对值|\|\s*x\s*\||折线统计图|折线图|折线|阶梯|台阶|距离[-—－]?时间|路程[-—－]?时间|s\s*[-—－]\s*t\s*图|v\s*[-—－]\s*t\s*图|piecewise|step\s*chart|step\s*function|line\s*chart)/i;
  if (!CUE.test(txt)) return { skip: 'no continuous-curve cue' };
  if (PIECEWISE.test(txt)) return { skip: 'piecewise-linear scene (exempt)' };

  const tls = (window.__timelines && typeof window.__timelines === 'object') ? Object.values(window.__timelines) : [];
  tls.forEach(t => { try { t.pause(); t.progress(1); } catch (e) {} });

  function isArrowOrMarker(el) {
    if (el.getAttribute('marker-end') || el.getAttribute('marker-start')) return true;
    if (el.closest('marker,defs')) return true;
    return false;
  }
  // Windowed turning: at each point compare the chord ~WIN units BEFORE vs AFTER it. A polyline
  // KINK turns sharply within the window (the full joint angle); a smooth curve turns gradually
  // (small angle in the same window). Dense sampling can't hide a kink from a window that
  // straddles it. We then count kinks as contiguous over-threshold runs, and measure how much of
  // the path is DEAD STRAIGHT (chord interiors) — the polyline signature is "long straight runs
  // punctuated by a few kinks", vs a smooth curve that bends everywhere.
  function analyze(pts) {
    const n = pts.length;
    if (n < 7) return { kinks: 0, maxAng: 0, straightFrac: 1 };
    // choose window in index units ≈ 8 user-units of arc
    const stepLen = (function () {
      let tot = 0; for (let i = 1; i < n; i++) tot += Math.hypot(pts[i].x - pts[i-1].x, pts[i].y - pts[i-1].y);
      return tot / (n - 1);
    })();
    const W = Math.max(2, Math.round(8 / Math.max(0.5, stepLen)));
    const ang = new Array(n).fill(0);
    let measured = 0, straight = 0, maxAng = 0;
    for (let i = W; i < n - W; i++) {
      const ax = pts[i].x - pts[i-W].x, ay = pts[i].y - pts[i-W].y;
      const bx = pts[i+W].x - pts[i].x, by = pts[i+W].y - pts[i].y;
      const la = Math.hypot(ax, ay), lb = Math.hypot(bx, by);
      if (la < 1e-6 || lb < 1e-6) continue;
      let c = (ax*bx + ay*by) / (la*lb); c = Math.max(-1, Math.min(1, c));
      const a = Math.acos(c) * 180 / Math.PI;
      ang[i] = a; measured++;
      if (a < 3) straight++;
      if (a > maxAng) maxAng = a;
    }
    // count kinks = contiguous runs of ang > CORNER_DEG
    let kinks = 0, inRun = false;
    for (let i = W; i < n - W; i++) {
      if (ang[i] > CORNER_DEG) { if (!inRun) { kinks++; inRun = true; } }
      else inRun = false;
    }
    return { kinks, maxAng, straightFrac: measured ? straight / measured : 1 };
  }

  const findings = [];
  const cands = Array.from(document.querySelectorAll('svg path, svg polyline'));
  for (const el of cands) {
    if (isArrowOrMarker(el)) continue;
    const cs = getComputedStyle(el);
    if (cs.fill && cs.fill !== 'none' && cs.fill !== 'rgba(0, 0, 0, 0)' && cs.fill !== 'transparent') continue; // filled shape, not a plotted curve
    if (!cs.stroke || cs.stroke === 'none') continue;
    // build a dense point list in user space
    let pts = [];
    const tag = el.tagName.toLowerCase();
    if (tag === 'polyline') {
      const raw = (el.getAttribute('points') || '').trim().split(/[\s,]+/).map(Number);
      for (let i = 0; i + 1 < raw.length; i += 2) pts.push({ x: raw[i], y: raw[i+1] });
    } else {
      let L; try { L = el.getTotalLength(); } catch (e) { continue; }
      if (!L || L < 60) continue;
      const n = Math.min(600, Math.max(12, Math.ceil(L / STEP)));
      for (let i = 0; i <= n; i++) { try { const p = el.getPointAtLength(L * i / n); pts.push({ x: p.x, y: p.y }); } catch (e) {} }
    }
    if (pts.length < 7) continue;
    // span filter (skip tiny decorations) — use bbox in px
    const bb = el.getBoundingClientRect();
    if (Math.max(bb.width, bb.height) < 120) continue;

    const { kinks, maxAng, straightFrac } = analyze(pts);
    // polyline signature: >=MIN_CORNERS sharp kinks AND long dead-straight runs between them.
    // (A smooth curve — even high-curvature 1/x — turns continuously, so straightFrac is low.)
    if (kinks >= MIN_CORNERS && straightFrac > 0.45) {
      findings.push({ id: el.id || null, tag, kinks, maxAng: Math.round(maxAng),
                      straightPct: Math.round(straightFrac * 100), samples: pts.length,
                      spanPx: Math.round(Math.max(bb.width, bb.height)) });
    }
  }
  return { checked: true, findings };
}

(async () => {
  const files = fs.readdirSync(dir).filter(f => f.endsWith('.html') && !f.startsWith('__smoothchk_'));
  let browser;
  try { browser = await puppeteer.launch({ executablePath: chrome, headless: 'new', args: ['--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--force-device-scale-factor=1'] }); }
  catch (e) { console.log(JSON.stringify({__skip__: "chrome launch failed: " + e.message})); process.exit(0); }
  const results = [];
  for (const f of files) {
    const src = fs.readFileSync(path.join(dir, f), 'utf8');
    const doc = '<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;padding:0;background:#fff;}</style></head><body>' + unwrapFragment(src) + '</body></html>';
    const tmpPath = path.join(dir, '__smoothchk_' + f);
    fs.writeFileSync(tmpPath, doc);
    const page = await browser.newPage();
    await page.setViewport({ width: FRAME_W, height: FRAME_H, deviceScaleFactor: 1 });
    let r = { file: f };
    try {
      await page.goto(pathToFileURL(path.resolve(tmpPath)).href, { waitUntil: 'load', timeout: 20000 });
      await page.evaluate(async () => { try { await document.fonts.ready; } catch (e) {} });
      await new Promise(res => setTimeout(res, 350));
      r = Object.assign({ file: f }, await page.evaluate(PAGE_FN, CORNER_DEG, MIN_CORNERS, STEP));
    } catch (e) { r = { file: f, skip: 'measure error: ' + e.message }; }
    finally { await page.close(); try { fs.unlinkSync(tmpPath); } catch (e) {} }
    results.push(r);
  }
  await browser.close();
  console.log(JSON.stringify({ results }));
})().catch(e => { console.log(JSON.stringify({__skip__: "driver error: " + e.message})); process.exit(0); });
""".replace("%CORNER_DEG%", str(CORNER_DEG)).replace("%MIN_CORNERS%", str(MIN_CORNERS)).replace("%STEP%", str(SAMPLE_STEP))


def main():
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    comp = dist / "compositions"
    if not comp.is_dir() or not any(comp.glob("*.html")):
        print(f"OK: no compositions/ under {dist} — nothing to smooth-curve check.")
        return 0
    chrome = find_chrome()
    if not chrome:
        print("NOTE: check_smooth_curve_render skipped — Chrome not found "
              "(set PUPPETEER_EXECUTABLE_PATH / set it in ~/.qwen-mm-plugins/config). "
              "Static check_smooth_curve.py still applies at pre-render.")
        return 0

    env = build_node_env()
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
        tf.write(NODE_DRIVER)
        driver = tf.name
    try:
        proc = subprocess.run(["node", driver, str(comp.resolve()), chrome],
                              capture_output=True, text=True, timeout=300, env=env)
    except Exception as e:
        os.unlink(driver)
        print(f"NOTE: check_smooth_curve_render skipped — node driver error: {e}")
        return 0
    os.unlink(driver)

    payload = None
    for ln in reversed(proc.stdout.strip().splitlines()):
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                payload = json.loads(ln)
                break
            except Exception:
                continue
    if payload is None or payload.get("__skip__"):
        print(f"NOTE: check_smooth_curve_render skipped — {payload.get('__skip__') if payload else 'no output'}")
        return 0

    fails = []
    for r in payload.get("results", []):
        for f in r.get("findings", []):
            who = f"#{f['id']}" if f.get("id") else f"<{f['tag']}>"
            fails.append(f"compositions/{r['file']}: rendered curve {who} is a STRAIGHT-CHORD "
                         f"polyline — {f['kinks']} sharp kinks (max turn {f['maxAng']}°, "
                         f"{f['straightPct']}% dead-straight between kinks, {f['samples']} samples, "
                         f"span {f['spanPx']}px) → 用分段直线逼近曲线 (jagged zig-zag, not smooth).")

    if fails:
        print("FAIL: continuous curve(s) rendered as straight chords (分段直线逼近曲线):")
        for f in fails:
            print("  " + f)
        print("\nFIX: rewrite the curve so its RENDERED path is smooth, then re-render and re-check:")
        print("  - Build the path `d` with the Catmull-Rom `smoothPath()` helper (cubic Béziers "
              "through your data points) — see references/step-5-build-components.md → "
              "\"Smooth curves (顺滑曲线)\"; OR")
        print("  - Emit a DENSELY-sampled point list (~1 pt per ≤10 px, ~120+ pts across a wide "
              "axis) so no single step turns sharply.")
        print("  Genuinely piecewise-linear graphs (y=|x|, 分段函数, 折线统计图, 匀速距离-时间) "
              "are exempt — do not force those smooth.")
        return 1

    print("OK: continuous curves render smooth (no straight-chord polylines).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
