# Repository scripts

Scripts in this directory must work on both Windows and macOS. Prefer Node.js or Python over platform-specific shell scripts.

## sb6657 style corpus

Fetch the public read-only barrage corpus into ignored local data, then derive an aggregate-only profile:

```powershell
python scripts/fetch_sb6657_corpus.py --page-size 500 --delay 0.35
python scripts/profile_sb6657_corpus.py
```

The fetcher deliberately omits `dpahjdoiaw` and `siteToken`. Never commit `.advx-data/sb6657/corpus.jsonl`; only the reviewed aggregate profile belongs in source control.

## room-6657 generation skill

Compile the reviewed project Skill into the backend runtime artifact:

```powershell
python scripts/sync_room_6657_skill.py
python scripts/sync_room_6657_skill.py --check
```

Download the locked Microsoft SkillOpt checkout and run the isolated, review-gated optimization loop:

```powershell
python scripts/run_room_6657_skillopt.py bootstrap
python scripts/run_room_6657_skillopt.py dry-run --backend mock
python scripts/run_room_6657_skillopt.py run --backend codex
python scripts/run_room_6657_skillopt.py status
python scripts/run_room_6657_skillopt.py evaluate --backend codex --skill <candidate>
python scripts/run_room_6657_skillopt.py approve --staging <path> --reason <text>
python scripts/run_room_6657_skillopt.py adopt --staging <path>
```

Real model calls use a temporary minimal workspace, a sanitized environment,
and a temporary Codex home containing only copied authentication. Real runs
stage proposals only. Evaluation, approval, adoption, and rollback are bound to
the exact candidate and require an explicit staging directory.
Use `reject --staging <path> --reason <text>` when a scored candidate violates a
Persona or product contract; the project wrapper will then refuse to adopt it.
