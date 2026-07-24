from __future__ import annotations

import hashlib
import json
import re
from functools import cache
from importlib.resources import files
from typing import Any

ROOM_6657_MODE_ID = "room-6657"
_PROFILE_RESOURCE = "room_6657_style_profile.json"
_GENERATION_SKILL_RESOURCE = "room_6657_generation_skill.json"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class StyleProfileError(RuntimeError):
    pass


def style_guidance_for(
    mode_context: dict[str, object],
    *,
    persona_id: str,
) -> dict[str, object] | None:
    if mode_context.get("mode_id") != ROOM_6657_MODE_ID:
        return None

    profile = _load_profile()
    generation_skill = _load_generation_skill()
    source = _require_dict(profile, "source")
    lengths = _require_dict(profile, "length_characters")
    quantiles = _require_dict(lengths, "quantiles")
    rates = _require_dict(profile, "rates")
    rhetoric = _require_dict(profile, "rhetorical_signals")
    popular = profile.get("popular_slice")
    runtime_quantiles = (
        _require_dict(popular, "length_quantiles")
        if isinstance(popular, dict)
        and isinstance(popular.get("length_quantiles"), dict)
        else quantiles
    )
    popular_rates = (
        _require_dict(popular, "rates")
        if isinstance(popular, dict) and isinstance(popular.get("rates"), dict)
        else rates
    )
    popular_rhetoric = (
        _require_dict(popular, "rhetorical_signals")
        if isinstance(popular, dict)
        and isinstance(popular.get("rhetorical_signals"), dict)
        else rhetoric
    )
    profile_hash = _profile_hash(profile)
    corpus_hash = _require_string(source, "canonical_sha256")
    record_count = _require_int(source, "record_count")
    directives = _require_string_list(generation_skill, "directives")
    learned_directives = _require_optional_string_list(
        generation_skill,
        "learned_directives",
    )
    persona_lenses = _require_string_dict(generation_skill, "persona_lenses")
    persona_lens = persona_lenses.get(persona_id)
    if persona_lens is None:
        raise StyleProfileError(
            f"bundled 6657 generation skill has no lens for Persona {persona_id}"
        )

    return {
        "profile_id": f"sb6657-aggregate-v1-{corpus_hash[:12]}",
        "profile_hash": profile_hash,
        "generation_skill_hash": _require_string(
            generation_skill,
            "source_skill_sha256",
        ),
        "source": {
            "kind": "aggregate_style_statistics",
            "record_count": record_count,
            "corpus_sha256": corpus_hash,
            "popular_record_count": (
                _require_int(popular, "record_count")
                if isinstance(popular, dict)
                else record_count
            ),
            "popular_min_copy_count": (
                _require_int(popular, "min_copy_count")
                if isinstance(popular, dict)
                else 0
            ),
            "raw_examples_included": False,
        },
        "length_characters": {
            "preferred_min": _require_number(runtime_quantiles, "p25"),
            "median": _require_number(runtime_quantiles, "p50"),
            "preferred_max": _require_number(runtime_quantiles, "p75"),
            "soft_max": _require_number(runtime_quantiles, "p90"),
        },
        "signal_cadence": {
            "question": _require_number(popular_rates, "question_mark"),
            "exclamation": _require_number(popular_rates, "exclamation_mark"),
            "controlled_repetition": _require_number(popular_rates, "repetition"),
            "mention": _require_number(popular_rates, "mention"),
            "bracketed_aside": _require_number(popular_rhetoric, "bracketed_aside"),
            "imperative": _require_number(popular_rhetoric, "imperative_signal"),
        },
        "directives": [*directives, *learned_directives],
        "persona_lens": persona_lens,
    }


