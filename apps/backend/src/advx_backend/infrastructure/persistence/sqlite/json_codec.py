import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class _VersionedObject(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_version: Literal[1] = 1
    value: dict[str, JsonValue]


class _VersionedTags(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    value: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(max_length=32)


def encode_object(value: dict[str, JsonValue]) -> str:
    payload = _VersionedObject(value=value)
    return json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def decode_object(value: str) -> dict[str, JsonValue]:
    return _VersionedObject.model_validate_json(value).value


def encode_tags(value: list[str]) -> str:
    payload = _VersionedTags(value=value)
    return json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def decode_tags(value: str) -> list[str]:
    return _VersionedTags.model_validate_json(value).value
