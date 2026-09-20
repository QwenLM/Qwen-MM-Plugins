# Music Caption Evidence Contract

Normalize all successful branches into exactly one JSON object with
`schema="music-caption-evidence"`. The final synthesis step must consume this object
alone. Structure and sentence lyrics are independent timelines; never force one timeline inside
another.

## Canonical shape

```json
{
  "schema": "music-caption-evidence",
  "audio": {
    "duration_sec": 182.417
  },
  "structure": {
    "timestamp_format": "HH:MM:SS,mmm",
    "anomalies": [
      {
        "type": "short_segment_merged",
        "original_index": 0,
        "original_label": "intro",
        "original_duration_sec": 0.52,
        "merged_into_label": "verse",
        "merge_direction": "next"
      }
    ],
    "segments": [
      {
        "index": 0,
        "start_timestamp": "00:00:00,000",
        "end_timestamp": "00:00:15,020",
        "start_sec": 0.0,
        "end_sec": 15.02,
        "duration_sec": 15.02,
        "label": "intro",
        "structure_asr_lyrics": ""
      }
    ]
  },
  "sentence_lyrics": {
    "status": "ok",
    "cues": [
      {
        "index": 1,
        "start_timestamp": "00:00:05,120",
        "end_timestamp": "00:00:08,460",
        "start_sec": 5.12,
        "end_sec": 8.46,
        "text_lines": ["When you walk through a storm,"]
      }
    ]
  },
  "global_caption": "Complete unmodified global-caption text.",
  "segment_annotations": [
    {
      "segment_index": 0,
      "start_timestamp": "00:00:00,000",
      "end_timestamp": "00:00:15,020",
      "label": "intro",
      "annotation": "Complete unmodified per-clip annotation text."
    }
  ]
}
```

For a no-lyrics result, use `{"status":"no_lyrics","cues":[]}` as the complete
`sentence_lyrics` value.

## Timeline independence

- `structure.segments` is the authoritative structure and clip-analysis timeline.
- `sentence_lyrics.cues` is an independent verbatim transcription timeline.
- Do not add `assigned_segment_index`, split cues at structure boundaries, move cues between
  sections, alter structure boundaries from lyric timing, or imply exact agreement between branches.
- Time overlap may be discussed only as an explicitly qualified observation during synthesis. It
  must never become a normalized fact or mutate any source timeline.

## Field rules

### `schema`

Require the exact string `music-caption-evidence`.

### `audio`

Copy positive `duration_sec` from the slicing manifest after probing the physical audio. Do not
include source paths, filenames, credentials, model settings, or retry metadata.

### `structure`

- Copy the corrected slicing-manifest timeline, including any deterministic syntax normalization or
  final-end clamp.
- Copy `structure_anomalies` to `structure.anomalies`; use an empty list when no anomaly was recorded.
- Allow `timestamp_format` only as `MM:SS,mmm` or `HH:MM:SS,mmm`.
- Number indices from zero; preserve order, continuity, labels, and durations.
- Rename manifest `lyrics` to `structure_asr_lyrics` and preserve it only as secondary diagnostic
  evidence. It is not the sentence-lyrics source.
- Exclude clip paths and diagnostic fields such as `original_end_timestamp`.

### `sentence_lyrics`

- Allow `status` only as `ok` or `no_lyrics`.
- For `ok`, parse every validated SRT cue and preserve its one or two `text_lines` verbatim.
- Number indices from one and preserve chronological order.
- Copy timestamps after the permitted final-end clamp and derive matching numeric seconds.
- Do not add any structure relationship or inferred label.
- For `no_lyrics`, require `cues=[]`.

### `global_caption`

Copy the complete non-empty global-caption response without pre-summarizing it.

### `segment_annotations`

- Create exactly one entry for every structure segment and join only by `segment_index`.
- Copy timestamps and labels from normalized structure rather than model prose.
- Preserve the complete non-empty per-clip annotation.
- Exclude clip paths, API metadata, usage data, and request settings.

## Assembly and validation

1. Build `audio` and `structure` from the corrected slicing manifest.
2. Parse the independently validated SRT into `sentence_lyrics` without structure assignment.
3. Copy the complete global caption.
4. Join exactly one complete clip annotation to each structure segment by index.
5. Reject rather than synthesize if a required or known-only top-level key is wrong; a timestamp
   exceeds `audio.duration_sec`; either timeline is malformed or reordered; a structure annotation
   is missing or mismatched; or the global caption is empty.

Keep raw responses and diagnostics outside this object. Do not place summaries, inferred alignment,
output prose, Markdown, or explanatory notes inside it.
