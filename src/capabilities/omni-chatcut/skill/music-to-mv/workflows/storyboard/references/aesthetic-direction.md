# Promptable Global Visual System

Put the reusable visual decisions in `style_bible` so image and video prompts can consume them directly:

- `overall_visual_style`: concrete medium, era, production treatment, and image character
- `color_palette`: base palette plus section-linked changes
- `film_look`: lens, depth of field, exposure, grain/render, and camera texture
- `mood`: visible atmosphere, not an abstract theme explanation
- `composition_grammar`: framing, negative space, layering, obstruction, horizon, and subject hierarchy
- `camera_grammar`: camera height, focal-length family, still/moving behavior, and permitted exceptions
- `lighting_grammar`: source, direction, contrast, exposure, and section changes
- `material_language`: skin, fabric, surfaces, atmosphere, line, paint, or render materials
- `production_design`: architecture, object density, wear, and spatial hierarchy
- `wardrobe_rules`: silhouette, palette, materials, and allowed changes
- `recurring_elements`: only elements assigned to exact shots, including how their visible state changes
- `prohibited_elements`: unwanted styles, text, logos, subtitles, watermarks, and generic effects

Avoid empty terms such as “cinematic,” “beautiful,” or “high quality” unless followed by concrete image behavior. Copy the global system into scene, cast, and shot prompts where relevant; do not create a separate aesthetic scorecard.

Medium-specific details remain technical prompt content: physical credibility for live action, stable line/model rules for 2D, coherent materials/rig behavior for 3D, and explicit compositing boundaries for mixed media.
