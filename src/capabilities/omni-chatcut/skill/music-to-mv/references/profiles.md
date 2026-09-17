# Music2MV Profiles

Profiles replace duplicated Skill variants. Select one base profile and record any explicit overrides.

- `standard`: sentence lyrics, visible singing allowed, and burned white Noto subtitles.
- `merged-lyrics`: group dense adjacent lyric cues while retaining original sentence timestamps as provenance.
- `no-asset-qc`: legacy compatibility alias for `standard`; semantic generated-asset review is now
  already off by default.

Profiles do not enable or disable Omni semantic QC. The sole execution switch is
`quality_control.enabled` in the per-run config; it defaults to `false`.

Use [profiles.json](../assets/profiles.json) as the machine-readable policy. Profile overrides never allow
fabricated evidence, invalid timelines, unsafe identities, or generation without the necessary inputs.
