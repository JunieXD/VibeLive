from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

MODE_ID = "room-6657"
PERSONA_IDS = (
    "reaction_qmark",
    "hardmouth_antifan",
    "instigator",
    "fun_seeker",
    "meme_archivist",
    "abstract_radio",
    "parrot_unit",
    "jinx_machine",
    "grudge_keeper",
    "cheat_suspector",
    "praise_then_bite",
    "clip_alarm",
    "room_historian",
    "longtime_fan",
)
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = REPO_ROOT / ".codex" / "skills" / "room-6657-style" / "SKILL.md"
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "apps"
    / "backend"
    / "src"
    / "advx_backend"
    / "providers"
    / "model"
    / "room_6657_generation_skill.json"
)

_SECOND_LEVEL_HEADING = re.compile(r"^## (.+?)\s*$")
_THIRD_LEVEL_HEADING = re.compile(r"^### ([a-z0-9_]+)\s*$")
_DIRECTIVE = re.compile(r"^- (.+?)\s*$")
_TODO = re.compile(r"\b(?:TODO|TBD|FIXME)\b", re.IGNORECASE)
_EXAMPLE_HEADING = re.compile(r"\b(?:examples?|samples?)\b|示例|样例", re.IGNORECASE)
_LEARNED_START = "<!-- SKILLOPT-SLEEP:LEARNED START -->"
_LEARNED_END = "<!-- SKILLOPT-SLEEP:LEARNED END -->"
_LEARNED_HEADING = "## Learned preferences & procedures"


class SkillSyncError(ValueError):
    pass


def compile_skill(path: Path) -> bytes:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SkillSyncError(f"{path} must be UTF-8: {error}") from error

    _validate_no_examples_or_placeholders(text)
    sections = _second_level_sections(text)
    directives = _parse_directives(sections)
    persona_lenses = _parse_persona_lenses(sections)
    learned_directives = _parse_learned_directives(text)
    payload = {
        "schema_version": 1,
        "mode_id": MODE_ID,
        "source_skill_sha256": hashlib.sha256(raw).hexdigest(),
        "directives": directives,
        "learned_directives": learned_directives,
        "persona_lenses": persona_lenses,
    }
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, separators=(",", ": ")) + "\n"
    ).encode("utf-8")


def _validate_no_examples_or_placeholders(text: str) -> None:
    if _TODO.search(text):
        raise SkillSyncError("skill contains TODO, TBD, or FIXME")
    if "```" in text or "~~~" in text:
        raise SkillSyncError("skill must not contain fenced examples")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            raise SkillSyncError("skill must not contain quoted barrage examples")
        heading = _SECOND_LEVEL_HEADING.match(stripped)
        if heading and _EXAMPLE_HEADING.search(heading.group(1)):
            raise SkillSyncError("skill must not contain an examples section")


def _second_level_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        match = _SECOND_LEVEL_HEADING.match(line)
        if match:
            current = match.group(1)
            if current in sections:
                raise SkillSyncError(f"duplicate second-level heading: {current}")
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    for required in ("Runtime Directives", "Persona Lenses"):
        if required not in sections:
            raise SkillSyncError(f"missing required heading: ## {required}")
    return sections


def _parse_directives(sections: dict[str, list[str]]) -> list[str]:
    lines = sections["Runtime Directives"]
    directives: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        match = _DIRECTIVE.match(line)
        if not match:
            raise SkillSyncError("Runtime Directives may contain only non-empty bullet items")
        directives.append(match.group(1))
    if not directives:
        raise SkillSyncError("Runtime Directives must contain at least one item")
    if len(set(directives)) != len(directives):
        raise SkillSyncError("Runtime Directives contains duplicate items")
    return directives


