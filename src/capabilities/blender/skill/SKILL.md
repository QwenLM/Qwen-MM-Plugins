---
name: qwen-mm-plugins-blender
description: Use whenever a task involves building or editing a 3D scene or asset in Blender — modeling, characters/people, architecture/interiors, terrain/landscapes, props, materials, lighting, or rendering. Covers discovering installed add-ons, using generators, importing and REFINING ready-made assets, and matching the result to the spec. Requires a running Blender instance with the blender-mcp addon (see Prerequisite).
---

# Blender 3D Creation

You build 3D content in Blender by writing Python (via the `execute_blender_code` tool) against a **running** Blender instance. Quality bar: the result must actually match the request — not a rough pile of primitives, and not a bare unedited import.

## Prerequisite: a live Blender + addon (started for you on first use)

These tools are a **thin client**: they run Python against a **running** Blender carrying the
blender-mcp addon (bundled in this plugin), and do **not** launch Blender while serving. Normally you
start nothing — just call a tool.

- **Default (plugin install):** `QWEN_MM_AUTOLAUNCH=1` is preset, so the **first tool call** brings
  Blender up itself: it auto-installs the pinned Blender 4.2.x if missing (Linux-x86_64, rootless,
  ~300 MB one-time) and starts it with the bundled addon on `$BLENDER_HOST:$BLENDER_PORT` (default
  `localhost:9876`). Just call a tool such as `get_scene_info`; the **first** call may take a minute
  or two while it downloads (expected), later calls are instant.
- **Don't shell out to `qwen-mm-plugins-blender --launch-app` under a plugin install** — that console
  entry lives inside the uvx environment, not your shell PATH (`command not found`). Use it only from
  a source checkout, or for a manual / GUI start:
  ```bash
  python3 src/capabilities/blender/qwen_mm_plugins_blender --launch-app        # headless (xvfb)
  python3 src/capabilities/blender/qwen_mm_plugins_blender --launch-app --gui  # real display
  ```

Auto-launch can't cover two things: (1) auto-download is **Linux-x86_64 only** — elsewhere install
Blender yourself (`apt install blender` | `brew install --cask blender`); (2) a headless box needs a
virtual display (`apt install xvfb`, needs root). If a tool reports it can't connect, it's almost
always one of these — the error message spells out which; from a checkout, `... --check-system` lists
every missing system tool.

## Core workflow: build → REFINE → verify (never skip refine)

Generating or importing something is only step 1. A bare import, or a loose pile of assets, is NOT an acceptable final result. Always:
1. **Decompose the request** into concrete objects, real-world sizes, layout, materials, and lighting.
2. **Get base geometry** — prefer a generator or a ready-made asset *when one genuinely fits*. If nothing fits well, build it from scratch (primitives + modifiers + bmesh / geometry nodes); do NOT force an ill-fitting asset just to avoid modeling.
3. **Refine deeply to spec** — transforms (scale/position/orientation) are the bare minimum, not the goal. A good result usually needs substantial work on top of the base: correct proportions and real-world dimensions; edit/add/remove geometry and fix topology; model the details the request implies; combine/kitbash parts from several sources; author or tune materials and shading (not the defaults); set up lighting; and make objects relate correctly to one another (contact, alignment, consistent scale). Keep going until the object truly looks like what was asked.
4. **Verify and iterate** — render (`bpy.ops.render.render`) or capture `get_viewport_screenshot`, compare against the request, and fix the gaps. Repeat until it genuinely matches. Always call `get_scene_info` after a task to confirm the changes landed.

## Discover what's installed (do this before assuming)

Add-on sets differ per machine — introspect the running Blender instead of guessing:
```python
import bpy
print([a.module for a in bpy.context.preferences.addons])           # enabled add-ons
print([x for x in dir(bpy.ops.mesh) if not x.startswith("__")])     # available mesh operators
```

## Using add-ons

- **Operator-based add-ons** (Archimesh, A.N.T. Landscape, Tissue, and most add-ons): call the operator directly — operator names (`bl_idname`) are global and do NOT depend on how the add-on was installed:
  - Architecture (rooms/doors/windows/stairs/kitchen): `bpy.ops.mesh.archimesh_room()`, `.archimesh_door()`, `.archimesh_window()`, `.archimesh_stairs()`, `.archimesh_kitchen()`
  - Terrain/landscape: `bpy.ops.mesh.landscape_add(...)` — if it raises a poll() error, wrap the call in `bpy.context.temp_override(...)` with a VIEW_3D area + WINDOW region; set smoothing via `mesh.polygons[i].use_smooth=True` (not `shade_smooth()`).
  - Repeating patterns (brick walls, panels, honeycomb): select a base object + a component object, then `bpy.ops.object.tissue_tessellate()`.

- **Python-API add-ons installed as extensions** (Blender 4.2+): the module lives under the `bl_ext.user_default.<id>` namespace, NOT the bare name. For MPFB (human generator):
  ```python
  from bl_ext.user_default.mpfb.services.humanservice import HumanService
  human = HumanService.create_human()
  ```
  Portable form (works whether it's a legacy add-on or an extension):
  ```python
  import importlib, bpy
  mod = next((m for m in bpy.context.preferences.addons.keys()
              if m.split('.')[-1] == 'mpfb'), 'mpfb')
  HumanService = importlib.import_module(mod + '.services.humanservice').HumanService
  human = HumanService.create_human()
  ```
  `create_human()` returns the body mesh only. Apply skin/eyes/teeth (MPFB's MaterialService / the installed system-assets pack) so it isn't a gray mannequin, then adjust proportions/morphs toward the target.

## Ready-made assets (download at runtime — then refine)

- Furniture & finished models: `search_sketchfab_models` → `download_sketchfab_model` (check the preview with `get_sketchfab_model_preview` first).
- Materials / HDRIs / props: `search_polyhaven_assets` → `download_polyhaven_asset`; apply a texture with `set_texture`.
- Generate a custom single item: `generate_hyper3d_model_via_text` / `..._via_images` → `poll_rodin_job_status` → `import_generated_asset` (or the Hunyuan3D equivalents). Generators are for a single item — don't generate a whole scene or the ground in one shot.

An imported/generated asset is a starting point, not the deliverable. Normalize scale and placement, then refine it to the request: adjust proportions/geometry, fix or replace materials, add missing detail, and combine with other parts. After importing, always check the `world_bounding_box` and adjust location/scale/rotation so objects sit correctly and don't clip. If the closest asset is still a poor match, discard it and build from scratch.

## Nature without add-ons
- Scatter (grass/rocks/trees): Geometry Nodes "Distribute Points on Faces" + "Instance on Points".
- Sky/sun lighting: a world Sky Texture node with `sky_type='NISHITA'`.

## Reminder
Use generators and ready-made assets when they genuinely fit — but acquiring one is the start, not the finish, and forcing a poor-fit asset is worse than modeling from scratch. The deliverable is a scene/asset that truly matches the request, which usually takes real modeling, material, and lighting work well beyond simple transforms.
