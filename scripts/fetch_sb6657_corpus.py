#!/usr/bin/env python3
"""Fetch a deterministic, deduplicated sb6657 barrage corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from sb6657_corpus_common import atomic_write_bytes, atomic_write_json, canonical_records, jsonl_bytes

DEFAULT_ENDPOINT = "https://hguofichp.cn:10086/machine/Page"
DEFAULT_DIRECTORY = Path(".advx-data") / "sb6657"
FORBIDDEN_HEADERS = ("dpahjdoiaw", "siteToken")


class CorpusError(RuntimeError):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Politely fetch, validate, deduplicate, and atomically store sb6657 barrages."
    )
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="Pagination endpoint URL.")
    parser.add_argument("--page-size", type=int, default=100, help="Records per request (default: 100).")
    parser.add_argument("--max-pages", type=int, help="Optional cap for a partial/local run.")
    parser.add_argument("--delay", type=float, default=0.35, help="Seconds between requests.")
    parser.add_argument("--timeout", type=float, default=20.0, help="Request timeout in seconds.")
    parser.add_argument("--retries", type=int, default=4, help="Retries after the initial attempt.")
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_DIRECTORY / "corpus.jsonl", help="JSONL output path."
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=DEFAULT_DIRECTORY / "metadata.json",
        help="Metadata JSON output path.",
    )
    parser.add_argument(
        "--user-agent", default="ADVX-sb6657-corpus/1.0", help="Polite User-Agent value."
    )
    parser.add_argument("--self-test", action="store_true", help="Run a local HTTP self-test.")
    args = parser.parse_args(argv)
    if args.page_size < 1 or args.page_size > 1000:
        parser.error("--page-size must be between 1 and 1000")
    if args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pages must be positive")
    if args.delay < 0 or args.timeout <= 0 or args.retries < 0:
        parser.error("--delay must be non-negative; --timeout positive; --retries non-negative")
    return args


def fetch_page(
    endpoint: str,
    page_number: int,
    page_size: int,
    headers: dict[str, str],
    timeout: float,
    retries: int,
) -> dict[str, Any]:
    query = urllib.parse.urlencode({"pageNum": page_number, "pageSize": page_size})
    url = f"{endpoint}{'&' if '?' in endpoint else '?'}{query}"
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status != 200:
                    raise CorpusError(f"page {page_number}: HTTP {response.status}")
                payload = json.load(response)
            return validate_page(payload, page_number)
        except (CorpusError, json.JSONDecodeError, OSError, urllib.error.URLError) as error:
            if attempt >= retries:
                raise CorpusError(
                    f"page {page_number} failed after {retries + 1} attempts: {error}"
                ) from error
            time.sleep(min(8.0, 0.5 * (2**attempt)) + random.random() * 0.1)
    raise AssertionError("retry loop is exhaustive")


def validate_page(payload: Any, page_number: int) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise CorpusError(f"page {page_number}: expected response code 200")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise CorpusError(f"page {page_number}: data must be an object")
    items, total, last_page = data.get("list"), data.get("total"), data.get("lastPage")
    if not isinstance(items, list):
        raise CorpusError(f"page {page_number}: data.list must be an array")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise CorpusError(f"page {page_number}: data.total must be a non-negative integer")
    if not isinstance(last_page, bool):
        raise CorpusError(f"page {page_number}: data.lastPage must be boolean")
    return {
        "list": [validate_record(item, page_number, index) for index, item in enumerate(items)],
        "total": total,
        "lastPage": last_page,
    }


def validate_record(item: Any, page_number: int, index: int) -> dict[str, Any]:
    location = f"page {page_number} item {index}"
    if not isinstance(item, dict):
        raise CorpusError(f"{location}: record must be an object")
    barrage = item.get("barrage")
    if not isinstance(barrage, str) or not barrage.strip():
        raise CorpusError(f"{location}: barrage must be a non-empty string")
    identifier = item.get("id")
    if not isinstance(identifier, int) or isinstance(identifier, bool):
        raise CorpusError(f"{location}: id must be an integer")
    try:
        count = int(item.get("cnt"))
    except (TypeError, ValueError) as error:
        raise CorpusError(f"{location}: cnt must be integer-like") from error
    if count < 0:
        raise CorpusError(f"{location}: cnt must be non-negative")
    tags = item.get("tags")
    submitted = item.get("submitTime")
    if tags is not None and not isinstance(tags, str):
        raise CorpusError(f"{location}: tags must be a string or null")
    if submitted is not None and not isinstance(submitted, str):
        raise CorpusError(f"{location}: submitTime must be a string or null")
    return {
        "id": identifier,
        "barrage": barrage,
        "cnt": count,
        "tags": tags or "",
        "submitTime": submitted,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": args.user_agent}
    records: list[dict[str, Any]] = []
    reported_total: int | None = None
    observed_totals: set[int] = set()
    page_number = 1
    termination_reason = "unknown"
    while True:
        page = fetch_page(
            args.endpoint, page_number, args.page_size, headers, args.timeout, args.retries
        )
        if reported_total is None:
            reported_total = page["total"]
        observed_totals.add(page["total"])
        records.extend(page["list"])
        capped = args.max_pages is not None and page_number >= args.max_pages
        if page["lastPage"]:
            termination_reason = "last_page"
            break
        if not page["list"]:
            raise CorpusError(
                f"page {page_number}: received an empty page before lastPage=true"
            )
        if capped:
            termination_reason = "max_pages"
            break
        page_number += 1
        if args.delay:
            time.sleep(args.delay)

    canonical = canonical_records(records)
    content = jsonl_bytes(canonical)
    digest = hashlib.sha256(content).hexdigest()
    atomic_write_bytes(args.output, content)
    metadata = {
        "schema_version": 1,
        "source_url": args.endpoint,
        "fetched_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "page_size": args.page_size,
        "page_count": page_number,
        "reported_total": reported_total or 0,
        "observed_reported_totals": sorted(observed_totals),
        "fetched_count": len(records),
        "unique_count": len(canonical),
        "sha256": digest,
        "complete": termination_reason == "last_page",
        "termination_reason": termination_reason,
        "request_header_policy": {
            "sent": sorted(headers),
            "forbidden": list(FORBIDDEN_HEADERS),
            "site_attribution_headers_sent": False,
        },
    }
    atomic_write_json(args.metadata, metadata)
    return metadata


def self_test() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            page = int(query["pageNum"][0])
            empty_before_last = query.get("empty") == ["1"]
            items = [] if empty_before_last else {
                1: [
                    {"id": 2, "barrage": "重复！", "cnt": "2", "tags": "01", "submitTime": None},
                    {"id": 1, "barrage": "短句?", "cnt": "4", "tags": "02", "submitTime": None},
                ],
                2: [
                    {"id": 3, "barrage": "重复！", "cnt": "5", "tags": "03", "submitTime": None}
                ],
            }[page]
            body = json.dumps(
                {
                    "code": 200,
                    "data": {
                        "list": items,
                        "total": 3,
                        "lastPage": not empty_before_last and page == 2,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = parse_args(
                [
                    "--endpoint",
                    f"http://127.0.0.1:{server.server_port}/machine/Page",
                    "--page-size",
                    "2",
                    "--delay",
                    "0",
                    "--output",
                    str(root / "corpus.jsonl"),
                    "--metadata",
                    str(root / "metadata.json"),
                ]
            )
            metadata = run(args)
            assert metadata["fetched_count"] == 3 and metadata["unique_count"] == 2
            assert metadata["complete"] is True
            assert metadata["termination_reason"] == "last_page"
            records = [
                json.loads(line)
                for line in (root / "corpus.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            assert next(item for item in records if item["barrage"] == "重复！")["cnt"] == 5
            incomplete_args = parse_args(
                [
                    "--endpoint",
                    f"http://127.0.0.1:{server.server_port}/machine/Page?empty=1",
                    "--delay",
                    "0",
                    "--output",
                    str(root / "incomplete.jsonl"),
                    "--metadata",
                    str(root / "incomplete-metadata.json"),
                ]
            )
            try:
                run(incomplete_args)
            except CorpusError as error:
                assert "empty page before lastPage=true" in str(error)
            else:
                raise AssertionError("an empty non-terminal page was accepted")
    finally:
        server.shutdown()
        server.server_close()
    print("fetch_sb6657_corpus self-test: OK")


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        metadata = run(args)
    except CorpusError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        f"wrote {metadata['unique_count']} unique barrages to {args.output} "
        f"(sha256 {metadata['sha256']})"
    )
    print(f"wrote metadata to {args.metadata}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
