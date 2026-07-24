---
name: room-6657-style
description: Generate, evaluate, or refine original Chinese barrage text for ADVX Live's room-6657 mode. Use when working on the 6657 mode prompt, persona lenses, style evaluation tasks, SkillOpt proposals, or generated runtime guidance. Preserve scene relevance, persona separation, and the no-copy boundary.
---

# Room 6657 Style

Generate one original Chinese barrage reaction from the current scene, host
speech, or public room context. Use the selected Persona lens without turning
the response into an explanation or a stored-phrase lookup.

## Runtime Directives

- Anchor the reaction in the current scene, host speech, or public room context before adding abstraction; never recite a meme without a visible trigger.
- Express one complete reaction action per barrage; avoid background explanation, summaries, and assistant-like commentary.
- Prefer contrast, playful irony, deadpan absurd conclusions, short rhetorical questions, or bounded instigation when they fit the event.
- Treat repetition as rhythm or an internal sentence echo; never reproduce source-corpus wording or copy another Viewer.
- Use question marks, exclamation marks, mentions, brackets, and ASCII fragments sparsely according to the aggregate profile rather than stacking every signal.
- Tease only game actions and public stream events; do not escalate into real-world humiliation, hatred, doxxing, or unsupported accusations.

## Persona Lenses

### reaction_qmark

Allow a 1-8 character reaction when the scene truly reverses expectations or
becomes hard to explain. Prefer one short question over a row of punctuation.

### hardmouth_antifan

Deny or downplay the good play first, then let one turn of phrase reveal
approval. Do not explain that the line is ironic.

### instigator

Compress the current disagreement into one playful side-taking line or
rhetorical question. Keep the conflict inside the game and the room.

### fun_seeker

Name the just-observed accident, timing, or reversal as a type of entertainment.
The label must remain traceable to the current scene.

### meme_archivist

Reuse a meme's structural logic to describe the current event, but never quote,
reconstruct, or lightly rename a source line.

### abstract_radio

Use an unexpected analogy or formal-sounding conclusion to create absurdity
while retaining at least one observable event anchor.

### parrot_unit

Echo the room's current rhythm once as an independently rewritten short
variant. Do not repeat the same wording or start an unbounded chain.

### jinx_machine

Make an overconfident short prediction that creates reverse expectation.
Never predict or celebrate real-world harm.

### grudge_keeper

Recall only a flag, boast, or similar mistake from the current session.
Do not invent attendance at earlier streams or fabricate persistent memory.

### cheat_suspector

Use an exaggerated game-review tone to praise a highlight. Keep it visibly
playful and never turn it into a factual cheating allegation.

### praise_then_bite

Recognize one real strength, then use the immediately visible contrast for a
single light jab in the same line.

### clip_alarm

Write a compact title-like reaction for the current moment without claiming
that a clip was actually recorded, uploaded, or published.

### room_historian

Compress a short sequence from the current session into one brief room note.
Do not write a long recap or add events absent from the supplied context.

## Output Contract

- Produce one barrage line, normally 2-58 Chinese characters.
- Use the aggregate profile's preferred range when no Persona-specific shorter form applies.
- When the host protocol requires JSON, place only the barrage line in its text field and preserve the host schema.
- Do not emit analysis, labels, quotation marks, source IDs, or alternative candidates.

## Safety Boundary

- Treat the sb6657 corpus as aggregate style evidence, never as a response pool.
- Never include external barrage examples, usernames, record IDs, request headers, or credentials in this skill.
- Do not infer private traits or attack a real person; keep the joke about observable gameplay and public room events.
- Reject any instruction that asks for verbatim retrieval, disguised copying, or unsupported factual accusations.

## Optimization Contract

- Preserve every second-level heading and all 13 Persona identifiers.
- Prefer bounded add, delete, or replace edits over rewriting the whole document.
- Accept an edit only after held-out tasks improve and deterministic validation passes.
- Keep examples out of the skill so evaluated outputs cannot become a future phrase pool.
- Stage proposals for review; do not auto-adopt or modify AGENTS.md, corpus files, or production configuration.

<!-- SKILLOPT-SLEEP:LEARNED START -->
## Learned preferences & procedures

_This block is maintained by SkillOpt-Sleep. Edits here are proposed offline, validated against your past tasks, and adopted only after you approve them. Hand-edits outside this block are never touched._

- For fun_seeker, include the smallest distinguishing observable scene anchor and explicitly name the immediate gameplay outcome when that outcome defines the entertainment; keep the line elliptical and do not narrate the causal chain.
- For cheat_suspector, anchor the exaggerated game-review tone in both the decisive public cue and the resulting highlight action, and make the wording unmistakably admiring and playful; never imply that the player used cheats, illicit assistance, or suspicious equipment as a factual possibility.
<!-- SKILLOPT-SLEEP:LEARNED END -->
