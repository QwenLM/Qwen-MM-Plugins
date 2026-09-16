---
trait: animation
applies_to:
  output_extensions: [".pptx", ".odp", ".html", ".css", ".svg", ".mp4", ".gif"]
  keywords: ["animation", "animated", "transition", "morph", "reveal", "easing", "keyframe",
             "swing", "fade", "motion", "动画", "转场", "切换", "平滑", "缓动"]
capabilities_used:
  inspect: ["zipfile/lxml"]
  render: ["soffice --convert-to pdf", "pdftoppm", "playwright"]
---

# Trait: animation

Stacks on the base grader. Read it whenever the thing being graded is supposed to *move*.

## The trap

**A renderer that flattens motion is not a renderer that disproves it.** LibreOffice converts a
`.pptx` to PDF by drawing each slide's end state; a screenshot of a web page catches one instant of
a transition. In both cases the motion is real and invisible to the tool you used. So a rendered
image is evidence about *composition* — did the right things end up in the right places — and says
nothing about whether the animation exists.

Charging that to the skill is the failure mode: it reads as "the animation didn't work" when what
happened is "this environment cannot play animations" — and since the animation is usually the point
of the eval, it turns an environment gap into the skill's headline score.

## The evidence that does hold

Motion is declared in the file, so read the declaration:

- **pptx / odp** — unzip and read the slide XML. A Morph transition is a `p159:morph` element (the
  `2015/main` namespace, `Requires="p159"`); entrance/exit effects live in `p:timing`/`p:anim*`;
  3-D motion shows up as `a:scene3d`/`a:sp3d` with `a:rot` values. Paired shapes that Morph
  interpolates must exist on **both** slides with matching name/id and differing geometry — that
  pairing IS the animation, and it is fully checkable offline.
- **css / html** — `@keyframes`, `animation:`, `transition:`, `transform`. Read the declared
  duration, easing and property, and check they match what the video specified.
- **svg** — `<animate>`, `<animateTransform>`, or CSS inside `<style>`.
- **mp4 / gif** — here motion is directly checkable: `ffprobe` for frame count and duration, and
  two frames sampled at different times to confirm the content actually changes.

## How to word the verdict

Grade the declaration and the composition separately, and say which you did:

- technique present in the markup → PASS on the "is it animated" assertion, with the element quoted;
- end states correct in the render → PASS on the composition assertion, noting the flattening;
- neither obtainable → NOT_VERIFIABLE, with what you tried.

Do not write "no animation observed in the render" as a FAIL. Write that the render shows the end
states, that motion does not survive this conversion, and what the markup says.
