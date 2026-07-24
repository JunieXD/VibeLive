---
name: skillopt-sleep
description: Run the Microsoft SkillOpt-Sleep workflow for ADVX Live's room-6657 style skill. Use when downloading the pinned optimizer, validating the reviewed task set, running mock or Codex optimization, inspecting staged candidates, evaluating final tests, adopting a reviewed improvement, rejecting an unsafe candidate, or rolling back an adopted proposal.
---

# SkillOpt-Sleep

Use the project wrapper for every room-6657 optimization action:
`python scripts/run_room_6657_skillopt.py <action>`.

Do not invoke the upstream `skillopt_sleep` CLI directly from this repository.
The wrapper pins the upstream commit, uses project-private state, selects the
native Windows Codex executable, disables memory evolution, and compiles
accepted learned directives into the backend runtime artifact.

## Workflow

1. Run `bootstrap`, then `validate`.
2. Run `dry-run --backend mock` to prove plumbing only.
3. Run `run --backend codex` for a real bounded optimization.
4. Run `status` and inspect the latest `report.md`, `report.json`,
   `diagnostics.json`, and `proposed_SKILL.md`.
5. Reject a candidate that weakens a Persona or product contract, even when the
   model gate accepts it.
6. Run `evaluate --backend codex --skill <candidate>` on every candidate that
   survives review.
7. Record the explicit review with
   `approve --staging <directory> --reason "<review rationale>"` after the
   final tests pass. A rejected candidate cannot later be approved; run a new
   optimization instead.
8. Run `adopt --staging <directory>` only after approval is recorded.
9. Run project tests after adoption. Use `rollback --staging <directory>` if an
   adopted proposal regresses runtime behavior.

## Boundaries

- Use only the reviewed task file under `tests/fixtures/room-6657/`.
- Never harvest local transcripts or private sessions for this workflow.
- Never enable memory evolution, scheduling, or automatic adoption.
- Treat mock results as plumbing evidence, not language-quality evidence.
- Keep the sb6657 corpus out of prompts, tasks, skills, staging reports, and
  generated runtime JSON.
- Preserve every second-level heading and all 13 Persona identifiers in the
  target skill.
- Allow at most two proposed edits in one run.
- Require strict validation-set improvement, project review, and untouched
  final-test success before adoption.
- Keep rejected staging directories as local evidence; they are Git-ignored and
  cannot be adopted through the wrapper after rejection.

## Upstream

The pinned Microsoft SkillOpt repository and license are recorded in
`resources/skillopt/skillopt.lock.json`. `bootstrap` downloads that exact commit
to `.advx-data/tools/SkillOpt`, which remains outside version control.
