#!/usr/bin/env python3
"""Produce aggregate style statistics and generation guidance from a corpus."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from sb6657_corpus_common import atomic_write_json, canonical_records, canonical_sha256

DEFAULT_DIRECTORY = Path(".advx-data") / "sb6657"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Profile an sb6657 JSONL corpus without emitting raw barrage examples."
    )
    parser.add_argument(
        "--input", type=Path, default=DEFAULT_DIRECTORY / "corpus.jsonl", help="Input JSONL corpus."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DIRECTORY / "profile.json",
        help="Aggregate profile JSON output.",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        help="Fetch metadata JSON (default: metadata.json beside --input; optional if absent).",
    )
    parser.add_argument("--self-test", action="store_true", help="Run a local fixture self-test.")
    return parser.parse_args(argv)


def read_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if (
                    not isinstance(value, dict)
                    or not isinstance(value.get("barrage"), str)
                    or not value["barrage"]
                ):
                    raise ValueError(
                        f"line {line_number}: expected object with non-empty string barrage"
                    )
                try:
                    count = int(value.get("cnt", 0))
                except (TypeError, ValueError) as error:
                    raise ValueError(f"line {line_number}: cnt must be integer-like") from error
                if count < 0:
                    raise ValueError(f"line {line_number}: cnt must be non-negative")
                normalized = dict(value)
                normalized["cnt"] = count
                records.append(normalized)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read corpus {path}: {error}") from error
    if not records:
        raise ValueError("corpus is empty")
    return canonical_records(records)


def quantiles(values: list[int]) -> dict[str, float | int]:
    ordered = sorted(values)
    result: dict[str, float | int] = {}
    for label, probability in (
        ("p10", 0.10),
        ("p25", 0.25),
        ("p50", 0.50),
        ("p75", 0.75),
        ("p90", 0.90),
        ("p95", 0.95),
        ("p99", 0.99),
    ):
        position = (len(ordered) - 1) * probability
        lower, upper = math.floor(position), math.ceil(position)
        value = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
        result[label] = int(value) if value.is_integer() else round(value, 3)
    return result


def ratio(texts: list[str], predicate: Callable[[str], bool]) -> float:
    return round(sum(bool(predicate(text)) for text in texts) / len(texts), 6)


def text_rates(texts: list[str]) -> dict[str, float]:
    total_characters = sum(map(len, texts))
    return {
        "short_le_5": ratio(texts, lambda text: len(text) <= 5),
        "short_le_10": ratio(texts, lambda text: len(text) <= 10),
        "short_le_20": ratio(texts, lambda text: len(text) <= 20),
        "question_mark": ratio(texts, lambda text: bool(re.search(r"[?？]", text))),
        "exclamation_mark": ratio(texts, lambda text: bool(re.search(r"[!！]", text))),
        "repetition": ratio(
            texts,
            lambda text: bool(re.search(r"(.{1,8})\1{1,}", text, flags=re.DOTALL)),
        ),
        "mention": ratio(texts, lambda text: bool(re.search(r"@[\w\u4e00-\u9fff]+", text))),
        "newline": ratio(texts, lambda text: "\n" in text or "\r" in text),
        "contains_ascii": ratio(texts, lambda text: bool(re.search(r"[\x00-\x7f]", text))),
        "ascii_only": ratio(
            texts, lambda text: bool(text) and all(ord(character) < 128 for character in text)
        ),
        "ascii_character": round(
            sum(ord(character) < 128 for text in texts for character in text) / total_characters,
            6,
        ),
    }


def rhetorical_signals(texts: list[str]) -> dict[str, float]:
    return {
        "rhetorical_question": ratio(
            texts,
            lambda text: bool(
                re.search(r"(难道|怎么会|凭什么|谁能|谁懂|不是吧|吗[?？]|呢[?？])", text)
            ),
        ),
        "ellipsis": ratio(texts, lambda text: "……" in text or "..." in text),
        "repeated_punctuation": ratio(
            texts, lambda text: bool(re.search(r"([!?！？。~～])\1+", text))
        ),
        "bracketed_aside": ratio(texts, lambda text: bool(re.search(r"[（(【\[].+?[）)】\]]", text))),
        "laughter_signal": ratio(
            texts, lambda text: bool(re.search(r"(哈哈|笑死|hhh+|lol+)", text, re.IGNORECASE))
        ),
        "imperative_signal": ratio(
            texts, lambda text: bool(re.search(r"(快|别|不要|给我|赶紧|必须|建议)", text))
        ),
    }


def load_metadata(path: Path, records: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read metadata {path}: {error}") from error
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")

    digest = canonical_sha256(records)
    if metadata.get("sha256") != digest:
        raise ValueError("metadata sha256 does not match the canonical corpus")
    if metadata.get("unique_count") != len(records):
        raise ValueError("metadata unique_count does not match the canonical corpus")
    if metadata.get("complete") is not True:
        raise ValueError("metadata complete must be true for a tuning profile")
    if metadata.get("termination_reason") != "last_page":
        raise ValueError("metadata termination_reason must be last_page for a tuning profile")
    if not isinstance(metadata.get("source_url"), str) or not metadata["source_url"]:
        raise ValueError("metadata source_url must be a non-empty string")
    if not isinstance(metadata.get("fetched_at_utc"), str) or not metadata["fetched_at_utc"]:
        raise ValueError("metadata fetched_at_utc must be a non-empty string")
    reported_total = metadata.get("reported_total")
    if not isinstance(reported_total, int) or isinstance(reported_total, bool) or reported_total < 0:
        raise ValueError("metadata reported_total must be a non-negative integer")
    fetched_count = metadata.get("fetched_count")
    if not isinstance(fetched_count, int) or isinstance(fetched_count, bool) or fetched_count < 0:
        raise ValueError("metadata fetched_count must be a non-negative integer")
    if fetched_count != reported_total:
        raise ValueError("metadata fetched_count must match reported_total")
    page_count = metadata.get("page_count")
    if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
        raise ValueError("metadata page_count must be a positive integer")
    observed_totals = metadata.get("observed_reported_totals")
    if (
        not isinstance(observed_totals, list)
        or not observed_totals
        or any(
            not isinstance(total, int) or isinstance(total, bool) or total < 0
            for total in observed_totals
        )
        or set(observed_totals) != {reported_total}
    ):
        raise ValueError("metadata observed totals must remain stable at reported_total")
    if not isinstance(metadata.get("request_header_policy"), dict):
        raise ValueError("metadata request_header_policy must be an object")
    return metadata


def build_profile(
    records: list[dict[str, Any]], metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    texts = [record["barrage"] for record in records]
    lengths = [len(text) for text in texts]
    counts = [record["cnt"] for record in records]
    count_quantiles = quantiles(counts)
    popular_threshold = count_quantiles["p75"]
    popular_records = [record for record in records if record["cnt"] >= popular_threshold]
    popular_texts = [record["barrage"] for record in popular_records]
    popular_lengths = [len(text) for text in popular_texts]
    tag_counts: Counter[str] = Counter()
    for record in records:
        tags = record.get("tags") or ""
        tag_counts.update(tag.strip() for tag in str(tags).split(",") if tag.strip())

    median_length = quantiles(lengths)["p50"]
    source = {
        "record_count": len(records),
        "canonical_sha256": canonical_sha256(records),
        "source_url": metadata["source_url"] if metadata else None,
        "fetched_at_utc": metadata["fetched_at_utc"] if metadata else None,
        "reported_total": metadata["reported_total"] if metadata else None,
        "fetched_count": metadata["fetched_count"] if metadata else None,
        "page_count": metadata["page_count"] if metadata else None,
        "termination_reason": metadata["termination_reason"] if metadata else None,
        "request_header_policy": metadata["request_header_policy"] if metadata else None,
    }
    return {
        "schema_version": 1,
        "source": source,
        "length_characters": {
            "minimum": min(lengths),
            "maximum": max(lengths),
            "mean": round(sum(lengths) / len(lengths), 3),
            "quantiles": quantiles(lengths),
        },
        "copy_count": {
            "minimum": min(counts),
            "maximum": max(counts),
            "mean": round(sum(counts) / len(counts), 3),
            "quantiles": count_quantiles,
        },
        "rates": text_rates(texts),
        "tag_distribution": {
            tag: {"count": count, "ratio": round(count / len(records), 6)}
            for tag, count in sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))
        },
        "rhetorical_signals": rhetorical_signals(texts),
        "popular_slice": {
            "record_count": len(popular_records),
            "min_copy_count": min(record["cnt"] for record in popular_records),
            "length_quantiles": quantiles(popular_lengths),
            "rates": text_rates(popular_texts),
            "rhetorical_signals": rhetorical_signals(popular_texts),
        },
        "generation_instructions": [
            f"默认保持短促，目标字符长度以中位数 {median_length} 为中心，并参考长度分位数。",
            "使用弹幕口吻直接回应当前画面或事件，避免解释背景、总结过程或写成完整文章。",
            "按统计概率混合问句、感叹、复读、@、换行与 ASCII 片段，不要每条都堆叠信号。",
            "标签仅用于采样分层；生成内容不得原样复述语料，也不得输出来源记录或标识符。",
            "一次生成多个候选时保持句式和情绪多样，过滤与输入语料完全相同的文本。",
        ],
    }


def self_test() -> None:
    fixture = [
        {"id": 1, "barrage": "短句?", "cnt": 2, "tags": "01,02", "submitTime": None},
        {"id": 2, "barrage": "哈哈哈哈！！", "cnt": 8, "tags": "02", "submitTime": None},
        {"id": 3, "barrage": "@某人 别急...", "cnt": 4, "tags": "", "submitTime": None},
    ]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        metadata_path = root / "metadata.json"
        output = Path(directory) / "profile.json"
        records = canonical_records(fixture)
        atomic_write_json(
            metadata_path,
            {
                "source_url": "https://example.invalid/machine/Page",
                "fetched_at_utc": "2026-01-01T00:00:00Z",
                "reported_total": 3,
                "observed_reported_totals": [3],
                "fetched_count": 3,
                "unique_count": 3,
                "page_count": 2,
                "sha256": canonical_sha256(records),
                "complete": True,
                "termination_reason": "last_page",
                "request_header_policy": {"site_attribution_headers_sent": False},
            },
        )
        metadata = load_metadata(metadata_path, records)
        profile = build_profile(records, metadata)
        atomic_write_json(output, profile)
        loaded = json.loads(output.read_text(encoding="utf-8"))
        assert loaded["source"]["record_count"] == 3
        assert loaded["rates"]["question_mark"] == round(1 / 3, 6)
        assert loaded["tag_distribution"]["02"]["count"] == 2
        assert loaded["source"]["reported_total"] == 3
        assert loaded["popular_slice"]["record_count"] == 1
        serialized = output.read_text(encoding="utf-8")
        assert all(item["barrage"] not in serialized for item in fixture)
        invalid_metadata = dict(metadata or {})
        invalid_metadata["sha256"] = "0" * 64
        atomic_write_json(metadata_path, invalid_metadata)
        try:
            load_metadata(metadata_path, records)
        except ValueError as error:
            assert "sha256" in str(error)
        else:
            raise AssertionError("mismatched metadata sha256 was accepted")
        invalid_metadata["sha256"] = canonical_sha256(records)
        invalid_metadata["fetched_count"] = 2
        atomic_write_json(metadata_path, invalid_metadata)
        try:
            load_metadata(metadata_path, records)
        except ValueError as error:
            assert "fetched_count" in str(error)
        else:
            raise AssertionError("truncated metadata was accepted")
    print("profile_sb6657_corpus self-test: OK")


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        records = read_records(args.input)
        metadata_path = args.metadata or args.input.with_name("metadata.json")
        metadata = load_metadata(metadata_path, records)
        profile = build_profile(records, metadata)
        atomic_write_json(args.output, profile)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        f"wrote aggregate profile for {profile['source']['record_count']} records to {args.output} "
        f"(canonical sha256 {profile['source']['canonical_sha256']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
