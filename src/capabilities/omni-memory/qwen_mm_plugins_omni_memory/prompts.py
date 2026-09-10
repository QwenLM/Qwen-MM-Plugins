# Trailing whitespace inside the prompts is intentional — do not strip it.
"""The prompts the READ path calls a model with: re-watching stored clips, and watching a video that
has no memory at all. mem_core fills the {{PLACEHOLDER}} markers at call time.

Every one is a RAW string: the JSON examples inside contain backslash-escaped quotes that a normal
literal would silently unescape. They are Python rather than .md data files because markdown next to
the package is not covered by the host's [tool.setuptools.package-data], so it would go missing after
a pip install.

The build's prompts live in skill/script/build_memory/prompts.py; no prompt appears in both files.
"""

# Answering by re-watching the selected source clips alongside the text evidence. The one model call
# left on the read path, and the agent decides when it happens (replay_and_answer).
REPLAY_ANSWER_PROMPT = r"""You are answering the user's question about a video. The stored text
memory was insufficient, so you are now RE-WATCHING the original video clip(s) provided above. Use
what you SEE and HEAR in the clip(s) (plus the text memory below as context) to answer. If multiple
clips are given, they are in chronological order — combine evidence across them (e.g. for counting
or "which objects"). Answer in the SAME language as the question; be concise and direct.

{{EVIDENCE}}

QUESTION: {{QUERY}}
"""

# Answering a SHORT video in one pass, with no memory involved (watch_and_answer). Unlike the replay
# prompt there is no stored evidence to lean on and nothing was retrieved first, so the model is told
# it is seeing the whole video and must say so when the answer is not in it.
WATCH_ANSWER_PROMPT = r"""You are answering the user's question about a video. You are WATCHING the
whole video provided above — there is no stored memory of it and nothing was retrieved beforehand, so
everything you know about it is what you see and hear right now.

Use both streams and hold them against each other: what is said WHILE something is done, who is
speaking (go by lip movement and who is visibly talking), how it is said, and what non-speech sound
is present. Ground the answer in specific moments; give approximate timestamps when they help.

If the answer is not in the video, say so plainly rather than inferring it — a wrong guess is worse
than "the video does not show this". Answer in the SAME language as the question; be concise and
direct.

QUESTION: {{QUERY}}
"""
