"""Prompts for lecture notes and section/course rollups."""

from __future__ import annotations

SYSTEM = (
    "You are an expert technical note-taker producing revision notes from "
    "video-course transcripts for a working software engineer. You are "
    "precise about API names and syntax, you never pad, and you never invent "
    "detail that the transcript does not support."
)

# The hard constraint. Auto-captions capture only what was SPOKEN; code the
# instructor typed on screen without narrating is simply absent. Inventing
# plausible code there is the failure mode that would make these notes
# untrustworthy, so it is called out explicitly and given an escape hatch.
GROUNDING = """\
GROUNDING RULES — these override any instinct to be helpful:
- Write only what the transcript supports. Do not add facts, versions, flags,
  or parameters from your own knowledge of these libraries.
- The transcript is audio-only. Code shown on screen but not read aloud is NOT
  in the transcript. Where the instructor clearly refers to code without
  describing it ("as you can see here", "let me write this"), do not
  reconstruct it. Emit exactly:
  > [!] Code shown on screen, not described in the audio.
- The transcript is machine-generated and still contains mis-heard words.
  Repair obvious errors from context (product names, API names, numbers).
- If a section of this template has no support in the transcript, write
  "_Not covered in this lecture._" rather than filling it with generic
  material.
"""

LECTURE_TEMPLATE = """\
{grounding}
COURSE: {course}
SECTION: {section_idx} - {section_title}
LECTURE: {lec_label}  (lecture {lec_pos} of {lec_total} in this section)

Produce revision notes as GitHub-flavoured Markdown. Start at heading level 3
(`###`) and use EXACTLY these four sections, in this order, with these exact
headings:

### Summary
One-line **Objective:**. Then **Key concepts** as a bullet list, each
`- **Term** - definition`. Then **Takeaways** (3-6 bullets). Then a
**Why it matters** line placing this lecture in the course.

### Cheat-sheet
Only what is worth revisiting later: syntax, API signatures, config,
CLI commands, parameter meanings, and gotchas. Use fenced code blocks with a
language tag. Prefix each gotcha with `[!]`. If the lecture is purely
conceptual, write "_Conceptual lecture - no commands or syntax._"

### Recall
5-10 question/answer pairs for active recall, formatted:
**Q:** question
**A:** answer

Test understanding, not trivia. Skip if the lecture is pure setup/admin.

### Code walkthrough
Reconstruct, step by step, only the code the instructor actually NARRATED,
with a sentence of explanation per step. Obey the grounding rule above about
on-screen code. If no code was discussed, write "_No code in this lecture._"

Do not add any heading beyond those four. Do not add a preamble, a title, or a
closing remark - output the Markdown only.

TRANSCRIPT:
---
{transcript}
---
"""

SECTION_ROLLUP_TEMPLATE = """\
{grounding}
COURSE: {course}
SECTION: {section_idx} - {section_title} ({n_lectures} lectures)

Below are the per-lecture notes already generated for this whole section.
Write a section-level overview as GitHub-flavoured Markdown, starting at
heading level 3 (`###`), with EXACTLY these headings:

### Section overview
What this section teaches and how it builds on the earlier ones. 2-4 sentences.

### Concept map
The section's concepts as a nested bullet list, showing how they relate.

### Consolidated cheat-sheet
The syntax, commands, and gotchas from across the whole section, de-duplicated
and grouped by topic. This is the page to revise from.

### Common pitfalls
The mistakes this section warns about, as a bullet list. Write
"_None flagged._" if the notes flag none.

Output the Markdown only, no preamble.

PER-LECTURE NOTES:
---
{notes}
---
"""

COURSE_INDEX_TEMPLATE = """\
{grounding}
COURSE: {course}

Below are the section-level overviews for every section of this course.
Write a course-level master index as GitHub-flavoured Markdown, starting at
heading level 2 (`##`), with EXACTLY these headings:

## What this course covers
A short orientation: the arc of the course, 3-5 sentences.

## Learning path
The sections in order as a numbered list, each with one line on what it adds
and what it depends on.

## Core concepts across the course
The ideas that recur throughout, as `- **Term** - definition` bullets, with
the section number where each is introduced.

## Suggested revision order
A practical order to revise in, which need not match the teaching order.
Explain briefly why.

Output the Markdown only, no preamble.

SECTION OVERVIEWS:
---
{sections}
---
"""


