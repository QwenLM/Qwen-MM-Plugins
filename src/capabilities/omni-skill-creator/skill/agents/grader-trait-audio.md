---
trait: audio
applies_to:
  output_extensions: [".wav", ".mp3", ".flac", ".m4a", ".ogg", ".mid", ".midi", ".rpp", ".als", ".flp"]
  keywords: ["audio", "sound", "mix", "master", "DAW", "reverb", "compressor", "EQ", "sidechain",
             "duck", "tempo", "BPM", "track", "音频", "混音", "母带", "混响", "压缩", "音轨"]
capabilities_used:
  inspect: ["ffprobe", "ffmpeg -af astats/silencedetect/ebur128", "python + wave/numpy"]
  render: []
---

# Trait: audio

Stacks on the base grader. Read it whenever the artifact is meant to be *heard*, or is a
project file describing something to be heard.

## The trap

Assuming that because you cannot listen, you cannot check — and grading the prose about the sound
instead of the sound. Almost everything that matters about audio is measurable without ears, so an
unheard artifact is not an unverifiable one.

## The evidence ladder

1. **Container facts — `ffprobe`.** Duration, sample rate, channel count, codec, bit depth. Cheap
   and exact. Catches the most common real failures outright: a file that is 0.2 s long, mono when
   stereo was asked for, 8 kHz when 44.1 was specified.
2. **Signal statistics — `ffmpeg -af astats` / `ebur128` / `silencedetect`.** Peak and RMS level,
   DC offset, integrated loudness (LUFS), true peak, and where the silence is. This is how you
   check "it is not silent", "it does not clip", "it sits near -14 LUFS", "the tail is not cut".
3. **Structure over time.** Compute RMS per window with `wave` + `numpy` and you can check the
   shape of things: that a ducking/sidechain effect actually lowers one track while another plays,
   that a fade exists, that a section repeats, that tempo matches a stated BPM.
4. **Project files are text.** A REAPER `.rpp` is plain text — tracks, FX chains, parameter values,
   item positions are all readable and assertable. A `.mid` parses with any MIDI library: note
   numbers, velocities, timing. When the taught workflow is "set up this chain", the project file
   is *stronger* evidence than a rendered audio file, because it names the technique.
5. **Perceptual quality** — timbre, whether a mix "sounds warm". Usually the rung nothing here can
   reach, so this is the one that most often lands on NOT_VERIFIABLE. Say that you could not reach it
   and what you tried; if the environment does offer something that judges sound perceptually, use it
   rather than assuming the rung is closed.

## What a bare model already knows

A capable model knows the vocabulary (compressor, sidechain, LUFS targets, GM drum note numbers)
and will produce plausible prose about it unprompted. So terminology assertions do not discriminate,
and nothing in the run will expose that — such an assertion passes and looks earned. What a bare model
misses is the presenter's specific chain, specific values, specific ordering, and the reason behind
them — plus, when a real artifact is produced, whether the numbers come out right. Grade against
those, and say so in `eval_feedback` when an assertion only reaches the vocabulary.

## Applying it

Before concluding you cannot check the sound, run one probe rather than assuming: media handling
generally brings a probe tool with it, so "no audio tooling here" is a claim worth testing before
you rely on it. Reach for rung 5 only after rungs 1-4 are exhausted, and record which rung each
verdict came from.