@cache
def _load_profile() -> dict[str, Any]:
    try:
        raw = (
            files("advx_backend.providers.model")
            .joinpath(_PROFILE_RESOURCE)
            .read_text(encoding="utf-8")
        )
        profile = json.loads(raw)
    except (OSError, TypeError, json.JSONDecodeError) as error:
        raise StyleProfileError(f"cannot load bundled 6657 style profile: {error}") from error
    if not isinstance(profile, dict) or profile.get("schema_version") != 1:
        raise StyleProfileError("bundled 6657 style profile has an unsupported schema")
    source = _require_dict(profile, "source")
    record_count = _require_int(source, "record_count")
    corpus_hash = _require_string(source, "canonical_sha256")
    if record_count < 10_000:
        raise StyleProfileError("bundled 6657 style profile is not based on the full corpus")
    if not _SHA256_PATTERN.fullmatch(corpus_hash):
        raise StyleProfileError("bundled 6657 corpus hash is invalid")
    _require_dict(_require_dict(profile, "length_characters"), "quantiles")
    _require_dict(profile, "rates")
    _require_dict(profile, "rhetorical_signals")
    return profile


@cache
def _load_generation_skill() -> dict[str, Any]:
    try:
        raw = (
            files("advx_backend.providers.model")
            .joinpath(_GENERATION_SKILL_RESOURCE)
            .read_text(encoding="utf-8")
        )
        skill = json.loads(raw)
    except (OSError, TypeError, json.JSONDecodeError) as error:
        raise StyleProfileError(f"cannot load bundled 6657 generation skill: {error}") from error
    if not isinstance(skill, dict) or skill.get("schema_version") != 1:
        raise StyleProfileError("bundled 6657 generation skill has an unsupported schema")
    if skill.get("mode_id") != ROOM_6657_MODE_ID:
        raise StyleProfileError("bundled 6657 generation skill has the wrong mode")
    source_hash = _require_string(skill, "source_skill_sha256")
    if not _SHA256_PATTERN.fullmatch(source_hash):
        raise StyleProfileError("bundled 6657 generation skill hash is invalid")
    _require_string_list(skill, "directives")
    _require_optional_string_list(skill, "learned_directives")
    persona_lenses = _require_string_dict(skill, "persona_lenses")
    if len(persona_lenses) != 13:
        raise StyleProfileError("bundled 6657 generation skill must define 13 Persona lenses")
    return skill


def _profile_hash(profile: dict[str, Any]) -> str:
    canonical = json.dumps(
        profile,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _require_dict(parent: object, field: str) -> dict[str, Any]:
    value = parent.get(field) if isinstance(parent, dict) else None
    if not isinstance(value, dict):
        raise StyleProfileError(f"6657 style profile field {field} must be an object")
    return value


def _require_string(parent: dict[str, Any], field: str) -> str:
    value = parent.get(field)
    if not isinstance(value, str) or not value:
        raise StyleProfileError(f"6657 style profile field {field} must be a string")
    return value


def _require_int(parent: dict[str, Any], field: str) -> int:
    value = parent.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise StyleProfileError(f"6657 style profile field {field} must be an integer")
    return value


def _require_number(parent: dict[str, Any], field: str) -> int | float:
    value = parent.get(field)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise StyleProfileError(f"6657 style profile field {field} must be numeric")
    return value


def _require_string_list(parent: dict[str, Any], field: str) -> list[str]:
    value = parent.get(field)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise StyleProfileError(
            f"6657 style profile field {field} must be a non-empty string array"
        )
    return list(value)


def _require_string_dict(parent: dict[str, Any], field: str) -> dict[str, str]:
    value = parent.get(field)
    if (
        not isinstance(value, dict)
        or not value
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(item, str)
            or not item.strip()
            for key, item in value.items()
        )
    ):
        raise StyleProfileError(
            f"6657 style profile field {field} must be a non-empty string map"
        )
    return dict(value)


def _require_optional_string_list(parent: dict[str, Any], field: str) -> list[str]:
    value = parent.get(field, [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise StyleProfileError(
            f"6657 style profile field {field} must be a string array"
        )
    return list(value)


__all__ = [
    "ROOM_6657_MODE_ID",
    "StyleProfileError",
    "style_guidance_for",
]
