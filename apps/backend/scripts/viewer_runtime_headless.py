import asyncio
import json
import shutil
import sys
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from advx_backend.application.headless_harness import (
    EXIT_INVALID_INPUT,
    HeadlessHarness,
)


async def _run() -> int:
    try:
        payload: Any = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError
    except (json.JSONDecodeError, TypeError, ValueError):
        print(json.dumps({"ok": False, "error": {"code": "invalid_json"}}))
        return EXIT_INVALID_INPUT

    directory = Path(mkdtemp(prefix="advx-viewer-runtime-"))
    harness = HeadlessHarness(data_directory=directory)
    exit_code, response = await harness.execute(payload)
    if exit_code == 0:
        shutil.rmtree(directory)
        response["metadata"]["temporary_directory_cleaned"] = not directory.exists()
    else:
        response["metadata"]["temporary_directory_cleaned"] = False
    print(json.dumps(response, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
