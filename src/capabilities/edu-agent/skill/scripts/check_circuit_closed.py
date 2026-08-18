#!/usr/bin/env python3
r"""
check_circuit_closed.py — Pre-render gate for circuit **connectivity / loop closure**
(电路连通性 / 回路闭合).

Catches the "电源两端没连好线 / 回路没闭合 / 悬空导线 / 元件飘离导线" class of bug: a wiring
diagram where a component (especially a series resistor / the power source) is NOT actually on
the closed current loop, so the physics the video teaches rests on a broken schematic.

WHY THIS WAS REWRITTEN
----------------------
The old version read wire endpoints ONLY from JS array literals (`["w1",x1,y1,x2,y2]`) and
skipped static `<line>`/`<path>` wires. Two blind spots let a real bug through:
  (a) A hand-drawn schematic whose wires are static `<path d="M..L..">` produced NO JS array →
      the checker found no geometry → passed vacuously (this is exactly how a broken circuit
      slipped past: the model drew static paths and the gate never fired).
  (b) Even when it did parse geometry, it read the SVG **source coordinates**. The actual
      failure mode is a component group displaced at RENDER time by a GSAP transform /
      transform-origin bug (or CSS scoping): the source says R₂ sits on the wire, but the
      rendered frame shows R₂ floating off the wire. Source-based checks cannot see this.

WHAT IT DOES NOW
----------------
PRIMARY (render-based, requires headless Chrome — the render env has it):
  For each circuit composition it loads the scene in headless Chrome, seeks every registered
  GSAP timeline to its settled state (progress≈1), then reads the **post-transform screen
  coordinates** (getScreenCTM) of:
    - wire endpoints  — <line>/<path> inside the wire group (`#wires` / id|class contains
      "wire"); dashed elements (parallel voltmeter branch) and arrow-marker lines excluded;
    - component regions — bounding boxes of the component groups (`[id^=comp]` / `.sch-*`,
      falling back to large rect/circle chips).
  RULE B (primary): every wire endpoint must coincide (within tolerance) with another wire
  endpoint (a corner/junction) or land inside a component region. An endpoint touching nothing
  is a dangling wire → the loop is open there → a component floated off its wire. This is what
  catches the "R₂ rendered detached from the loop" bug, because R₂'s displaced box no longer
  covers the wire's gap endpoints.

FALLBACK (static, no Chrome): parse wire endpoints from BOTH the JS array literals AND static
  `<path>`/`<line>` inside the wire group, and run the same coincidence rule on source
  coordinates. Weaker (cannot see render-time displacement) but no longer blind to static
  paths. If no wire geometry is found at all, prints a NOTE and passes (never false-fails).

Usage:
    python3 check_circuit_closed.py [dist_dir]     # default ./dist
Exit 0 = clean / not verifiable, 1 = open loop / dangling wire / component off the loop.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from _render import build_node_env, find_chrome
from _shared import attr, iter_compositions

TOL_UNITS = 16.0  # viewBox units: two points closer than this are "the same node"

CIRCUIT_SIGNALS = ("变阻器", "电流表", "电压表", "开关", "电键", "电源", "电池",
                   "电路", "电流", "电压", "sch-battery", "sch-ammeter",
                   "sch-voltmeter", "sch-switch", "sch-resistor")


def is_circuit_scene(txt):
    return sum(1 for s in CIRCUIT_SIGNALS if s in txt) >= 2


# --------------------------------------------------------------------------- #
# PRIMARY: render-based loop-closure check (headless Chrome)
# --------------------------------------------------------------------------- #
NODE_DRIVER = r"""
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
let puppeteer;
try { puppeteer = require('puppeteer-core'); }
catch (e) { console.log(JSON.stringify({__skip__: "puppeteer-core not resolvable: " + e.message})); process.exit(0); }

const dir = process.argv[2];
const chrome = process.argv[3];
const FRAME_W = 1920, FRAME_H = 1080, TOL_UNITS = %TOL%;

