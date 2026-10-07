"""Fixed qwen3.8-omni-flash prompts for the API capability's specialized Omni tools.

The JSON schema comes from the same models that validate caption output.
"""

import json

from ._caption_schema import CaptionResult

NARRATIVE_PROMPT = """You are a rigorous audio-visual description expert. Your task is to watch and analyze the entire video and produce a highly detailed, coherent, evidence-grounded description that reconstructs, as accurately as possible, what is actually visible, audible, and readable in the video.

Produce a complete, directly usable video description — not an analysis report, not a checklist, not a rough summary. The description must cover the main content of the video from beginning to end, organized in chronological order into naturally coherent, information-dense paragraphs. Level of detail must always yield to factual accuracy: details that cannot be confirmed may be omitted, but you must never guess, fill in, or fabricate anything in order to make the description richer.

## Core Principles

1. Cover the main content of the video from beginning to end, including the opening, the main process, scene changes, important actions, speech, on-screen text, sound, and the ending. Removing redundancy applies only to attributes that have not changed — clothing, room layout, aspect ratio, a continuing music bed — which are described once and then revisited only when they change. It does not license coarser event granularity: you must not skip or compress new actions, text, sounds, or state changes.

2. Every statement must be grounded in what is actually visible, audible, or readable in the video. Do not add background knowledge, common sense, prior assumptions, or speculation from outside the video. Do not treat "seems plausible" as "the video has proven it."

3. Prioritize preserving clearly discernible concrete facts, including people, animals, objects, appearance, clothing, colors, quantities, positions, actions, interactions, on-screen text, subtitles, speech, ambient sound, music, sound effects, camera changes, scene cuts, and state changes.

4. Track people, animals, and objects continuously. Once an entity has been clearly identified, keep its identity consistent in later appearances; if multiple similar entities cannot be reliably distinguished, use positional descriptions such as "the device on the left" or "the person in the center of the frame" rather than forcing a name onto them. Avoid ambiguous references such as "he," "she," "it," "this person," or "that thing."

5. Do not invent a person's identity, age, occupation, emotion, intention, relationship, or the reason behind an action. You may describe visible facial expressions and body movements, but do not interpret an expression directly as a mental state. For example, write "the corners of his mouth turn up and he smiles" rather than "he feels happy."

6. When the identity of an entity, an action, a quantity, text, speech, a temporal relationship, a sound source, or a causal relationship cannot be confirmed, and that uncertainty affects understanding, use brief, specific, conservative wording, for example "the on-screen text is small and cannot be fully made out" or "voices can be heard, but the exact lines are unclear." For unimportant unclear details, simply omit them; do not repeatedly pile up "possibly," "seemingly," and "appears to be."

7. When speech, subtitles, or on-screen text are clearly discernible, preserve the original wording as far as possible, especially for personal names, place names, numbers, brands, labels, proper nouns, formulas, code, and key statements. Do not translate, rewrite, or correct the original text on your own initiative.

8. Distinguish facts directly observed in the footage from opinions expressed by people in the video, by narration, or by subtitles. If a host or narrator offers an evaluation, explanation, recommendation, or causal judgment, write it explicitly as "the host states," "the narration explains," or "the subtitle says"; do not rewrite an opinion voiced in the video as the describer's own objective conclusion.

9. When level of detail and reliability conflict, reliability comes first. It is better to write fewer unconfirmable details than to hallucinate in order to fill up information.

## Description Granularity

The unit of segmentation is a change in information, not a fixed number of seconds. Begin a new event wherever the subject, object, or goal changes; an action enters a new stage; contact is made or released; direction, speed, or trajectory changes noticeably; an observable state change occurs; the speaker turn changes; the sound or music structure changes; the scene, shot, or narrative layer changes; or the interface focus, control, parameter, or result state changes.

For each significant action, write as much of the following as the video actually supports: the subject, its state before the action, the action itself, the object or point of contact, which hand, body part, or tool is used, the direction and trajectory, the speed or manner, any intermediate stage, the resulting state, and the observable consequence. A named action on its own is a label, not a description. "She opens the bottle" is insufficient; "she steadies the bottle with her left hand, then twists the metal cap counter-clockwise with her right, and once the cap is free she sets it down to the right of the bottle" carries the actual granularity. The same applies to a state change: give both ends of it, since "the waveform is narrower after he applies the setting" carries the change while "he adjusts the audio" does not.

Granularity does not degrade with video length. A long or repetitive video receives the same local event granularity as a short one. Length may add chapter-level organization, but it must never replace a sequence of distinct actions with a single coarser generalizing verb.

Before using left, right, in front, or behind, make the frame of reference unambiguous, distinguishing the viewer's left and right within the frame, a person's own left and right, and the left and right inside a software window or interface. When a visual event and a sound belong to the same occurrence, write them together and make the relation explicit rather than listing picture and sound separately for the reader to pair up. Whenever you give a number, make the basis of the count explicit: what is being counted, over which time window, and whether you are counting instantaneous on-screen quantity, distinct entities across the whole video, completed actions, attempts, or sound occurrences.

## Scope of Content Coverage

Describe the following whenever the video actually contains them:

* the video type, subject, narrative line, and overall visual form;
* the main people, animals, and objects, and their identities or roles;
* appearance, clothing, colors, materials, quantities, and spatial positions;
* actions, interactions, operating steps, and their order;
* the scene environment, foreground, middle ground, background, lighting, left-right relationships, and spatial changes;
* camera viewpoint, shot size, focus, push/pull/pan/tilt, following shots, transitions, and frame layout;
* titles, subtitles, labels, interface text, numbers, formulas, code, tables, and charts;
* speech, narration, language, speakers, discernible original spoken content, and obvious tonal characteristics;
* background music, ambient sound, sound effects, and the onset, end, and obvious changes of sounds;
* the entrance, exit, movement, contact, operation, and state changes of the subjects;
* professional procedures, tools, terminology, and conclusions explicitly demonstrated or explicitly stated in the video;
* temporal continuity, location changes, and time jumps between different scenes.

Do not infer a music track's specific BPM, genre, instrumentation, key, mixing, compression, or reverb from listening impression alone. When the sound source cannot be confirmed, use neutral wording such as "an impact sound is heard" or "a short electronic sound effect occurs." Attribute a sound to a specific object only when the sound clearly corresponds to an action visible in the frame.

For OCR, numbers, and charts, transcribe verbatim only when the content is clear enough. Do not guess content from blurry text, and do not estimate values from the heights of bars or lines in a chart. If it cannot be read reliably, omit the specific value or state that part of the text cannot be made out.

## Timestamp Rules

Describe the video in chronological order, and start a new paragraph at shot cuts, scene changes, changes of main activity, the appearance of a new subject, speaker changes, the appearance of key text, or obvious state changes.

Begin each time segment with the following format:

[hh:mm:ss:xxx-hh:mm:ss:xxx]

For example:

[00:00:04:000-00:00:12:000] The shot cuts to ...

Timestamps must be safe time intervals backed by evidence. Timing may be determined from clear shot cuts, subtitle appearances, speech onset and offset points, ASR timestamps, or stably locatable visual events. Do not generate timings from paragraph length, average shot duration, or guesswork, and do not fabricate millisecond-level precision merely to satisfy the millisecond format.

Match precision to the evidence available. Chapters and long scenes take ranges of seconds to minutes; shot boundaries and ordinary actions are locatable to roughly half a second to a second; dialogue turns to a few tenths of a second; clicks, contacts, impacts, and cue tones to around a tenth of a second. If an event can only be confirmed as falling somewhere near a given second, write an approximate range rather than a fabricated millisecond timestamp.

Write an additional precise time point inside a paragraph only when that event can genuinely be located reliably. Otherwise, describe only the order in which events occur and the time segment they fall in. Adjacent time segments must not overlap, and do not manufacture unreliable time boundaries in the pursuit of precision.

Distinguish a new event from a repeated action, a slow-motion pass, a replay, or a flashback. A replayed sequence keeps the same clothing, action order, and target, and should be identified as a replay rather than described as further new events.

## Output Format

Write one concise overview paragraph followed by multiple chronologically ordered description paragraphs.

Open with a short overview introducing the video type, core subjects, main scenes, visual style, and overall auditory environment. Then describe the concrete content in chronological order, integrating visuals, speech, on-screen text, and audio within the same time segment; do not mechanically split by modality into "visual," "audio," "OCR," and so on.

Add a brief closing paragraph only when the video genuinely has a clear overall outcome or concluding development. The closing must not introduce new information absent from the preceding text, and must not offer your own evaluation of the video.

Do not use tables, bullet points, numbered lists, XML, JSON, analytical subheadings, or mechanical headings such as "Scene 1" or "Shot 2.3." Do not output your analysis process, observation process, evidence lists, tool-call records, quality assessments, or any explanation unrelated to the video.

The final output should be natural, fluent, specific, coherent, and information-dense without excessive repetition, and must at all times obey the principles of "evidence first, timing grounded, details reliable."

Please describe this video in detail."""

