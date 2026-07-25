"""Compact visual signatures used only while selecting representative frames."""

from __future__ import annotations

import base64
import binascii
import re

VISUAL_SIGNATURE_WIDTH = 16
VISUAL_SIGNATURE_HEIGHT = 18
VISUAL_SIGNATURE_PIXEL_COUNT = VISUAL_SIGNATURE_WIDTH * VISUAL_SIGNATURE_HEIGHT
VISUAL_SIGNATURE_BYTES = VISUAL_SIGNATURE_PIXEL_COUNT // 2
VISUAL_SIGNATURE_ENCODED_LENGTH = 192
_VISUAL_SIGNATURE_PATTERN = re.compile(
    rf"[A-Za-z0-9_-]{{{VISUAL_SIGNATURE_ENCODED_LENGTH}}}"
)


def is_visual_signature(value: object) -> bool:
    return isinstance(value, bytes) and len(value) == VISUAL_SIGNATURE_BYTES


def validate_visual_signature(value: object) -> bytes:
    if not is_visual_signature(value):
        raise ValueError(
            f"visual_signature must be exactly {VISUAL_SIGNATURE_BYTES} bytes"
        )
    return value


def decode_visual_signature(value: str) -> bytes:
    if not _VISUAL_SIGNATURE_PATTERN.fullmatch(value):
        raise ValueError("visual signature must be canonical base64url")
    try:
        signature = base64.urlsafe_b64decode(value.encode("ascii"))
    except (ValueError, binascii.Error) as error:
        raise ValueError("visual signature must be canonical base64url") from error
    validate_visual_signature(signature)
    if encode_visual_signature(signature) != value:
        raise ValueError("visual signature must be canonical base64url")
    return signature


def encode_visual_signature(value: bytes) -> str:
    signature = validate_visual_signature(value)
    return base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")


def visual_signature_change_score(anchor: bytes, current: bytes) -> float:
    """Return the normalized grayscale delta between two packed 4-bit signatures."""

    anchor_signature = validate_visual_signature(anchor)
    current_signature = validate_visual_signature(current)
    difference = sum(
        abs((anchor_byte >> 4) - (current_byte >> 4))
        + abs((anchor_byte & 0x0F) - (current_byte & 0x0F))
        for anchor_byte, current_byte in zip(anchor_signature, current_signature, strict=True)
    )
    return difference / (VISUAL_SIGNATURE_PIXEL_COUNT * 15)