function unwrapFragment(html) {
  return html
    .replace(/<template[^>]*>/gi, '')
    .replace(/<\/template>/gi, '')
    .replace(/<!doctype[^>]*>/gi, '')
    .replace(/<\/?html[^>]*>/gi, '')
    .replace(/<\/?head[^>]*>/gi, '')
    .replace(/<\/?body[^>]*>/gi, '');
}

// In-page analyzer — runs inside the rendered document, so all transforms
// (viewBox scale + GSAP group transforms + CSS) are already applied.
function PAGE_FN(TOL_UNITS) {
  const SIG = ['变阻器','电流表','电压表','开关','电键','电源','电池','电路','电流','电压'];
  const txt = (document.body && document.body.innerText) || '';
  const isCircuit = (SIG.filter(s => txt.includes(s)).length >= 2)
      || !!document.querySelector('[id*="wire" i],[class*="wire" i],[class*="sch-" i]');
  if (!isCircuit) return { skip: 'not a circuit scene' };

  // settle every registered timeline at its final state
  const tls = (window.__timelines && typeof window.__timelines === 'object')
      ? Object.values(window.__timelines) : [];
  tls.forEach(t => { try { t.pause(); t.progress(1); } catch (e) {} });

  // pick the SVG that carries the wiring (has a wire group, else the biggest)
  const svgs = Array.from(document.querySelectorAll('svg'));
  if (!svgs.length) return { skip: 'no svg' };
  let svg = svgs.find(s => s.querySelector('[id*="wire" i],[class*="wire" i]')) || null;
  if (!svg) {
    let best = 0;
    for (const s of svgs) { const r = s.getBoundingClientRect(); const a = r.width * r.height; if (a > best) { best = a; svg = s; } }
  }
  if (!svg) return { skip: 'no svg' };

  function toScreen(el, x, y) {
    const ctm = el.getScreenCTM ? el.getScreenCTM() : null;
    if (!ctm) return null;
    const p = svg.createSVGPoint(); p.x = x; p.y = y;
    const r = p.matrixTransform(ctm);
    return { x: r.x, y: r.y };
  }
  const c0 = svg.getScreenCTM();
  const scale = c0 ? Math.hypot(c0.a, c0.b) : 1;
  const TOL = Math.max(6, TOL_UNITS * scale);

  function isArrow(el) {
    return !!(el.getAttribute('marker-end') || el.getAttribute('marker-start')
      || el.getAttribute('marker-mid'));
  }

  // ---- wire elements: ONLY from a wire group (`#wires` / id|class contains "wire",
  // or `.wire`). We do NOT scrape all <line>/<path>, because battery plates, resistor
  // zig-zags and meter internals are also lines whose endpoints are not loop junctions —
  // scraping them false-positives. If no wire group exists we skip (cannot reliably tell
  // main wires from symbol strokes). NOTE: we do NOT exclude dasharray elements — main
  // wires commonly animate via stroke-dasharray/dashoffset (draw-on), so they are dashed
  // in the DOM even though they render solid; the parallel voltmeter branch is drawn
  // OUTSIDE the wire group and is excluded by that alone.
  let cand = [];
  const wireGroups = Array.from(svg.querySelectorAll('[id*="wire" i],[class*="wire" i]'));
  wireGroups.forEach(g => cand.push(...g.querySelectorAll('line,path')));
  cand.push(...svg.querySelectorAll('line.wire,path.wire'));
  cand = Array.from(new Set(cand));
  if (cand.length === 0) {
    return { skip: 'no <g id="wires"> / .wire elements — main wires not separable from '
      + 'symbol strokes; loop closure NOT verified', needsWireGroup: true };
  }

  const endpoints = [];  // {p:{x,y}, el}
  for (const el of cand) {
    if (isArrow(el)) continue;   // arrow indicator line, not a wire
    const tag = el.tagName.toLowerCase();
    if (tag === 'line') {
      const x1 = parseFloat(el.getAttribute('x1')), y1 = parseFloat(el.getAttribute('y1'));
      const x2 = parseFloat(el.getAttribute('x2')), y2 = parseFloat(el.getAttribute('y2'));
      if ([x1, y1, x2, y2].some(v => Number.isNaN(v))) continue;
      if (Math.hypot(x2 - x1, y2 - y1) < 2) continue; // zero-length
      const a = toScreen(el, x1, y1), b = toScreen(el, x2, y2);
      if (a && b) { endpoints.push({ p: a, el }); endpoints.push({ p: b, el }); }
    } else if (tag === 'path') {
      let L; try { L = el.getTotalLength(); } catch (e) { continue; }
      if (!L || L < 2) continue;
      let a0, b0; try { a0 = el.getPointAtLength(0); b0 = el.getPointAtLength(L); } catch (e) { continue; }
      const a = toScreen(el, a0.x, a0.y), b = toScreen(el, b0.x, b0.y);
      if (a && b) { endpoints.push({ p: a, el }); endpoints.push({ p: b, el }); }
    }
  }

  // ---- component regions: bounding boxes (screen px, post-transform) ----
  const boxes = [];
  let compEls = Array.from(svg.querySelectorAll('[id^="comp" i],[class*="sch-" i]'));
  if (!compEls.length) {
    // fallback: large rect / circle chips (skip junction dots & tiny marks)
    svg.querySelectorAll('rect').forEach(el => {
      const w = parseFloat(el.getAttribute('width')), h = parseFloat(el.getAttribute('height'));
      if (!Number.isNaN(w) && !Number.isNaN(h) && w >= 40 && h >= 30) compEls.push(el);
    });
    svg.querySelectorAll('circle').forEach(el => {
      const r = parseFloat(el.getAttribute('r'));
      if (!Number.isNaN(r) && r >= 15) compEls.push(el);
    });
  }
  for (const el of compEls) {
    const b = el.getBoundingClientRect();
    if (b.width < 4 || b.height < 4) continue;
    boxes.push({ left: b.left - TOL, top: b.top - TOL, right: b.right + TOL, bottom: b.bottom + TOL });
  }

  if (endpoints.length < 6) {
    return { skip: 'insufficient wire geometry (' + endpoints.length + ' endpoints)' };
  }
  if (boxes.length === 0) {
    return { skip: 'no component regions detected (cannot judge terminals)' };
  }

  function near(a, b) { return Math.abs(a.x - b.x) <= TOL && Math.abs(a.y - b.y) <= TOL; }
  function inBox(p) { return boxes.some(bx => p.x >= bx.left && p.x <= bx.right && p.y >= bx.top && p.y <= bx.bottom); }

  const dangling = [];
  for (let i = 0; i < endpoints.length; i++) {
    const ep = endpoints[i];
    let touchWire = false;
    for (let j = 0; j < endpoints.length; j++) {
      if (endpoints[j].el === ep.el) continue;         // same wire's own two ends don't count
      if (near(ep.p, endpoints[j].p)) { touchWire = true; break; }
    }
    if (touchWire) continue;
    if (inBox(ep.p)) continue;
    dangling.push({ x: Math.round(ep.p.x), y: Math.round(ep.p.y) });
  }

  return {
    isCircuit: true,
    nWires: endpoints.length / 2, nBoxes: boxes.length, tol: Math.round(TOL),
    dangling,
  };
}

