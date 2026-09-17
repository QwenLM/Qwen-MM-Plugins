# Task

Listen to the separated `no_vocals` audio and decide whether it contains background audio worth preserving
in the translated video. FFmpeg has already established that the stem is not effectively silent.

# Decision policy

Choose `include` when at least one of these is clearly audible:

- background music or a musical bed;
- intentional ambience that materially establishes the scene;
- sound effects or other non-speech program audio that would be noticeably lost.

Choose `omit` when the stem contains only silence, negligible noise, isolated clicks, codec artifacts,
Demucs separation residue, or faint leaked speech. Residual source speech is not background content and must
not cause `include`.

When evidence is genuinely ambiguous, choose `include`; losing meaningful program audio is worse than
retaining a quiet uncertain bed. Base the decision only on the supplied audio and FFmpeg evidence.

# Output

Return only one JSON object with exactly this shape:

```json
{
  "background_mode": "include",
  "confidence": "high",
  "reason": "one concise decision reason"
}
```

`background_mode` must be `include` or `omit`; `confidence` must be `high`, `medium`, or `low`. Do not add
Markdown, commentary, timestamps, identities, transcription, or inferred source context.
