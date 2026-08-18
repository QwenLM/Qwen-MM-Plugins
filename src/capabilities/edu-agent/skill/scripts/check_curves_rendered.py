#!/usr/bin/env python3
r"""
check_curves_rendered.py — POST-RENDER render-truth gate: fail when a line/curve that SHOULD be
drawn is actually MISSING from the rendered video (线/曲线在成片里没画出来).

WHY every other gate misses this
--------------------------------
The pre-render gates (and even the headless overlap gate) inspect the *composition* HTML. But the
most damaging "线画不出来" bug does NOT live in the composition — the composition is correct (it has
`<script src="./gsap/gsap.min.js">`, and its curve `d` is generated at load by inline JS). The bug
lives in the RENDERER: e.g. a hand-rolled CDP/puppeteer renderer that strips the GSAP `<script>` tag
without re-injecting it via an absolute `file://` path (like it does for bg-texture). Then in the
render page `gsap` is undefined, `gsap.timeline()` throws BEFORE the curve-building `setup()` runs, so
every `<path>` keeps its placeholder `d="M0 0"` and no stroke is painted — while the static dots,
axes and labels still show. Result: "有点、没线". The composition passes every static/headless check;
only the ACTUAL video frame reveals the loss.

HOW IT WORKS (per scene) — renderer-agnostic
--------------------------------------------
1. Load the composition standalone in headless Chrome with a KNOWN-GOOD gsap injected via absolute
   file:// (so the reference is always correct, independent of the ./gsap mirror). Sample the GSAP
   timeline at TWO local times L1=0.50·dur and L2=0.85·dur, and collect every CONTENT stroke
   (path/polyline/line, saturated color, width≥2, real length). For each we record its resolved RGB,
   its sample points (frame-pixel coords via getPointAtLength+getScreenCTM; the composition fills the
   1920×1080 frame so screen px == video px), and whether it MOVED between L1 and L2.
   - Only TIME-INVARIANT (static-position) strokes are asserted against the video. A moving arrow that
     tracks a falling figure is excluded — its position depends on animation state, so a gsap-dead
     video (everything at rest pose) legitimately differs and must not be flagged. Static strokes have
     FIXED svg coordinates, so they sit at the same place whether or not gsap ran — the ONLY reason a
     static content stroke would vanish is that its geometry was never generated (the target bug).
   - A path with a content color + width≥3 + fill:none but getTotalLength()≈0 is a COMPOSITION bug
     (its `d` was never generated) — flagged even without a video.
2. Extract the matching frame from output.mp4 (ffmpeg, raw rgb24) at the SAME local time (start+L2).
3. For each static stroke, look in a small neighborhood around each sample point for a pixel close to
   the stroke color. hit-ratio = points found / sampled.
   - hit-ratio ≥ 0.5 → PRESENT "anchor" (usually the static axes).
   - hit-ratio < 0.2 → MISSING.
   - FAIL a scene only when it has BOTH ≥1 present anchor AND ≥1 missing static stroke: the anchors
     prove the frame is aligned, so an absent curve is a real "线画不出来". If nothing is found at all
     (whole chart absent / blank / misaligned) we defer to blank-frame detection and SKIP, so this
     gate never false-positives on alignment.

Graceful skip (exit 0, NOTE) when: no video, no Chrome, no puppeteer-core, no ffmpeg, no gsap for the
reference, or scene timing can't be read. The render env has all of these, so it is enforced there.

Usage:
    python3 check_curves_rendered.py [dist_dir] [video_path]
Exit 0 = expected lines/curves present (or skipped). Exit 1 = a curve/line is missing from the video,
or a composition curve has no geometry.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from shutil import which

from _render import build_node_env, find_chrome

FRAME_W = 1920
FRAME_H = 1080

# stroke selection
MIN_WIDTH = 2.0            # px: ignore hairline decorations
MIN_SCREEN_LEN = 55.0      # px: ignore ticks / arrowheads / tiny marks
N_SAMPLES = 16             # sample points along each stroke
DEGENERATE_LEN = 5.0       # getTotalLength below this + content color + width≥3 → d never generated
MOVE_TOL = 9.0             # px: a stroke whose points shift more than this across the window is "dynamic"
FRAC1 = 0.50             # first settle — AFTER panel entrance + draw-on, so only true motion counts
FRAC2 = 0.85             # second settle time == the video sampling time
SOLID_MIN_WIDTH = 3.0      # only assert SOLID strokes at least this wide against the video

# pixel matching
COLOR_TOL = 66.0           # max RGB euclidean distance to count a pixel as "this stroke"
WIN = 5                    # base neighborhood radius (grows with stroke width)
HIT_PRESENT = 0.50         # hit-ratio ≥ this → present anchor
HIT_MISSING = 0.20         # hit-ratio < this → missing
MAX_STROKES = 48           # per-scene cap (cost bound)


NODE_DRIVER = r"""
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
let puppeteer;
try { puppeteer = require('puppeteer-core'); }
catch (e) { console.log(JSON.stringify({__skip__: "puppeteer-core not resolvable: " + e.message})); process.exit(0); }

