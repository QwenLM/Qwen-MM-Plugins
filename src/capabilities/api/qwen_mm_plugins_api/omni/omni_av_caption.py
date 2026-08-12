"""Omni A/V Caption — detailed Markdown report of a video: timestamped storyline, visible text,
speaker transcript, plus minor-safety compliance flags and a final safety assessment."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from ._common import run_omni, summary_block


class OmniAvCaptionArgs(BaseModel):
    file_path: str = Field(description="Absolute path to a local audio/video file, or an http(s)/OSS URL.")
    fps: Optional[float] = Field(
        default=None,
        description="Video sampling fps (default 1.0). Higher = finer temporal detail, more tokens, and a "
        "larger upload — which shortens the maximum uploadable length.",
    )
    max_pixels: Optional[int] = Field(default=None, description="Per-frame pixel budget (default 200704 ≈ 448²).")
    model: Optional[str] = Field(
        default=None,
        description="Omni model id override. Defaults to QWEN_MM_API_OMNI_MODEL, then qwen3.5-omni-plus.",
    )
    api_key: Optional[str] = Field(default=None, description="DashScope API key (defaults to DASHSCOPE_API_KEY).")
    base_url: Optional[str] = Field(default=None, description="OpenAI-compatible base URL override.")
    dry_run: bool = Field(default=False, description="Return the request that would be sent, without calling the API.")


TOOL: dict[str, Any] = {
    "name": "omni_av_caption",
    "description": (
        "Produce a detailed Markdown report of an audio/video using the Qwen-Omni model (reads both "
        "the video frames and the audio track): a timestamped storyline, all visible text, a "
        "speaker-attributed transcript, plus flags for content inappropriate for minors with a "
        "compliance-alert table and a final safety assessment. "
        "A local file is uploaded inline, where the endpoint caps a media item at 10 MB of base64, so "
        "it is transcoded to fit — about 9 min at the default 1 fps / 448² sampling. A longer local video "
        "is delivered another way automatically: uploaded to OSS when OSS_* is configured (no size "
        "limit), else split into sampled frames plus its full audio track. Passing an http(s)/OSS URL "
        "skips all of that (fetched and sampled server-side); for hour-scale video use video-memory. "
        "Use dry_run=true to preview the request payload without calling."
    ),
    "args": OmniAvCaptionArgs,
}

_PROMPT = """
Please provide a detailed description of the video. It must explicitly include three primary sections as defined below. Additionally, you are required to include two specific analytical sections to identify content that is inappropriate for minors.

### Section 1: Storyline
Provide a structured chronological storyline of every noticeable audio and visual detail.
*   **Requirement:** In the storyline, if a segment contains content potentially harmful to minors (graphic violence, gore, dangerous acts, bullying, or inappropriate adult themes), you must append the following at the end of that specific paragraph: **[FLAG: <Category> - <Severity: Low/Medium/High>]**.

### Section 2: Visible Text
Provide a structured list of all visible text. For each text element, include start timestamp, end timestamp, the exact text content, and the appearance characteristics. If no text appears, explicitly state: "No text appears."

### Section 3: Speakers and Transcript
Provide a structured speech-to-text transcription. Include the speaker (corresponding to the character or voice-over, including accent and tone), exact spoken content, start timestamp, end timestamp, and speaking state (prosody, emotion, and style). If no speech appears, explicitly state: "No speech appears."

### Section 4: Compliance Alert (Summary)
Provide a table summarizing all flagged segments for quick review:
| Time Range | Category | Risk Level | Justification |
| :--- | :--- | :--- | :--- |
| xx:xx - xx:xx | e.g., Violence | High | e.g., Realistic physical assault observed. |

### Section 5: Summary of Safety Findings
Provide a final assessment on whether the video is suitable for minors, citing the most critical timestamps and explaining the overall risk profile. Apply a zero-tolerance policy; if there is ambiguity regarding whether a scene is harmful, err on the side of caution.

---

## Output Format:

```
## Storyline

<xx:xx.xxx> - <xx:xx.xxx>
<An unstructured long paragraph describing all audio/visual details. If harmful, append [FLAG: <Category> - <Severity>].>

## Visible Text

<xx:xx.xxx> - <xx:xx.xxx>
“<element>”: <appearance>

## Speakers and Transcript

Speaker profiles:
<speaker> - <profile>

<xx:xx.xxx> - <xx:xx.xxx>
Speaker: <speaker>
State: <description>
Content: “<content>”

## Compliance Alert (Summary)

| Time Range | Category | Risk Level | Justification |
| :--- | :--- | :--- | :--- |

## Summary of Safety Findings

<Paragraphs detailing the safety assessment.>
```
"""


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    # The prompt asks for a Markdown report, not JSON — return the model's text as-is. The
    # five-section report runs long, so give it more headroom than the default 4096 tokens.
    text, blocks = run_omni(arguments, prompt=_PROMPT, mode="auto", json_output=False, max_tokens=65536)
    if blocks is not None:
        return blocks
    return [summary_block(str(text))]
