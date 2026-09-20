<!-- Internal workflow: loaded by the root Omni ChatCut Movie Commentary Skill. -->
# Rendering Workflow

Dispatch one movie-commentary executor per frozen shard, with at most three concurrent. Each gets only the
source, one shard JSON, absolute execution facts path, and isolated `out/shard_NN/`. Resume the same agent
when recoverable work exists; the orchestrator cannot replace a failed executor or rewrite narration.

## Executor contract

Synthesize narration first with the configured voice. For Edge TTS `NoAudioReceived`, empty, network, or
undecodable output, retry at most six total attempts with bounded increasing backoff; verify every result
with ffprobe. Never substitute silence, a tone, or another engine. Normalize VO around -19 LUFS.

Derive complete sentence boundaries from real paced audio. Subtitle cues may divide a sentence but never
create visual cuts; Chinese cues target about 18 and never exceed 22 CJK characters. Use exactly the
measured style from execution facts.

For each locator, trim a bounded source window, call `omni_call` for temporal grounding, and map relative
timestamps back to source time. If Omni fails only after complete planner evidence exists, local frame/audio
re-pinning is allowed inside that interval and must be reported as `degraded_local_repin`; missing evidence
blocks the segment.

Use the real source track as a quiet ducked bed. In `highlight_sync`, preserve natural-speed source audio and
mute commentary VO/subtitles/BGM. Bound every audio filter graph and immediately probe every output. Render
one MP4 and one report per shard.

The report schema is `omni-chatcut/movie-commentary-exec-report/v1`; it records status, exact frozen segment
IDs, `understanding_mode`, evidence, external errors, absolute source cuts, sentence/subtitle timing, voice
attempts, audio decisions, shard offsets, measurements, unresolved items, and QA checks.

## Orchestrator completion

Independently decode and inspect every shard, then call `validate_movie_commentary_delivery` with
`require_final=false`. Concatenate matching shards in order with the FFmpeg concat filter and re-encode to
common stream parameters. Write `full/commentary.mp4` and
`full/orchestrator_qa_full.json` (`omni-chatcut/movie-commentary-final-qa/v1`).

Final QA includes full decode, streams and durations, FPS/frame count, shard-duration sum, loudness, true
peak, black ≥0.5s, freeze ≥1s, unexplained silence, seam frames, subtitle placement, and highlight windows.
Every required `checks` value and `overall_pass` must be true before final delivery validation succeeds.
