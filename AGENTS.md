# Repository Guidelines

## Project Structure & Module Organization

`apps/desktop` contains the Electron and React client. Keep process-specific code under `src/main`, `src/preload`, and `src/renderers`; reusable client logic belongs in `src/shared`. `apps/backend` is the FastAPI service, organized into `api`, `application`, `domain`, `infrastructure`, and `providers`, with Python tests in `apps/backend/tests`. Generated TypeScript API types live in `packages/contracts`. Put distributable audience presets in `resources`, cross-process scenarios in `tests/e2e`, synthetic fixtures in `tests/fixtures`, and product or architecture decisions in `docs`.

## Build, Test, and Development Commands

Use Node.js 24+, pnpm 11+, Python 3.11 or 3.12, and uv 0.11+.

- `pnpm install` installs workspace dependencies.
- `uv sync --project apps/backend --group dev` creates the backend development environment.
- `pnpm dev` starts the backend and Electron app together.
- `pnpm dev:desktop` or `pnpm dev:backend` runs one side independently.
- `pnpm contracts` exports backend OpenAPI and regenerates TypeScript contracts.
- `pnpm typecheck`, `pnpm test`, and `pnpm build` run repository-wide validation.
- `uv run --project apps/backend ruff check apps/backend` checks Python formatting and imports.

## Coding Style & Naming Conventions

Follow `.editorconfig`: UTF-8, LF endings, final newlines, two-space indentation, and four spaces for Python. Existing TypeScript uses single quotes and no semicolons. Use `PascalCase` for React components and Python classes, `camelCase` for TypeScript functions and variables, and `snake_case` for Python modules and functions. Ruff enforces Python rules `E`, `F`, `I`, and `UP` with a 100-character line limit. Do not hand-edit `packages/contracts/src/generated/openapi.ts`; regenerate it with `pnpm contracts`.

## Testing Guidelines

Desktop tests use Vitest and are colocated as `*.test.ts`; backend tests use pytest and follow `test_*.py`. Add focused regression tests for changed behavior, then run `pnpm test` and `pnpm typecheck`. End-to-end tests should exercise the real Electron/FastAPI lifecycle. Keep fixtures small, synthetic, and free of private recordings or screenshots. No minimum coverage threshold is currently configured.

## Commit & Pull Request Guidelines

Follow the repository's Conventional Commit style, such as `feat(desktop): add overlay controls`, `feat(backend): add session runtime`, `docs: update architecture`, or `chore: update tooling`. Keep commits scoped and imperative. Pull requests should explain the user-visible change, identify affected apps, list validation commands, link relevant issues, and include screenshots for UI changes. Note regenerated contracts or documentation updates explicitly.

## Security & Configuration

Copy settings from `.env.example`, but never commit `.env` files, provider credentials, logs, `.advx-data`, or Electron user data. Store production secrets through Electron `safeStorage`.