def _parse_persona_lenses(sections: dict[str, list[str]]) -> dict[str, str]:
    lenses: dict[str, list[str]] = {}
    current: str | None = None
    for line in sections["Persona Lenses"]:
        match = _THIRD_LEVEL_HEADING.match(line)
        if match:
            current = match.group(1)
            if current in lenses:
                raise SkillSyncError(f"duplicate Persona lens: {current}")
            lenses[current] = []
            continue
        if not line.strip():
            continue
        if current is None:
            raise SkillSyncError("Persona Lenses must begin with a Persona heading")
        if line.startswith("#") or line.lstrip().startswith("- "):
            raise SkillSyncError(f"Persona lens {current} must be prose, not nested Markdown")
        lenses[current].append(line.strip())

    actual = set(lenses)
    expected = set(PERSONA_IDS)
    if actual != expected:
        missing = ", ".join(sorted(expected - actual)) or "none"
        extra = ", ".join(sorted(actual - expected)) or "none"
        raise SkillSyncError(f"Persona set mismatch; missing: {missing}; extra: {extra}")

    normalized: dict[str, str] = {}
    for persona_id in PERSONA_IDS:
        value = " ".join(lenses[persona_id])
        if not value:
            raise SkillSyncError(f"Persona lens {persona_id} must not be empty")
        normalized[persona_id] = value
    return normalized


def _parse_learned_directives(text: str) -> list[str]:
    start_count = text.count(_LEARNED_START)
    end_count = text.count(_LEARNED_END)
    if start_count == 0 and end_count == 0:
        return []
    if start_count != 1 or end_count != 1:
        raise SkillSyncError("skill must contain one complete SkillOpt learned block")
    start = text.index(_LEARNED_START)
    end = text.index(_LEARNED_END)
    if end <= start:
        raise SkillSyncError("SkillOpt learned block markers are out of order")

    block = text[start + len(_LEARNED_START) : end]
    directives: list[str] = []
    heading_seen = False
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == _LEARNED_HEADING:
            if heading_seen:
                raise SkillSyncError("SkillOpt learned block has a duplicate heading")
            heading_seen = True
            continue
        if stripped.startswith("_") and stripped.endswith("_"):
            continue
        match = _DIRECTIVE.match(stripped)
        if not match:
            raise SkillSyncError("SkillOpt learned block may contain only its banner and bullets")
        directives.append(match.group(1))
    if not heading_seen:
        raise SkillSyncError("SkillOpt learned block is missing its heading")
    if not directives:
        raise SkillSyncError("SkillOpt learned block must contain at least one directive")
    if len(set(directives)) != len(directives):
        raise SkillSyncError("SkillOpt learned block contains duplicate directives")
    return directives


def _run_self_test() -> None:
    source = """# Room 6657 Style

## Runtime Directives

- React to the current scene.

## Persona Lenses

"""
    source += "\n".join(f"### {persona_id}\n\nLens for {persona_id}.\n" for persona_id in PERSONA_IDS)
    with tempfile.TemporaryDirectory() as temporary_directory:
        path = Path(temporary_directory) / "SKILL.md"
        path.write_text(source, encoding="utf-8")
        payload = json.loads(compile_skill(path))
        assert payload["mode_id"] == MODE_ID
        assert payload["directives"] == ["React to the current scene."]
        assert payload["learned_directives"] == []
        assert list(payload["persona_lenses"]) == list(PERSONA_IDS)

        learned = (
            "\n<!-- SKILLOPT-SLEEP:LEARNED START -->\n"
            "## Learned preferences & procedures\n\n"
            "_Managed by SkillOpt-Sleep._\n\n"
            "- Use the supplied scene as a complete task.\n"
            "<!-- SKILLOPT-SLEEP:LEARNED END -->\n"
        )
        path.write_text(source + learned, encoding="utf-8")
        payload = json.loads(compile_skill(path))
        assert payload["learned_directives"] == [
            "Use the supplied scene as a complete task."
        ]

        path.write_text(source + "\n## Examples\n\n> copied line\n", encoding="utf-8")
        try:
            compile_skill(path)
        except SkillSyncError:
            pass
        else:
            raise AssertionError("external example validation did not fail")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile the room-6657 Markdown skill into backend runtime JSON."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail unless the output already matches the canonical generated bytes.",
    )
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.self_test:
            _run_self_test()
            print("room-6657 skill sync self-test passed")
            return 0

        generated = compile_skill(args.input.resolve())
        output = args.output.resolve()
        if args.check:
            if not output.exists():
                raise SkillSyncError(f"generated output is missing: {output}")
            if output.read_bytes() != generated:
                raise SkillSyncError(
                    f"generated output is stale; run {Path(__file__).name} to refresh it"
                )
            print(f"room-6657 generation skill is current: {output}")
            return 0

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(generated)
        print(f"wrote room-6657 generation skill: {output}")
        return 0
    except (OSError, SkillSyncError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
