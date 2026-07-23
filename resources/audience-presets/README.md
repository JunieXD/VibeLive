# Audience presets

This directory contains versioned starter audience profiles. Presets define stable identities, personalities and preferences; they must not define model-call grouping or barrage scheduling algorithms.

The frontend source of truth is `apps/desktop/src/shared/audience/presets.ts`.
Persona documents use this portable layout:

1. The first content in the file is a fenced `json` object with `"version": 1`.
2. The remaining Markdown is the behavioral instruction body.
3. IDs are stable lowercase snake_case or kebab-case values and must not be regenerated from display names.

See `persona-example.md` for a round-trippable example.
