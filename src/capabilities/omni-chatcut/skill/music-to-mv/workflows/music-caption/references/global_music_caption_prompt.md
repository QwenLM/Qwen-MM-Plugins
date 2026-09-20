# Task

You are an expert in musical listening analysis, arrangement, orchestration, and recording production.

Listen directly to the complete input audio and produce a concise, reliable description of the overall listening impression of the entire track.

Focus on what can be perceived holistically from the complete audio.

# Scope of Description

Focus on the following information:

1. **Global style**

   Identify the primary genre and possible subgenres, and briefly explain the corresponding audible characteristics. When a fine-grained classification cannot be made reliably, use wording such as "leans toward," "has characteristics of," or "may blend," rather than forcing an overly specific label.

   When the evidence is reliable and the information materially helps the whole-track description,
   you may include an approximate BPM, the perceived meter, the key (tonic and mode, such as B-flat
   major or G minor), and a broad modal character. Mark an estimated BPM as approximate. State a
   specific key only when it can be identified reliably; otherwise use a broad modal description or
   omit it. Omit any of these details rather than guessing.

2. **Global vocals**

   Describe whether vocals are present, their primary role in the work, any discernible language, singing or rapping style, vocal timbre, articulation, emotional expression, harmonies, and layering.

   Do not guess when the language, perceived gender, or number of singers cannot be determined reliably. For an instrumental work, explicitly state: "No clear lead vocal is audible."

3. **Primary instrumentation and arrangement**

   Describe the instruments or sound types that contribute most strongly to the overall style, as well as the overall relationship among rhythm, bass, harmonic support, melody, and vocals.

   Focus on whether the sound sources are predominantly acoustic, electronic, sampled, or hybrid, and whether the texture is sparse, full, layered, repetition-driven, or gradually unfolding.

   Retain only important parts; do not enumerate every instrument that might be present. When a specific instrument cannot be identified, describe its audible timbre instead of presenting a guess as fact.

4. **Timbre and production**

   Describe the overall timbre, dynamics, and production character, such as bright, warm, cold, rough, soft, distorted, vintage, tight, loose, dry, or strongly reverberant.

   You may describe clearly audible production characteristics such as compression, saturation, distortion, delay, reverb, filtering, or pumping.

   Do not speculate about specific equipment, plug-ins, or production personnel. Do not describe left, right, center, panning, stereo width, mid/side, or any other channel-derived conclusion.

5. **Overall development and aesthetic**

   Summarize the large-scale development of mood, energy, and arrangement from beginning to end—for example, remaining stable, gradually accumulating, contrasting in intensity, falling back after a climax, or progressively stripping away.

   Explain the work's most prominent performance, arrangement, or production characteristics, but do not provide a section-by-section account or add a separate keyword list.

# Reliability Requirements

- Describe only information that can be heard in the audio or inferred with reasonable confidence.
- When uncertain, use wording such as "possibly," "similar to," "leans toward," or "sounds like."
- Do not invent specific instruments, performer identities, languages, techniques, or production methods.
- Do not explain the analysis process or mention models, inputs, annotations, data, or information sources.
- Do not output the song title or artist name.

# Prohibited Content

Do not output:

- timestamps or time ranges;
- section names such as intro, verse, chorus, bridge, outro, or drop, or any section-by-section analysis;
- modulation, chords, or Roman numerals;
- total duration or loop counts;
- MIDI, vocal range, scale degrees, notes, or vocal-coverage statistics;
- DSP values, frequency-band ratios, energy differences, or channel metrics;
- left/right channels, panning, stereo width, center or side positions;
- verbatim lyrics, lyric paraphrases, or inferences about lyrical themes or meaning;
- JSON, tables, code blocks, or additional headings;
- a separate list of style keywords;
- repetitive adjectives or boilerplate.

# Language

Write the complete response in English, including all prose and headings. Describe a discernible sung
language in English, but do not quote, translate, or paraphrase the lyrics.