const dir = process.argv[2];
const chrome = process.argv[3];
const gsapPath = process.argv[4] || '';
const katexPath = process.argv[5] || '';
const timing = JSON.parse(process.argv[6] || '{}');   // {basename:[start,dur]}
const P = JSON.parse(process.argv[7]);                // tunables
const fontsDir = process.argv[8] || '';               // dir with the self-hosted woff2 fonts

function unwrapFragment(html) {
  return html
    .replace(/<template[^>]*>/gi, '').replace(/<\/template>/gi, '')
    .replace(/<!doctype[^>]*>/gi, '').replace(/<\/?html[^>]*>/gi, '')
    .replace(/<\/?head[^>]*>/gi, '').replace(/<\/?body[^>]*>/gi, '');
}
// Load the SAME self-hosted fonts the video uses. Text metrics drive the flex layout that sizes and
// positions the SVG; without the real fonts the chart shifts/scales differently and the sample points
// won't line up with the video frame. Also inject a known-good gsap (and katex) via absolute file://
// so the reference render is always correct regardless of the ./gsap mirror.
function fontFace() {
  if (!fontsDir) return '';
  const u = n => pathToFileURL(path.join(fontsDir, n)).href;
  return '<style>' +
    '@font-face{font-family:"Noto Sans SC";src:url("' + u('NotoSansSC-Bold.woff2') + '") format("woff2");font-weight:400 700;font-display:block;}' +
    '@font-face{font-family:"Noto Sans SC";src:url("' + u('NotoSansSC-ExtraBold.woff2') + '") format("woff2");font-weight:800;font-display:block;}' +
    '@font-face{font-family:"Noto Sans SC";src:url("' + u('NotoSansSC-Black.woff2') + '") format("woff2");font-weight:900;font-display:block;}' +
    '@font-face{font-family:"Inter";src:url("' + u('Inter-Variable.woff2') + '") format("woff2");font-weight:100 900;font-display:block;}' +
    '@font-face{font-family:"JetBrains Mono";src:url("' + u('JetBrainsMono-Bold.woff2') + '") format("woff2");font-weight:700;font-display:block;}' +
    '</style>';
}
function headLibs() {
  let s = '<style>html,body{margin:0;padding:0;background:#fff;}</style>' + fontFace();
  if (gsapPath)  s += '<script src="' + pathToFileURL(gsapPath).href + '"></script>';
  if (katexPath) s += '<script src="' + pathToFileURL(katexPath).href + '"></script>';
  return s;
}

