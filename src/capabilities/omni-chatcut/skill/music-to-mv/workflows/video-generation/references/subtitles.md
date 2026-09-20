# Lyrics Subtitle Burn-In

Subtitle rendering is a deterministic post-assembly step. It never asks a model to rewrite,
translate, merge, fill, or retime lyrics.

When `subtitles.enabled=true`, resolve the accepted sentence-level lyrics in this order:

1. `--lyrics-srt`
2. `subtitles.source_srt`
3. `music2mv-manifest.json` artifact `lyrics_srt`
4. `analysis/music-caption/lyrics.srt`
5. `subtitles.source_evidence`
6. the manifest `music_caption_evidence` artifact or `analysis/music-caption/evidence.json`

Evidence-backed rendering materializes `sentence_lyrics.cues` verbatim as
`<execution>/subtitles/lyrics.srt`. A validated `sentence_lyrics.status=no_lyrics` skips burn-in and
records that reason. Missing or malformed lyrics block assembly when subtitles are enabled; they are
not treated as an instrumental result. Set `subtitles.enabled=false` only as an explicit project
override.

After the edit and untouched soundtrack are assembled into
`video_segments/assembled_with_audio.mp4`, run `scripts/burn_lyrics_subtitles.py`. It strictly
validates monotonic, non-overlapping SRT cues within the video duration and burns them with the
bundled OFL-licensed Noto Sans CJK SC Bold font:

- solid white text with no outline, shadow, box, glow, or blur
- bottom-center placement inside a resolution-scaled safe margin
- fixed position during each cue
- subtle 120 ms fade-in and 180 ms fade-out

The renderer preserves resolution, duration, frame rate, and the assembled audio track, writes
`subtitles/lyrics.ass`, and fully decodes the result. `final_mv.mp4` is the burned deliverable;
`assembled_with_audio.mp4` remains the clean master. Technical verification records subtitle status,
cue count, SRT/ASS paths, and the render report, but visual readability still requires frame evidence
when a reviewer requests it.
