# Asset sources

Every file in this directory is a short, license-permissive sample trimmed down to the size the
`qwen-mm-plugins-api` Omni tools and the MCP endpoint tolerate (see `usage.md` → Video delivery and
OSS). None of the files are generated or synthesized — each is a real excerpt of a real recording.

| File | Length | Role | Source |
|------|--------|------|--------|
| `guess_age_gender.wav` | 9 s | `omni_asr` / `omni_asr_timestamped` | Official Qwen2.5-Omni / Qwen2-Audio demo audio (`guess_age_gender.wav`), distributed with the Qwen2.5-Omni repo under Apache-2.0. |
| `interview_clip.wav` | 35 s | `omni_multi_speaker_asr` | Interview excerpt (a researcher describing a rat harm-aversion study, interviewer + interviewee), trimmed from the public Qwen demo interview audio. |
| `music_40s.wav` | 27 s | `omni_music_caption` | Singer-songwriter / acoustic-folk excerpt with piano, xylophone and a female voice, trimmed from a public demo music track. |
| `draw1_clip.mp4` | 15 s | `omni_av_caption` | Screen recording of a tablet drawing app showing a ukulele being drawn, trimmed + re-encoded (H.264, 448p-class) from the full draw1 screen recording. |
| `basketball_clip.mp4` | 20 s | `omni_av_grounding` / `omni_av_counting` | Basketball gameplay clip with one made basket, trimmed from `basketball_30s.mp4` (Pexels sports footage) and re-encoded. |

`case-*.png` are key-frame montages (6 evenly sampled frames) generated with ffmpeg for the two
video cases in `usage.md`; audio cases have no preview image.

- Audio is mono WAV (PCM): `guess_age_gender.wav` at 48 kHz, the other two at 16 kHz — all small
  enough to be sent unchanged by the tools' inline delivery (no downmix needed).
- All video is H.264 + AAC in MP4, well under the 10 MB inline limit.