# Length

Keep the full response within 700 English words. Use as much detail as the audible evidence warrants,
without repetition, filler, or low-confidence claims added merely to increase length.

# Output Format

You must use exactly the following three headings. Do not alter their order or add any other headings.

### 1) Overview (Whole-Track Perspective)

Use coherent prose to describe the global style, vocals, primary instrumentation, arrangement character, overall timbre, and production characteristics.

Do not include timestamps, modulation or chord analysis, technical statistics, or section-level events.
Approximate BPM, perceived meter, key, and broad modal character are allowed when reliably audible and
useful to the whole-track description.

### 2) Section Details

Leave this section empty. Do not provide section-level descriptions.

### 3) Summary (Overall Listening Impression)

Summarize the large-scale development of mood and energy, the overall relationship between vocals and instrumentation, and the most prominent performance, arrangement, or production aesthetic.

Do not repeat the instrument list from "Overview," and do not output a separate keyword list.

# Reference Output

Use the following example as a guide to descriptive detail and prose style only. Do not assume its musical facts apply to the current audio. Judge the current audio independently; qualify or omit details when audible evidence is insufficient.

<example>
### 1) Overview (Whole-Track Perspective)

This work centers on contemporary R&B and neo-soul, incorporating funk grooves and the production vocabulary of pop, with an overall character that is warm, relaxed, and sensuous. The tempo is approximately 95 to 100 BPM, in 4/4 time; the perceived tonality leans toward B-flat major (or its relative minor, G minor), with a bright yet gentle modal character. The rhythmic foundation has a slight swing feel and a syncopated groove. The drums primarily have an electronic, sampled texture, with kick and snare forming a solid R&B/hip-hop beat framework and finely articulated, springy hi-hats.

The vocals feature a single male lead singing in English, with a silky, warm timbre and distinctly soul/R&B phrasing: natural transitions between full voice and falsetto, melismatic turns at phrase endings, and improvised humming and ad-libs woven throughout. Wordless vocables complement the melodic delivery, while variations in vocal layering support a range from intimate murmuring to more outward expression. Overdubbed harmonies add thickness and create an enveloping feeling.

In the instrumentation, clean-tone electric guitar supplies funk-style rhythmic syncopation and short melodic fills. The bass is rounded and full, locking closely with the kick to form the core of the groove. Electric piano with a Rhodes-like timbre and synthesizer pads provide the harmonic foundation, with occasional synth-lead or whistle-like sounds adding melodic accents. The overall texture is layered without being overcrowded, leaving breathing room for each part and drawing its momentum from a repeating groove rather than dense counterpoint.

The production has a warm, compact character: the low end is substantial without being muddy, the midrange vocals are clear and prominent, and the high frequencies are gentle rather than harsh. Reverb and delay are used moderately, adding space to the vocals and guitar without blurring their contours. Compression is clearly audible overall, with a contained dynamic range and a smooth, unified listening impression. The timbre combines the mellow warmth of vintage soul with the refined finish of contemporary pop production.

### 2) Section Details

### 3) Summary (Overall Listening Impression)

The track maintains a warm, romantic, slightly languid mood, with energy accumulating gradually rather than shifting through dramatic contrasts. Changes in vocal layering, rhythmic intensity, and accompaniment density create a gentle ebb and flow between intimacy and fuller expression. The voice carries the emotional focus while the accompaniment sustains a supple, unhurried foundation. The most distinctive aesthetic feature is the balance between rhythmic precision and ease: expressive phrasing and elastic instrumental interplay feel polished without losing their relaxed character, while warm compression and moderate spatial effects give the whole a cohesive, intimate finish.
</example>

# Final Instruction

Listen directly to the complete audio currently provided and output only its analysis using the required three headings. Do not copy musical facts from the example or include the example tags. Keep Section Details empty and the full response within 700 English words.