def lecture_prompt(course, section, lecture, lec_pos, lec_total, transcript):
    return LECTURE_TEMPLATE.format(
        grounding=GROUNDING,
        course=course,
        section_idx=section.idx,
        section_title=section.title,
        lec_label=lecture.label,
        lec_pos=lec_pos,
        lec_total=lec_total,
        transcript=transcript,
    )


def section_rollup_prompt(course, section, notes):
    return SECTION_ROLLUP_TEMPLATE.format(
        grounding=GROUNDING,
        course=course,
        section_idx=section.idx,
        section_title=section.title,
        n_lectures=len(section.lectures),
        notes=notes,
    )


def course_index_prompt(course, sections_text):
    return COURSE_INDEX_TEMPLATE.format(
        grounding=GROUNDING, course=course, sections=sections_text
    )


NO_TRANSCRIPT_STUB = """\
### Summary

> [!] **No transcript available for this lecture.**
>
> Udemy's auto-captions produced {words} words for this lecture, which is not
> enough to generate notes from. Nothing here was inferred - watch this one
> directly.

### Cheat-sheet

_Unavailable - no transcript._

### Recall

_Unavailable - no transcript._

### Code walkthrough

_Unavailable - no transcript._
"""


EMPTY_SECTION_STUB = """\
### Section overview

> [!] **No transcript available for any lecture in this section.**
>
> Udemy's auto-captions failed across this whole section, so there is nothing
> to summarise. Watch these lectures directly.

### Concept map

_Unavailable - no transcript._

### Consolidated cheat-sheet

_Unavailable - no transcript._

### Common pitfalls

_Unavailable - no transcript._
"""


DIAGRAM_TEMPLATE = """\
{grounding}
COURSE: {course}
SECTION: {section_idx} - {section_title}

Below are the notes already generated for this section. Produce 1-3 Mermaid
diagrams that make this section's material easier to understand at a glance.

WHAT TO DRAW - in priority order, only where the notes actually support it:
1. **System / architecture structure** - the components involved and how they
   connect (`flowchart LR` or `flowchart TB`).
2. **Control or data flow** - the path a request, message, or piece of state
   takes through the system (`flowchart TB`, or `sequenceDiagram` when the
   order of interactions between parties is the point).
3. **State transitions** - when the section is genuinely about states and the
   events that move between them (`stateDiagram-v2`).

DO NOT produce:
- Mind maps, word clouds, or "concept" bubbles restating the heading list.
- A diagram of the course structure, the lecture order, or the learning path.
- Decorative diagrams that carry no information the notes don't already state
  plainly in one sentence.

If the section is pure setup, installation, or admin with no system or flow to
draw, output exactly `_No diagram adds anything to this section._` and nothing
else. A missing diagram is better than a useless one.

RULES FOR THE MERMAID ITSELF - a diagram that does not render is worse than
no diagram:
- Start each block with a valid type line: `flowchart LR`, `flowchart TB`,
  `sequenceDiagram`, or `stateDiagram-v2`.
- Node ids must be simple alphanumerics (`nodeA`, `step1`). Put all punctuation
  inside the quoted label: `nodeA["ChatOpenAI (paid)"]`.
- Never use a Mermaid keyword as a node id - `graph`, `end`, `state`, `class`,
  `style`, `click`, `subgraph`, `direction`, `note`. They break the parser.
  Write `graphNode["compiled graph"]`, not `graph["compiled graph"]`.
- Quote every label containing a space, bracket, comma, or symbol.
- No HTML, no `<br>`, no CSS, no `style`/`classDef` lines, no emoji.
- Keep it to at most 12 nodes. A diagram too dense to read teaches nothing.

FORMAT - for each diagram, exactly this, and nothing else:

#### <short title naming what is drawn>

```mermaid
<the diagram>
```

<one sentence saying what the diagram shows>

SECTION NOTES:
---
{notes}
---
"""


def diagram_prompt(course, section, notes):
    return DIAGRAM_TEMPLATE.format(
        grounding=GROUNDING,
        course=course,
        section_idx=section.idx,
        section_title=section.title,
        notes=notes,
    )
