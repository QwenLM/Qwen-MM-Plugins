# Reusable Cast Assets and Scene Definitions

Create these formal definitions only after the visual/edit outline exists. The outline uses provisional functional roles and environment needs; this stage converts only the required placeholders into stable IDs and generation inputs. Every cast member and scene must cite at least one outline row that uses it.

## Cast

Create only identities required by the outline and used in the shot plan. Each cast entry defines:

- stable `id`, `role`, and visible `identity`
- appearance details that must remain consistent
- one `portrait_t2i_prompt` for identity-reference generation
- eligibility/safety metadata required by the selected execution route

Do not create per-wardrobe portraits or per-shot human keyframes. Wardrobe belongs to scenes and is repeated exactly in shot prompts.

## Scenes

Create only environments required by the outline and used by shots. Each scene defines:

- `id`, `name`, `setting`, `lighting`, `palette`, and material details
- usable layout or blocking zones when spatial continuity matters
- wardrobe by cast ID

Write enough concrete spatial and material information for each shot prompt to reconstruct the required environment. Recurring scenes may preserve named landmarks, screen direction, entrances, exits, and blocking zones through text continuity, while composition, depth, weather, activity, and local set dressing may vary by shot. Only cast identities become generated image assets.
