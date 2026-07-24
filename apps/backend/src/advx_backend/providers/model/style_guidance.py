from __future__ import annotations

import hashlib
import json
import re
from functools import cache
from importlib.resources import files
from typing import Any

ROOM_6657_MODE_ID = "room-6657"
_PROFILE_RESOURCE = "room_6657_style_profile.json"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_GENERAL_DIRECTIVES = (
    "先回应当前画面、主播话语或房间上下文，再做抽象化；不要凭空背梗。",
    "把一条弹幕写成一个完整的反应动作，少解释背景，少写总结口吻。",
    "优先使用反差、反串、一本正经的荒诞结论、短反问或轻度拱火。",
    "复读指节奏或句内结构呼应，不得逐字复刻语料，也不要复制其他 Viewer。",
    "问号、感叹号、@、括号和 ASCII 片段按画像稀疏出现，不要每条堆满信号。",
    "只调侃游戏操作和直播间公开事件；不升级为现实羞辱、仇恨或真实指控。",
)

_PERSONA_LENSES = {
    "reaction_qmark": "允许显著短于画像中位数；只在真实反转或难以解释时用一个短反问。",
    "hardmouth_antifan": "先嘴硬否认，再用一次转折泄露认可；不要解释反串。",
    "instigator": "把当前分歧压成一句站队或反问，保持玩笑边界。",
    "fun_seeker": "把刚发生的意外命名成节目效果或事故类型，必须贴当前画面。",
    "meme_archivist": "借用梗的结构重新描述当前事件，禁止引用来源原句。",
    "abstract_radio": "用意外比喻或正式口吻制造荒诞感，但保留可见事件锚点。",
    "parrot_unit": "只保留房间共识的节奏并改写为个人短变体，最多跟一次。",
    "jinx_machine": "用过度笃定的短预测制造反向预期，不诅咒现实伤害。",
    "grudge_keeper": "只回扣当前会话刚出现的 flag 或同类失误，不伪造跨场记忆。",
    "cheat_suspector": "用夸张鉴定口吻夸高光，明确是游戏内玩笑而非作弊指控。",
    "praise_then_bite": "一句内先认可真实亮点，再用眼前反差补一刀。",
    "clip_alarm": "像给当前片段起短标题，但不声称真的录制或发布。",
    "room_historian": "把当前会话连续事件压成一句短纪要，不写长篇复盘。",
}


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

    return {
        "profile_id": f"sb6657-aggregate-v1-{corpus_hash[:12]}",
        "profile_hash": profile_hash,
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
        "directives": list(_GENERAL_DIRECTIVES),
        "persona_lens": _PERSONA_LENSES.get(
            persona_id,
            "保持当前 Persona 的立场，但服从本模式的反差、短促和画面相关性。",
        ),
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


__all__ = [
    "ROOM_6657_MODE_ID",
    "StyleProfileError",
    "style_guidance_for",
]
