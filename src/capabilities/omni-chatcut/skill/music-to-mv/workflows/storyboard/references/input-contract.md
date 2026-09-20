# Rich Music-Analysis Input Contract

The preferred upstream Markdown contains these sections:

1. `Whole-Track Overview`
2. `Structure Timeline`
3. `Lyrics Timeline`
4. `Overall Summary`

## Structure Timeline

Each section uses an exact timestamp, label, and section-level caption:

```text
### [00:00:19,840 --> 00:00:37,640] [verse]
...
```

Preserve the original section boundaries even when lyric boundaries differ.
Preserve the complete section caption verbatim in every overlapping visual unit's
`section_caption_evidence`; keep the separately authored `design_application` outside that source text.
The caption body may span multiple lines and paragraphs. Read the entire body up to the next
structure heading or the end of Structure Timeline, preserving internal line breaks and paragraph
boundaries. Normalization may trim surrounding whitespace and remove a legacy leading `Caption:`
marker; it must not retain only the first line or summarize the body.

Read the complete Whole-Track Overview and Overall Summary as global context. Richer descriptions
do not provide finer timing: an untimed within-section development must not become an asserted
musical event at an invented timestamp.

## Lyrics Timeline

Each lyric item contains an index, timestamp line, and lyric text. `[Instrumental]` is valid and should produce empty lyric text in storyboard audio-sync fields.

## Timing Precedence

- Track duration comes from the latest structure or lyric end.
- Section labels determine macro narrative turns.
- Lyric phrases determine performance and semantic windows.

## Independent Predictions

Structure and lyrics may come from independent predictors and need not align exactly. Do not silently move either timeline. Preserve the exact lyric text that overlaps a resulting shot.
