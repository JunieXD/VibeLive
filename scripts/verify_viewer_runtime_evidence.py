import asyncio
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "e2e"))

from viewer_runtime_recorded_evidence import collect_evidence  # noqa: E402


async def _run() -> None:
    fixture = ROOT / "tests" / "fixtures" / "cs2" / "viewer_runtime_recorded.json"
    expected_path = (
        ROOT
        / "tests"
        / "e2e"
        / "cs2_viewer_runtime_recorded_evidence.json"
    )
    with TemporaryDirectory(prefix="advx-recorded-evidence-") as directory:
        evidence = await collect_evidence(fixture, data_directory=Path(directory))
    if os.environ.get("ADVX_UPDATE_EVIDENCE") == "1":
        expected_path.write_text(
            f"{json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True)}\n",
            encoding="utf-8",
        )
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    if evidence != expected:
        raise SystemExit("deterministic viewer-runtime evidence drifted")
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_run())
