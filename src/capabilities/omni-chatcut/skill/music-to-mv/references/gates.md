# Music2MV Gates

1. **Input:** at least one usable source audio, completed caption/map, validated storyboard, or resume state.
2. **Caption:** all profile-required evidence exists; absent optional QC is explicitly recorded.
3. **Authoring:** the storyboard validator reports valid; required creative/continuity reports exist.
4. **Execution:** media form, identities, duration, audio, assembly mode, credentials, and assets are compatible.
5. **Completion:** all requested segments are present, configured semantic-QC decisions are complete, assembly succeeds, and technical QC passes.

Failed validation blocks downstream work. When semantic QC is enabled, a rejected segment is
regenerated within the configured bound or falls back to the best retained candidate. Structural
problems return to storyboard authoring. Technical QC and optional semantic review are separate.