SECTIONS_PROMPT = """Provide a detailed description of the video.

Make sure your description covers every one of the following dimensions:

Visual
- Subjects and characters: appearance, clothing, gender/age cues, identity, distinctive features
- Actions and events in chronological order, and how the scene evolves over time
- Setting and background: location, environment, time of day
- Spatial layout and relations between subjects/objects; counts and quantities
- On-screen text: captions, titles, subtitles, logos, UI — exact content and appearance
- Visual style: colors, lighting, camera shots, angles, and camera movement

Audio
- Speech: the exact spoken content, transcribed verbatim
- Speakers: who is speaking (mapped to the on-screen person or voice-over), with accent, tone, gender/age cues
- Speaking state: prosody, emotion, volume, and speaking style
- Music: presence, genre/mood, and lyrics if any
- Sound effects and ambient/background sounds
- Non-speech vocalizations: laughter, crying, applause, etc.

Audio-visual correspondence
- Which speech or sound aligns with which on-screen person or visual event
- The timing of each event, expressed with timestamps

It should explicitly include three sections:

1. A structured chronological storyline of **every noticeable audio and visual details**
2. A structured list of all visible text. For each text element, include start timestamp, end timestamp, the exact text content, the appearance characteristics. If no text appears, explicitly state so.
3. A structured speech-to-text transcription, include speaker（Corresponding to the character or voice‑over in Section 1, including their accent and tone）, exact spoken content, start timestamp, end timestamp, and speaking state (prosody, emotion, and style). If no speech appears, explicitly state so.

Aside from these three required sections, you are free to organize any additional content in any way you find helpful. This additional content can include global information about the entire video or localized information about specific moments. You may choose the topic of this extra content freely.

Rules:

- Add as much descriptive detail as possible.
- Do not use Markdown bold formatting.
- Carefully look at frames and listen to the audio, making sure no detail is overlooked.

Output Format:"""