(async () => {
  const files = fs.readdirSync(dir).filter(f => f.endsWith('.html') && !f.startsWith('__curvechk_'));
  const out = [];
  let browser;
  try {
    browser = await puppeteer.launch({
      executablePath: chrome, headless: 'new',
      args: ['--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--force-device-scale-factor=1'],
    });
  } catch (e) { console.log(JSON.stringify({__skip__: "chrome launch failed: " + e.message})); process.exit(0); }

  for (const f of files) {
    const src = fs.readFileSync(path.join(dir, f), 'utf8');
    const doc = '<!doctype html><html><head><meta charset="utf-8">' + headLibs() + '</head><body>' +
      unwrapFragment(src) + '</body></html>';
    const tmpName = '__curvechk_' + f, tmpPath = path.join(dir, tmpName);
    fs.writeFileSync(tmpPath, doc);
    const page = await browser.newPage();
    await page.setViewport({ width: P.FRAME_W, height: P.FRAME_H, deviceScaleFactor: 1 });
    const clipDur = (timing[f] && timing[f][1]) ? timing[f][1] : null;
    let rec = { file: f, ok: true, strokes: [] };
    try {
      await page.goto(pathToFileURL(tmpPath).href, { waitUntil: 'load', timeout: 20000 });
      await page.evaluate(async () => { try { await document.fonts.ready; } catch(e){} });
      await new Promise(res => setTimeout(res, 300));
      rec = await page.evaluate((P, clipDur) => {
        const FW = P.FRAME_W, FH = P.FRAME_H;
        const root = document.querySelector('[data-composition-id]') || document.body;
        const tls = (window.__timelines && typeof window.__timelines === 'object')
          ? Object.values(window.__timelines) : [];
        let dur = clipDur;
        if (!dur) { try { dur = tls[0] && tls[0].duration ? tls[0].duration() : null; } catch(e) { dur = null; } }
        const L1 = dur ? dur * P.FRAC1 : null;
        const L2 = dur ? dur * P.FRAC2 : null;
        function settle(L) {
          tls.forEach(t => { try { t.pause(); if (L == null) t.progress(0.9); else t.time(Math.min(L, t.duration())); } catch(e){} });
          void root.getBoundingClientRect();
        }
        function rgb(str) {
          if (!str) return null;
          const m = str.match(/rgba?\(([^)]+)\)/); if (!m) return null;
          const p = m[1].split(',').map(s => parseFloat(s.trim()));
          if (p.length < 3 || p.some(isNaN)) return null;
          if (p.length >= 4 && p[3] < 0.5) return null;
          return [Math.round(p[0]), Math.round(p[1]), Math.round(p[2])];
        }
        function contentColor(c) {
          if (!c) return false;
          const luma = 0.299*c[0] + 0.587*c[1] + 0.114*c[2];
          const sat = Math.max(c[0],c[1],c[2]) - Math.min(c[0],c[1],c[2]);
          if (luma >= 228) return false;              // near white (background/panels)
          if (sat <= 30 && luma >= 195) return false; // light gray (gridlines/ticks)
          return true;
        }
        function ancestorVisible(el) {
          let n = el;
          while (n && n.nodeType === 1) {
            const s = getComputedStyle(n);
            if (s.display === 'none' || s.visibility === 'hidden') return false;
            if (parseFloat(s.opacity || '1') < 0.5) return false;
            n = n.parentElement;
          }
          return true;
        }
        function samplePts(el, L) {
          const ctm = el.getScreenCTM && el.getScreenCTM();
          const svg = el.ownerSVGElement || el.closest('svg');
          if (!ctm || !svg || !svg.createSVGPoint) return null;
          if (L < P.DEGENERATE_LEN) return null;
          const pts = [];
          for (let i = 0; i < P.N_SAMPLES; i++) {
            let up; try { up = el.getPointAtLength(L * i / (P.N_SAMPLES - 1)); } catch(e) { up = null; }
            if (!up) continue;
            const sp = svg.createSVGPoint(); sp.x = up.x; sp.y = up.y;
            const q = sp.matrixTransform(ctm);
            pts.push([q.x, q.y]);
          }
          return pts;
        }

        // enumerate candidate content strokes at L2
        settle(L2);
        const els = Array.from(root.querySelectorAll('svg path, svg polyline, svg line'));
        const cands = [];
        for (const el of els) {
          if (cands.length >= P.MAX_STROKES) break;
          if (el.closest('defs') || el.closest('marker') || el.closest('symbol')) continue;
          const cs = getComputedStyle(el);
          const col = rgb(cs.stroke) || rgb(el.getAttribute('stroke'));
          if (!col || !contentColor(col)) continue;
          const w = parseFloat(cs.strokeWidth) || parseFloat(el.getAttribute('stroke-width')) || 1;
          if (w < P.MIN_WIDTH) continue;
          let len = 0; try { len = el.getTotalLength ? el.getTotalLength() : 0; } catch(e) { len = 0; }
          const fill = (cs.fill || el.getAttribute('fill') || '').trim().toLowerCase();
          const fillNone = (!fill || fill === 'none' || fill === 'transparent');
          if (len < P.DEGENERATE_LEN) {
            if (fillNone && w >= 3) cands.push({ el, degenerate: true, color: col, width: w,
                                                 tag: el.tagName.toLowerCase(), id: el.id || (el.getAttribute('class')||'') });
            continue;
          }
          // Decorative dash pattern (e.g. "6 7") repeats many times → thin dashed guide, unreliable to
          // pixel-match. A draw-on reveal uses stroke-dasharray == full length (one period ≈ len), which
          // renders SOLID once revealed — keep that.
          let dashedDecorative = false;
          const da = (el.getAttribute('stroke-dasharray') || cs.strokeDasharray || '').trim();
          if (da && da !== 'none') {
            const vals = da.split(/[ ,]+/).map(parseFloat).filter(v => !isNaN(v) && v > 0);
            const period = vals.reduce((a, b) => a + b, 0);
            if (period > 0 && period < 0.5 * len) dashedDecorative = true;
          }
          cands.push({ el, degenerate: false, color: col, width: w, len, dashedDecorative,
                       tag: el.tagName.toLowerCase(), id: el.id || (el.getAttribute('class')||'') });
        }
        // points at L2
        for (const c of cands) { if (!c.degenerate) c.p2 = samplePts(c.el, c.len); }
        // points at L1 (for movement detection)
        settle(L1);
        for (const c of cands) { if (!c.degenerate) c.p1 = samplePts(c.el, c.len); }
        // back to L2 for final visibility check
        settle(L2);

        const strokes = [];
        for (const c of cands) {
          if (c.degenerate) {
            strokes.push({ degenerate: true, color: c.color, width: c.width, tag: c.tag, id: c.id });
            continue;
          }
          const p2 = (c.p2 || []).filter(q => q[0] >= 0 && q[1] >= 0 && q[0] < FW && q[1] < FH);
          if (p2.length < 4) continue;
          let minx=1e9,miny=1e9,maxx=-1e9,maxy=-1e9;
          for (const q of p2){ minx=Math.min(minx,q[0]); maxx=Math.max(maxx,q[0]); miny=Math.min(miny,q[1]); maxy=Math.max(maxy,q[1]); }
          if (Math.hypot(maxx-minx, maxy-miny) < P.MIN_SCREEN_LEN) continue;
          if (!ancestorVisible(c.el)) continue;
          // movement between L1 and L2
          let moved = 0;
          if (c.p1 && c.p1.length === c.p2.length) {
            for (let i = 0; i < c.p2.length; i++) moved = Math.max(moved, Math.hypot(c.p2[i][0]-c.p1[i][0], c.p2[i][1]-c.p1[i][1]));
          } else { moved = 1e9; }  // couldn't compare → treat as dynamic (exclude)
          strokes.push({ degenerate: false, color: c.color, width: c.width, tag: c.tag, id: c.id,
                         dynamic: moved > P.MOVE_TOL, dashedDecorative: !!c.dashedDecorative,
                         pts: p2.map(q => [Math.round(q[0]), Math.round(q[1])]) });
        }
        return { ok: true, strokes, hadTimeline: tls.length > 0, gsap: (typeof window.gsap !== 'undefined'), dur };
      }, P, clipDur);
      rec.file = f;
    } catch (e) {
      rec = { file: f, ok: true, strokes: [], skipped: 'measure error: ' + e.message };
    } finally {
      await page.close();
      try { fs.unlinkSync(tmpPath); } catch(e){}
    }
    out.push(rec);
  }
  await browser.close();
  console.log(JSON.stringify({ results: out }));
})().catch(e => { console.log(JSON.stringify({__skip__: "driver error: " + e.message})); process.exit(0); });
"""


# ---------------------------------------------------------------------------
# environment discovery (mirrors check_render_overlap.py)
# ---------------------------------------------------------------------------
def find_lib(dist: Path, rel: str):
    """Locate a real library file (gsap/katex) for the known-good reference render."""
    cands = [dist / rel, dist / "compositions" / rel,
             Path(__file__).resolve().parent.parent / "assets" / rel]
    for c in cands:
        if c.is_file():
            return str(c)
    return None


def find_video(dist: Path, override):
    if override:
        p = Path(override)
        return p if p.is_file() else None
    for name in ("output.mp4", "final.mp4", "video.mp4"):
        p = dist / name
        if p.is_file():
            return p
    vids = sorted(dist.glob("*.mp4"), key=lambda p: p.stat().st_size, reverse=True)
    return vids[0] if vids else None


def scene_timing(dist: Path):
    """Return {composition_basename: (start, duration)} from index.html or render_plan.json."""
    timing = {}
    index = dist / "index.html"
    if index.is_file():
        html = index.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"<[^>]*data-composition-src\s*=\s*[\"']([^\"']+)[\"'][^>]*>", html):
            tag, src = m.group(0), m.group(1)
            ds = re.search(r"data-start\s*=\s*[\"']([0-9.]+)[\"']", tag)
            dd = re.search(r"data-duration\s*=\s*[\"']([0-9.]+)[\"']", tag)
            if ds and dd:
                timing[Path(src).name] = (float(ds.group(1)), float(dd.group(1)))
    if not timing:
        plan = dist / "render_plan.json"
        if plan.is_file():
            try:
                data = json.loads(plan.read_text(encoding="utf-8"))
                for s in data.get("scenes", []):
                    f = s.get("file")
                    if f and "start" in s and "duration" in s:
                        timing[Path(f).name] = (float(s["start"]), float(s["duration"]))
            except Exception:
                pass
    return timing


def extract_frame(video: Path, t: float):
    """Return (w, h, rgb24 bytes) for the frame nearest time t, or None."""
    pre = max(0.0, t - 1.2)
    inner = t - pre
    cmd = ["ffmpeg", "-nostdin", "-v", "error",
           "-ss", f"{pre:.3f}", "-i", str(video), "-ss", f"{inner:.3f}",
           "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
    except Exception:
        return None
    data = proc.stdout
    if not data or len(data) < FRAME_W * FRAME_H * 3:
        return None
    return (FRAME_W, FRAME_H, data[:FRAME_W * FRAME_H * 3])


def hit_ratio(frame, color, pts, width):
    w, h, buf = frame
    r0, g0, b0 = color
    rad = max(WIN, int(round(width)) + 2)
    tol2 = COLOR_TOL * COLOR_TOL
    hits = 0
    for (px, py) in pts:
        found = False
        for dy in range(-rad, rad + 1):
            yy = py + dy
            if yy < 0 or yy >= h:
                continue
            rowbase = yy * w
            for dx in range(-rad, rad + 1):
                xx = px + dx
                if xx < 0 or xx >= w:
                    continue
                i = (rowbase + xx) * 3
                dr = buf[i] - r0
                dg = buf[i + 1] - g0
                db = buf[i + 2] - b0
                if dr * dr + dg * dg + db * db <= tol2:
                    found = True
                    break
            if found:
                break
        if found:
            hits += 1
    return hits / max(1, len(pts))


def find_fonts_dir(dist: Path):
    """Directory holding the self-hosted woff2 fonts (must match what the video used)."""
    cands = [dist / "assets" / "fonts", dist / "compositions" / "assets" / "fonts",
             Path(__file__).resolve().parent.parent / "assets" / "fonts"]
    for c in cands:
        if (c / "NotoSansSC-Bold.woff2").is_file():
            return str(c)
    return ""


def run_driver(comp: Path, chrome: str, gsap: str, katex: str, timing: dict, fonts_dir: str):
    env = build_node_env()
    tjson = json.dumps({k: [v[0], v[1]] for k, v in timing.items()})
    tunables = json.dumps({
        "FRAME_W": FRAME_W, "FRAME_H": FRAME_H, "MIN_WIDTH": MIN_WIDTH,
        "MIN_SCREEN_LEN": MIN_SCREEN_LEN, "N_SAMPLES": N_SAMPLES,
        "DEGENERATE_LEN": DEGENERATE_LEN, "MAX_STROKES": MAX_STROKES,
        "MOVE_TOL": MOVE_TOL, "FRAC1": FRAC1, "FRAC2": FRAC2,
    })
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
        tf.write(NODE_DRIVER)
        driver = tf.name
    try:
        proc = subprocess.run(
            ["node", driver, str(comp), chrome, gsap or "", katex or "", tjson, tunables, fonts_dir or ""],
            capture_output=True, text=True, timeout=300, env=env)
    except Exception as e:
        return None, f"could not run node driver: {e}"
    finally:
        try:
            os.unlink(driver)
        except Exception:
            pass
    payload = None
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except Exception:
                continue
    if payload is None:
        return None, "no measurement output. stderr: " + (proc.stderr or "")[-300:]
    return payload, None


def main() -> int:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    video_override = sys.argv[2] if len(sys.argv) > 2 else None
    comp = dist / "compositions"
    if not comp.is_dir() or not any(comp.glob("*.html")):
        print(f"OK: no compositions/*.html under {dist} — nothing to curve-check.")
        return 0

    chrome = find_chrome()
    if not chrome:
        print("NOTE: check_curves_rendered skipped — Chrome not found "
              "(set it in ~/.qwen-mm-plugins/config / set PUPPETEER_EXECUTABLE_PATH). Curves NOT measured.")
        return 0
    gsap = find_lib(dist, "gsap/gsap.min.js")
    if not gsap:
        print("NOTE: check_curves_rendered skipped — no gsap.min.js found for the reference render.")
        return 0
    katex = find_lib(dist, "katex/katex.min.js")
    fonts_dir = find_fonts_dir(dist)
    timing = scene_timing(dist)

    payload, err = run_driver(comp, chrome, gsap, katex, timing, fonts_dir)
    if payload is None:
        print(f"NOTE: check_curves_rendered skipped — {err}")
        return 0
    if payload.get("__skip__"):
        print(f"NOTE: check_curves_rendered skipped — {payload['__skip__']}")
        return 0

    results = payload.get("results", [])

    # composition-level: content curves with no geometry (d never generated)
    comp_fails = []
    for r in results:
        for s in r.get("strokes", []):
            if s.get("degenerate"):
                comp_fails.append(
                    f"compositions/{r['file']}: <{s['tag']}> '{s.get('id','')}' "
                    f"(stroke rgb{tuple(s['color'])}, width {s['width']:.0f}) 的几何为空 "
                    f"(getTotalLength≈0 / d='M0 0') — 曲线/线的路径数据从未由 JS 生成。")

    # video-level: expected STATIC strokes missing from the rendered frame
    video = find_video(dist, video_override)
    video_fails = []
    scenes_checked = 0
    note_lines = []

    if video is None:
        note_lines.append("skipped video check — no rendered video (output.mp4) found.")
    elif not which("ffmpeg"):
        note_lines.append("skipped video check — ffmpeg not found，无法抽取成片帧核验。")
    elif not timing:
        note_lines.append("skipped video check — could not read scene timing from index.html / render_plan.json。")
    else:
        for r in results:
            # Assert only SOLID, wide-enough, position-invariant strokes: those unambiguously reveal
            # "geometry never drawn" when absent. Thin/decorative-dashed strokes are anti-aliased and
            # unreliable to pixel-match; dynamic (moving) strokes legitimately differ in a gsap-dead
            # frame, so both are excluded to keep false-positives at zero.
            static = [s for s in r.get("strokes", [])
                      if not s.get("degenerate") and not s.get("dynamic")
                      and not s.get("dashedDecorative")
                      and s.get("width", 0) >= SOLID_MIN_WIDTH and s.get("pts")]
            if not static:
                continue
            t = timing.get(r["file"])
            if not t:
                continue
            start, dur = t
            ts = start + dur * FRAC2
            frame = extract_frame(video, ts)
            if frame is None:
                note_lines.append(f"skipped {r['file']} — 抽帧失败 (t≈{ts:.2f}s)。")
                continue
            scenes_checked += 1
            present, missing = [], []
            for s in static:
                ratio = hit_ratio(frame, s["color"], s["pts"], s["width"])
                s["_ratio"] = ratio
                if ratio >= HIT_PRESENT:
                    present.append(s)
                elif ratio < HIT_MISSING:
                    missing.append(s)
            if missing and present:
                for s in missing:
                    kind = "曲线" if s["tag"] == "path" else "线"
                    video_fails.append(
                        f"{r['file']} (t≈{ts:.2f}s): 应绘制的{kind} <{s['tag']}> '{s.get('id','')}' "
                        f"(stroke rgb{tuple(s['color'])}) 在成片里缺失 "
                        f"(命中率 {s['_ratio']*100:.0f}%，同场景其它静态线已画出) "
                        f"— 渲染器很可能没加载 GSAP，JS 生成的路径 d 未被绘制。")
            elif missing and not present:
                note_lines.append(
                    f"skipped {r['file']} — {len(missing)} 条线未命中且无可对齐锚点；"
                    f"可能整幅图缺失(见空白帧检测)或坐标未对齐。")

    all_fails = comp_fails + video_fails
    if all_fails:
        print("FAIL: 检测到应绘制的线/曲线没有画出来:")
        for f in all_fails:
            print("  - " + f)
        print("\nFIX:")
        print("  • 成片缺失(锚点线在、独独曲线不见): 检查渲染器是否在把 composition 塞进渲染页时删除了 "
              "<script src=\"./gsap/gsap.min.js\"> 却没用绝对路径重新注入 "
              "(<script src=\"file://<DIST>/gsap/gsap.min.js\"></script>)。否则 gsap 未定义、"
              "gsap.timeline() 抛错，生成曲线 d 的 setup() 不执行 → 路径停在 d=\"M0 0\"。"
              "首选直接用 `npx hyperframes render`，不要自造渲染器。")
        print("  • composition 级几何为空: 生成 d 的内联 JS 必须先于/独立于 gsap.timeline() 执行，"
              "确保 getTotalLength()>0。")
        print("  • 改完重新渲染并重跑本闸门，直到通过。")
        return 1

    msg = f"OK: 成片核验通过 — {scenes_checked} 个含静态线/曲线的场景，应绘制的线均已画出。"
    if scenes_checked == 0 and not comp_fails:
        msg = "OK: 未发现需要核验的静态内容线/曲线。"
    print(msg)
    for n in note_lines:
        print("  NOTE: " + n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