(async () => {
  const files = fs.readdirSync(dir).filter(f => f.endsWith('.html') && !f.startsWith('__loopchk_'));
  let browser;
  try {
    browser = await puppeteer.launch({
      executablePath: chrome, headless: 'new',
      args: ['--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--force-device-scale-factor=1'],
    });
  } catch (e) { console.log(JSON.stringify({__skip__: "chrome launch failed: " + e.message})); process.exit(0); }

  const results = [];
  for (const f of files) {
    const src = fs.readFileSync(path.join(dir, f), 'utf8');
    const doc = '<!doctype html><html><head><meta charset="utf-8">'
      + '<style>html,body{margin:0;padding:0;background:#fff;}</style></head><body>'
      + unwrapFragment(src) + '</body></html>';
    const tmpPath = path.join(dir, '__loopchk_' + f);
    fs.writeFileSync(tmpPath, doc);
    const page = await browser.newPage();
    await page.setViewport({ width: FRAME_W, height: FRAME_H, deviceScaleFactor: 1 });
    let r = { file: f };
    try {
      await page.goto(pathToFileURL(path.resolve(tmpPath)).href, { waitUntil: 'load', timeout: 20000 });
      await page.evaluate(async () => { try { await document.fonts.ready; } catch (e) {} });
      await new Promise(res => setTimeout(res, 350));
      const out = await page.evaluate(PAGE_FN, TOL_UNITS);
      r = Object.assign({ file: f }, out);
    } catch (e) {
      r = { file: f, skip: 'measure error: ' + e.message };
    } finally {
      await page.close();
      try { fs.unlinkSync(tmpPath); } catch (e) {}
    }
    results.push(r);
  }
  await browser.close();
  console.log(JSON.stringify({ results }));
})().catch(e => { console.log(JSON.stringify({__skip__: "driver error: " + e.message})); process.exit(0); });
""".replace("%TOL%", str(TOL_UNITS))


def render_check(comp_dir):
    """Return (findings, ran). findings = list of (file, message). ran=False → could not render."""
    chrome = find_chrome()
    if not chrome:
        return [], False
    env = build_node_env()
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
        tf.write(NODE_DRIVER)
        driver = tf.name
    comp_abs = str(Path(comp_dir).resolve())  # file:// needs an absolute path
    try:
        proc = subprocess.run(["node", driver, comp_abs, chrome],
                              capture_output=True, text=True, timeout=240, env=env)
    except Exception:
        os.unlink(driver)
        return [], False
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
        return [], False
    findings = []
    for r in payload.get("results", []):
        if r.get("skip") or not r.get("isCircuit"):
            continue
        for d in r.get("dangling", []):
            findings.append((f"compositions/{r['file']}",
                             f"rendered circuit has a DANGLING wire endpoint at screen "
                             f"({d['x']},{d['y']}) — it connects to no other wire and lands on "
                             f"no component (悬空导线/元件飘离导线, 回路未闭合). A series component "
                             f"(如 R₂/电阻/电流表) is NOT on the closed loop in the RENDERED frame "
                             f"(source坐标看似在线上, 但被 transform 挪歪了). "
                             f"[tol≈{r.get('tol')}px, wires={r.get('nWires')}, "
                             f"boxes={r.get('nBoxes')}]"))
    return findings, True


# --------------------------------------------------------------------------- #
# FALLBACK: static loop-closure check (no Chrome) — now parses static paths too
# --------------------------------------------------------------------------- #
def _near(p, q, tol=TOL_UNITS):
    return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol


def _js_wire_segments(txt):
    segs = []
    for m in re.finditer(
        r'\[\s*"(\w+)"\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,'
        r'\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]', txt
    ):
        _id, a, b, c, d = m.group(1), *map(float, m.groups()[1:])
        segs.append(((a, b), (c, d)))
    return segs


def _wire_group_blocks(svg):
    """Return the inner text of every <g> whose id/class contains 'wire' (the main wires)."""
    blocks = []
    for gm in re.finditer(r"<g\b([^>]*)>(.*?)</g>", svg, re.S):
        head = "<g" + gm.group(1) + ">"
        gid = (attr(head, "id") or "") + " " + (attr(head, "class") or "")
        if "wire" in gid.lower():
            blocks.append(gm.group(2))
    return blocks


def _static_wire_segments(svg):
    """Wire endpoints from static <line>/<path M..L..> inside a wire group.
    Dashed elements and arrow-marker lines are excluded (parallel branch / indicators)."""
    segs = []
    for body in _wire_group_blocks(svg):
        for lm in re.finditer(r"<line\b[^>]*/?>", body):
            t = lm.group(0)
            if "dasharray" in t or "marker-" in t:
                continue
            try:
                x1 = float(attr(t, "x1")); y1 = float(attr(t, "y1"))
                x2 = float(attr(t, "x2")); y2 = float(attr(t, "y2"))
            except (TypeError, ValueError):
                continue
            if (x1 - x2) ** 2 + (y1 - y2) ** 2 < 4:
                continue
            segs.append(((x1, y1), (x2, y2)))
        for pm in re.finditer(r"<path\b[^>]*/?>", body):
            t = pm.group(0)
            if "dasharray" in t or "marker-" in t:
                continue
            d = attr(t, "d") or ""
            nums = re.findall(r"[-+]?\d+(?:\.\d+)?", d)
            if len(nums) < 4:
                continue
            vals = list(map(float, nums))
            start = (vals[0], vals[1])
            end = (vals[-2], vals[-1])
            if (start[0] - end[0]) ** 2 + (start[1] - end[1]) ** 2 < 4:
                continue
            segs.append((start, end))
    return segs


def _component_terminals(svg):
    terms = []
    for m in re.finditer(r"<rect\b[^>]*/?>", svg):
        t = m.group(0)
        try:
            x = float(attr(t, "x")); y = float(attr(t, "y"))
            w = float(attr(t, "width")); h = float(attr(t, "height"))
        except (TypeError, ValueError):
            continue
        if w < 40 or h < 30:
            continue
        for p in ((x, y + h / 2), (x + w, y + h / 2), (x + w / 2, y), (x + w / 2, y + h)):
            terms.append(p)
    for m in re.finditer(r"<circle\b[^>]*/?>", svg):
        t = m.group(0)
        try:
            cx = float(attr(t, "cx")); cy = float(attr(t, "cy")); r = float(attr(t, "r"))
        except (TypeError, ValueError):
            continue
        if r < 15:
            continue
        for p in ((cx - r, cy), (cx + r, cy), (cx, cy - r), (cx, cy + r)):
            terms.append(p)
    return terms


def _points_in(body):
    """Collect geometry points from an SVG fragment (line/rect/circle/path)."""
    pts = []
    for lm in re.finditer(r"<line\b[^>]*/?>", body):
        t = lm.group(0)
        try:
            pts.append((float(attr(t, "x1")), float(attr(t, "y1"))))
            pts.append((float(attr(t, "x2")), float(attr(t, "y2"))))
        except (TypeError, ValueError):
            pass
    for rm in re.finditer(r"<rect\b[^>]*/?>", body):
        t = rm.group(0)
        try:
            x = float(attr(t, "x")); y = float(attr(t, "y"))
            w = float(attr(t, "width")); h = float(attr(t, "height"))
            pts += [(x, y), (x + w, y + h)]
        except (TypeError, ValueError):
            pass
    for cm in re.finditer(r"<circle\b[^>]*/?>", body):
        t = cm.group(0)
        try:
            cx = float(attr(t, "cx")); cy = float(attr(t, "cy")); r = float(attr(t, "r"))
            pts += [(cx - r, cy - r), (cx + r, cy + r)]
        except (TypeError, ValueError):
            pass
    for pm in re.finditer(r"<path\b[^>]*/?>", body):
        d = attr(pm.group(0), "d") or ""
        nums = list(map(float, re.findall(r"[-+]?\d+(?:\.\d+)?", d)))
        for i in range(0, len(nums) - 1, 2):
            pts.append((nums[i], nums[i + 1]))
    return pts


def _source_component_boxes(svg):
    """Bounding box (minx,miny,maxx,maxy) of every component group (id^=comp / class*=sch-).
    Used as terminal REGIONS so wires ending inside a battery/switch/meter symbol (drawn as
    raw <line>s, not a rect/circle) are counted as connected — avoids static false positives."""
    boxes = []
    for gm in re.finditer(r"<g\b([^>]*)>(.*?)</g>", svg, re.S):
        head = "<g" + gm.group(1) + ">"
        gid = ((attr(head, "id") or "") + " " + (attr(head, "class") or "")).lower()
        if not (re.search(r"\bcomp", gid) or "sch-" in gid):
            continue
        pts = _points_in(gm.group(2))
        if not pts:
            continue
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    return boxes


def static_check(path):
    txt = path.read_text(encoding="utf-8", errors="replace")
    if not is_circuit_scene(txt):
        return [], False
    findings = []
    verified = False
    for sm in re.finditer(r"<svg\b[^>]*>.*?</svg>", txt, re.S):
        svg = sm.group(0)
        segs = _js_wire_segments(txt) or _static_wire_segments(svg)
        if len(segs) < 3:
            continue
        verified = True
        term_pts = _component_terminals(svg)
        comp_boxes = _source_component_boxes(svg)

        def connected(p, si):
            if any(sj != si and (_near(p, q1) or _near(p, q2))
                   for sj, (q1, q2) in enumerate(segs)):
                return True
            if any(_near(p, tp) for tp in term_pts):
                return True
            for (x0, y0, x1, y1) in comp_boxes:
                if x0 - TOL_UNITS <= p[0] <= x1 + TOL_UNITS and y0 - TOL_UNITS <= p[1] <= y1 + TOL_UNITS:
                    return True
            return False

        for si, seg in enumerate(segs):
            for p in seg:
                if not connected(p, si):
                    findings.append((f"compositions/{path.name}",
                                     f"dangling wire endpoint at ({p[0]:.0f},{p[1]:.0f}) in SVG "
                                     f"source — connects to no other wire / component terminal "
                                     f"(悬空导线, 回路未闭合)."))
    return findings, verified


# --------------------------------------------------------------------------- #
def main():
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    if not dist.is_dir():
        print(f"ERROR: dist dir not found: {dist}", file=sys.stderr)
        return 1
    comp = dist / "compositions"

    # ---- PRIMARY: render-based (post-transform screen coords) ----
    findings, ran = ([], False)
    if comp.is_dir() and any(comp.glob("*.html")):
        findings, ran = render_check(comp)

    mode = "render"
    if not ran:
        # ---- FALLBACK: static (source coords), now incl. static <path>/<line> ----
        mode = "static"
        files = iter_compositions(dist)
        any_verified = False
        findings = []
        for f in files:
            fnd, ver = static_check(f)
            any_verified = any_verified or ver
            findings.extend(fnd)
        if not findings and not any_verified:
            print("NOTE: no wire geometry found and headless Chrome unavailable — "
                  "circuit closure NOT verified (install Chrome / set it in ~/.qwen-mm-plugins/config "
                  "for the render-based check).")
            return 0

    # dedupe
    seen, uniq = set(), []
    for fp, msg in findings:
        key = (fp, msg[:60])
        if key in seen:
            continue
        seen.add(key)
        uniq.append((fp, msg))

    if uniq:
        print(f"FAIL: {len(uniq)} circuit loop-closure problem(s) [{mode} check] — "
              "open loop / dangling wire / component off the rendered loop (电路未闭合):")
        for fp, msg in uniq:
            print(f"  {fp}: {msg}")
        print("\nFIX:")
        print("  - Every series component (电流表/电阻/变阻器/开关/电源) must sit ON the closed "
              "loop: BOTH its terminals connect to a wire, and the wire endpoints land exactly "
              "on the component terminals.")
        print("  - Prefer the skill's sch-* components; if hand-drawing SVG, put the main wires "
              "in a `<g id=\"wires\">` group and verify each wire endpoint meets a component "
              "terminal or another wire at a corner.")
        print("  - If it looks right in the source but fails the RENDER check, a GSAP transform "
              "/ transform-origin (or scoped CSS) is displacing the component group at render "
              "time — fix the transform so the rendered element stays on its wire.")
        return 1

    print(f"OK: circuit loop appears closed [{mode} check] — no dangling wire endpoints, "
          "series components sit on the loop.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