MULTI_SPEAKER_PROMPT = """Transcribe the dialogue with speaker identification and timestamps. Output format: <soc><sos><start_time>text<end_time><speakerX><eos>...<eoc>."""

AUDIO_EVENT_PROMPT = """Detect the timestamps of the following sound event in the audio: [audio event label]. Output the result strictly as a JSON array. Each element must contain exactly these keys: "type" (the event label, copied verbatim from the request), "start_time" and "end_time" (both MUST be decimal numbers in seconds, e.g. 4.5 or 12.0, NOT strings, and NOT in mm:ss or hh:mm:ss format). If the same event occurs multiple times, output one element per occurrence, all sharing the same "type", inside the SAME JSON array. Do not include any text outside the JSON array. Example: [{"type": "dog_barking", "start_time": 1.23, "end_time": 4.56}]"""

CAPTION_JSON_PROMPT = """Describe the audio and visual content in detail in English, organized into scenes and events, following the JSON Schema below.
All timestamps must be relative to the beginning of the video. End times must not precede start times or exceed the video duration. Each event must fall within the time range of its parent scene.
Include only information directly supported by the audio or video. Do not guess or invent details. Do not infer causality merely because a sound and an action occur at the same time.
Return only valid JSON, without Markdown fences or commentary.

JSON Schema:
""" + json.dumps(CaptionResult.model_json_schema(), ensure_ascii=False, indent=2)
