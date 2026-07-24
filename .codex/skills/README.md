# Project Skills

Source: https://github.com/chaseai-yt/grill-me-codex

Pinned upstream revision at installation:

```text
fe37a7083e93e61d46e84cb8ccdd901fa8aa90fc
```

Installed project-local skills:

- `grill-me-codex`
- `grill-with-docs-codex`
- `codex-review`
- `codex-build`

The upstream workflow was written for Claude Code coordinating an external Codex process. In this repository:

- `grill-me-codex` Act 1 can be used directly for one-question-at-a-time requirements interviews.
- Do not recursively launch Act 2 or Act 3 from an already active Codex session unless the user explicitly requests that workflow.
- These skills do not authorize OMX, tmux teams, destructive Git operations, credential exposure, or automatic implementation.
- Repository `AGENTS.md` and explicit user instructions remain authoritative.
