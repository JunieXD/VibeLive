from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = REPO_ROOT / "resources" / "skillopt" / "skillopt.lock.json"
TARGET_SKILL = REPO_ROOT / ".codex" / "skills" / "room-6657-style" / "SKILL.md"
TASKS_FILE = (
    REPO_ROOT / "tests" / "fixtures" / "room-6657" / "skillopt-reviewed-tasks.json"
)
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync_room_6657_skill.py"
GENERATED_SKILL = (
    REPO_ROOT
    / "apps"
    / "backend"
    / "src"
    / "advx_backend"
    / "providers"
    / "model"
    / "room_6657_generation_skill.json"
)
STAGING_ROOT = REPO_ROOT / ".skillopt-sleep" / "staging"
MUTATION_LOCK = REPO_ROOT / ".advx-data" / "locks" / "room-6657-skillopt.lock"
EXPECTED_HEADINGS = (
    "Runtime Directives",
    "Persona Lenses",
    "Output Contract",
    "Safety Boundary",
    "Optimization Contract",
)
LEARNED_HEADING = "Learned preferences & procedures"
REQUIRED_SAFETY_ANCHORS = (
    "current scene",
    "never reproduce source-corpus wording",
    "aggregate style evidence",
    "verbatim retrieval",
    "unsupported factual accusations",
)
PREFERENCES = (
    "Treat these as hard constraints: preserve every existing second-level heading "
    "and all 13 Persona identifiers; make at most two bounded edits; never add "
    "examples, source-corpus phrases, or stored response candidates; never modify "
    "memory, AGENTS.md, corpus data, generated runtime JSON, or production "
    "configuration; improve only scene relevance, persona separation, brevity, "
    "originality, and safety; preserve every Persona-specific length and behavior "
    "contract and never supersede one with a generic hard override; keep barrage "
    "language elliptical by requiring only the smallest distinguishing scene anchor, "
    "not a narrated causal chain; for hardmouth_antifan, a proposed rule must make "
    "the second clause change polarity through an explicit concessive turn and "
    "acknowledge one concrete merit rather than continue mockery; keep the target "
    "document in English."
)
MODEL_WORKSPACE_AGENTS = """# Isolated Skill Evaluation Workspace

Use only the task, skill text, rubric, and preferences supplied in the prompt.
Do not inspect files, run shell commands, invoke tools, or access paths outside
this workspace. Return only the response format requested by the prompt.
"""
SECOND_LEVEL_HEADING = re.compile(r"^## (.+?)\s*$", re.MULTILINE)
ENV_ALLOWLIST = (
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATH",
    "PATHEXT",
    "TEMP",
    "TMP",
    "APPDATA",
    "LOCALAPPDATA",
    "LANG",
    "LC_ALL",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NO_PROXY",
    "no_proxy",
)
PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


class SkillOptError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SkillOptError(f"cannot read JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise SkillOptError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise SkillOptError(f"cannot hash {path}: {error}") from error


def _lock() -> dict[str, Any]:
    value = _read_json(LOCK_PATH)
    required = ("repository", "commit", "local_path", "license")
    for field in required:
        if not isinstance(value.get(field), str) or not value[field]:
            raise SkillOptError(f"lock field {field} must be a non-empty string")
    commit = value["commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SkillOptError("lock commit must be a full lowercase Git SHA")
    if value["license"] != "MIT":
        raise SkillOptError("unexpected SkillOpt license in lock file")
    return value


def _tool_dir(lock: dict[str, Any]) -> Path:
    path = (REPO_ROOT / lock["local_path"]).resolve()
    private_root = (REPO_ROOT / ".advx-data" / "tools").resolve()
    if not path.is_relative_to(private_root):
        raise SkillOptError("locked tool path must stay under .advx-data/tools")
    return path


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        capture_output=capture,
    )


def _git(tool_dir: Path, *arguments: str, capture: bool = True) -> str:
    result = _run(["git", *arguments], cwd=tool_dir, capture=capture)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SkillOptError(f"git {' '.join(arguments)} failed: {detail}")
    return result.stdout.strip()


def bootstrap_tool() -> Path:
    lock = _lock()
    tool_dir = _tool_dir(lock)
    if not tool_dir.exists():
        tool_dir.parent.mkdir(parents=True, exist_ok=True)
        result = _run(
            ["git", "clone", lock["repository"], str(tool_dir)],
            cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            raise SkillOptError("failed to clone Microsoft SkillOpt")
    if not (tool_dir / ".git").is_dir():
        raise SkillOptError(f"tool path is not a Git checkout: {tool_dir}")
    if _git(tool_dir, "status", "--porcelain"):
        raise SkillOptError("SkillOpt checkout is dirty; refusing to replace local changes")
    head = _git(tool_dir, "rev-parse", "HEAD")
    if head != lock["commit"]:
        fetch = _run(["git", "fetch", "origin", lock["commit"]], cwd=tool_dir)
        if fetch.returncode != 0:
            raise SkillOptError(f"failed to fetch locked SkillOpt commit {lock['commit']}")
        checkout = _run(
            ["git", "checkout", "--detach", lock["commit"]],
            cwd=tool_dir,
        )
        if checkout.returncode != 0:
            raise SkillOptError(f"failed to checkout locked SkillOpt commit {lock['commit']}")
    verify_tool(tool_dir, lock)
    print(f"SkillOpt ready: {tool_dir} @ {lock['commit']}")
    return tool_dir


def verify_tool(tool_dir: Path, lock: dict[str, Any]) -> None:
    if _git(tool_dir, "rev-parse", "HEAD") != lock["commit"]:
        raise SkillOptError("SkillOpt checkout does not match the locked commit")
    if _git(tool_dir, "status", "--porcelain"):
        raise SkillOptError("SkillOpt checkout has local modifications")
    remote = _git(tool_dir, "remote", "get-url", "origin")
    normalized_remote = remote.removesuffix(".git").lower()
    normalized_expected = lock["repository"].removesuffix(".git").lower()
    if normalized_remote != normalized_expected:
        raise SkillOptError(f"unexpected SkillOpt origin: {remote}")
    if not (tool_dir / "LICENSE").is_file():
        raise SkillOptError("SkillOpt checkout is missing its MIT license file")
    if not (tool_dir / "skillopt_sleep" / "__main__.py").is_file():
        raise SkillOptError("SkillOpt-Sleep entrypoint is missing")


def _actual_codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".codex").resolve()


def _native_codex_from_wrapper(wrapper: Path) -> Path | None:
    module_root = (
        wrapper.resolve().parent
        / "node_modules"
        / "@openai"
        / "codex"
        / "node_modules"
    )
    candidates = sorted(
        module_root.glob("@openai/codex-win32-*/vendor/*/bin/codex.exe")
    )
    return candidates[0].resolve() if candidates else None


def _codex_path() -> str:
    if os.name == "nt":
        npm_wrapper = shutil.which("codex.cmd")
        if npm_wrapper:
            native = _native_codex_from_wrapper(Path(npm_wrapper))
            if native is not None:
                return str(native)
    resolved = shutil.which("codex")
    if resolved:
        return str(Path(resolved).resolve())
    raise SkillOptError("Codex CLI is not available on PATH")


def _prepare_isolated_codex_home(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    source = _actual_codex_home() / "auth.json"
    if source.is_file():
        shutil.copy2(source, destination / "auth.json")
    (destination / "config.toml").write_text(
        "prefer_websockets = false\n",
        encoding="utf-8",
    )


def _safe_loopback_proxy(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.username is None
        and parsed.password is None
    )


def _sanitized_env(home: Path, codex_home: Path) -> dict[str, str]:
    home.mkdir(parents=True, exist_ok=True)
    env = {key: os.environ[key] for key in ENV_ALLOWLIST if key in os.environ}
    for key in PROXY_ENV_KEYS:
        value = os.environ.get(key)
        if value and _safe_loopback_proxy(value):
            env[key] = value
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "CODEX_HOME": str(codex_home),
            "SKILLOPT_SLEEP_WORKERS": "2",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def _write_isolated_config(home: Path, project: Path) -> None:
    state_dir = home / ".skillopt-sleep"
    claude_home = home / ".claude"
    codex_transcripts = home / ".codex-transcripts"
    state_dir.mkdir(parents=True, exist_ok=True)
    claude_home.mkdir(parents=True, exist_ok=True)
    codex_transcripts.mkdir(parents=True, exist_ok=True)
    payload = {
        "projects": "invoked",
        "invoked_project": str(project),
        "claude_home": str(claude_home),
        "codex_home": str(codex_transcripts),
        "state_dir": str(state_dir),
        "evolve_memory": False,
        "evolve_skill": True,
        "llm_mine": False,
        "gate_mode": "on",
        "gate_metric": "mixed",
        "auto_adopt": False,
        "edit_budget": 2,
        "max_tasks_per_night": 12,
        "evidence_log": False,
        "redact_secrets": True,
        "preferences": PREFERENCES,
    }
    _write_json(state_dir / "config.json", payload)


@contextmanager
def _isolated_model_workspace() -> Iterator[tuple[Path, Path, Path]]:
    root = Path(tempfile.mkdtemp(prefix="advx-room-6657-skillopt-")).resolve()
    project = root / "project"
    home = root / "home"
    codex_home = root / "codex-home"
    project.mkdir(parents=True)
    home.mkdir(parents=True)
    (project / "AGENTS.md").write_text(MODEL_WORKSPACE_AGENTS, encoding="utf-8")
    _write_isolated_config(home, project)
    _prepare_isolated_codex_home(codex_home)
    try:
        yield project, home, codex_home
    finally:
        shutil.rmtree(root, ignore_errors=True)


@contextmanager
def _temporary_environment(env: dict[str, str]) -> Iterator[None]:
    original = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


@contextmanager
def _project_mutation_lock() -> Iterator[None]:
    MUTATION_LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            MUTATION_LOCK,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )
    except FileExistsError as error:
        raise SkillOptError(
            f"another room-6657 mutation is active or left a stale lock: {MUTATION_LOCK}"
        ) from error
    try:
        os.write(
            descriptor,
            json.dumps({"pid": os.getpid(), "created_at_utc": _utc_now()}).encode("utf-8"),
        )
        yield
    finally:
        os.close(descriptor)
        MUTATION_LOCK.unlink(missing_ok=True)


def validate_tasks() -> None:
    payload = _read_json(TASKS_FILE)
    if payload.get("format") != "skillopt_sleep.tasks.v1":
        raise SkillOptError("reviewed tasks use an unsupported format")
    if payload.get("reviewed") is not True:
        raise SkillOptError("reviewed tasks must keep reviewed=true")
    if payload.get("target_skill_path") != ".codex/skills/room-6657-style/SKILL.md":
        raise SkillOptError("reviewed tasks target the wrong skill")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 12:
        raise SkillOptError("reviewed tasks must contain exactly twelve bounded cases")
    seen: set[str] = set()
    splits = {"train": 0, "val": 0, "test": 0}
    for task in tasks:
        if not isinstance(task, dict):
            raise SkillOptError("every reviewed task must be an object")
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id or task_id in seen:
            raise SkillOptError("reviewed task IDs must be unique non-empty strings")
        seen.add(task_id)
        split = task.get("split")
        if split not in splits:
            raise SkillOptError(f"task {task_id} has an unsupported split")
        splits[split] += 1
        if task.get("origin") != "real":
            raise SkillOptError(f"task {task_id} must remain a reviewed real case")
        if task.get("reference_kind") != "rubric":
            raise SkillOptError(f"task {task_id} must use a semantic rubric")
        if task.get("source_sessions") != []:
            raise SkillOptError(f"task {task_id} must not reference private sessions")
        for field in ("intent", "context_excerpt", "reference"):
            if not isinstance(task.get(field), str) or not task[field].strip():
                raise SkillOptError(f"task {task_id} field {field} must be non-empty")
    if splits != {"train": 5, "val": 4, "test": 3}:
        raise SkillOptError(f"unexpected reviewed task split: {splits}")


def _heading_names(path: Path) -> tuple[str, ...]:
    return tuple(SECOND_LEVEL_HEADING.findall(path.read_text(encoding="utf-8")))


def validate_candidate(path: Path, *, reference_path: Path | None = None) -> None:
    if not path.is_file():
        raise SkillOptError(f"candidate skill does not exist: {path}")
    candidate_text = path.read_text(encoding="utf-8")
    headings = _heading_names(path)
    allowed_headings = (EXPECTED_HEADINGS, (*EXPECTED_HEADINGS, LEARNED_HEADING))
    if headings not in allowed_headings:
        raise SkillOptError(
            "candidate must preserve the second-level heading sequence, with only "
            "the managed SkillOpt heading allowed after it: "
            + ", ".join(EXPECTED_HEADINGS)
        )
    for anchor in REQUIRED_SAFETY_ANCHORS:
        if anchor not in candidate_text:
            raise SkillOptError(f"candidate removed required safety anchor: {anchor}")
    reference = reference_path or TARGET_SKILL
    if path.stat().st_size > int(reference.stat().st_size * 1.35):
        raise SkillOptError("candidate exceeds the 35% bounded-growth limit")
    with tempfile.TemporaryDirectory(prefix="room_6657_skill_") as directory:
        generated = Path(directory) / "room_6657_generation_skill.json"
        result = _run(
            [
                sys.executable,
                str(SYNC_SCRIPT),
                "--input",
                str(path),
                "--output",
                str(generated),
            ],
            cwd=REPO_ROOT,
            capture=True,
        )
        if result.returncode != 0:
            raise SkillOptError(
                "candidate does not compile: "
                + (result.stderr or result.stdout or "unknown error").strip()
            )
        payload = _read_json(generated)
        learned = payload.get("learned_directives")
        if not isinstance(learned, list):
            raise SkillOptError("candidate compiler did not emit learned directives")
        if len(learned) > 6:
            raise SkillOptError("candidate exceeds the six-directive retained-learning budget")
        if any(not isinstance(item, str) or len(item) > 500 for item in learned):
            raise SkillOptError("candidate has an oversized learned directive")
        if sum(len(item) for item in learned) > 1_200:
            raise SkillOptError("candidate learned directives exceed the 1200-character budget")


def validate_project() -> None:
    lock = _lock()
    tool_dir = _tool_dir(lock)
    if not tool_dir.exists():
        raise SkillOptError("SkillOpt is not downloaded; run bootstrap first")
    verify_tool(tool_dir, lock)
    validate_tasks()
    validate_candidate(TARGET_SKILL)
    result = _run(
        [sys.executable, str(SYNC_SCRIPT), "--check"],
        cwd=REPO_ROOT,
        capture=True,
    )
    if result.returncode != 0:
        raise SkillOptError(
            "generated runtime skill is stale: "
            + (result.stderr or result.stdout or "unknown error").strip()
        )
    print("room-6657 SkillOpt validation passed")


def _skillopt_command(
    action: str,
    *,
    backend: str,
    model: str,
    max_tasks: int,
    edit_budget: int,
    project: Path,
    home: Path,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "skillopt_sleep",
        action,
        "--project",
        str(project),
        "--claude-home",
        str(home / ".claude"),
        "--codex-home",
        str(home / ".codex-transcripts"),
        "--target-skill-path",
        str(TARGET_SKILL),
        "--json",
        "--backend",
        backend,
        "--tasks-file",
        str(TASKS_FILE),
        "--edit-budget",
        str(edit_budget),
        "--max-tasks",
        str(max_tasks),
        "--preferences",
        PREFERENCES,
        "--progress",
    ]
    if backend == "codex":
        command.extend(["--codex-path", _codex_path()])
    if model:
        command.extend(["--model", model])
    return command


def _unique_staging_destination(name: str) -> Path:
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    destination = STAGING_ROOT / name
    index = 2
    while destination.exists():
        destination = STAGING_ROOT / f"{name}-{index}"
        index += 1
    return destination


def _relocate_staging(project: Path) -> Path:
    source_root = project / ".skillopt-sleep" / "staging"
    candidates = (
        [path for path in source_root.iterdir() if (path / "manifest.json").is_file()]
        if source_root.is_dir()
        else []
    )
    if len(candidates) != 1:
        raise SkillOptError(
            f"expected exactly one isolated staging directory, found {len(candidates)}"
        )
    source = candidates[0]
    destination = _unique_staging_destination(source.name)
    shutil.move(str(source), str(destination))
    return destination


def _seal_staging(staging_dir: Path, baseline_sha256: str) -> dict[str, Any]:
    candidate = staging_dir / "proposed_SKILL.md"
    payload: dict[str, Any] = {
        "schema_version": 1,
        "baseline_skill_sha256": baseline_sha256,
        "candidate_skill_sha256": _sha256(candidate) if candidate.is_file() else None,
        "report_sha256": _sha256(staging_dir / "report.json"),
        "manifest_sha256": _sha256(staging_dir / "manifest.json"),
        "reviewed_tasks_sha256": _sha256(TASKS_FILE),
        "upstream_commit": _lock()["commit"],
        "sealed_at_utc": _utc_now(),
    }
    diagnostics = staging_dir / "diagnostics.json"
    if diagnostics.is_file():
        payload["diagnostics_sha256"] = _sha256(diagnostics)
    _write_json(staging_dir / "provenance.json", payload)
    return payload


def run_cycle(
    action: str,
    *,
    backend: str,
    model: str,
    max_tasks: int,
    edit_budget: int,
) -> int:
    validate_project()
    lock = _lock()
    tool_dir = _tool_dir(lock)
    baseline_sha256 = _sha256(TARGET_SKILL)
    with _isolated_model_workspace() as (project, home, codex_home):
        command = _skillopt_command(
            action,
            backend=backend,
            model=model,
            max_tasks=max_tasks,
            edit_budget=edit_budget,
            project=project,
            home=home,
        )
        result = _run(command, cwd=tool_dir, env=_sanitized_env(home, codex_home))
        if result.returncode != 0 or action == "dry-run":
            return result.returncode
        if _sha256(TARGET_SKILL) != baseline_sha256:
            raise SkillOptError("live skill changed during optimization; refusing to seal output")
        staging_dir = _relocate_staging(project)
        provenance = _seal_staging(staging_dir, baseline_sha256)
        print(
            json.dumps(
                {
                    "staging": str(staging_dir),
                    "candidate_sha256": provenance["candidate_skill_sha256"],
                    "provenance": str(staging_dir / "provenance.json"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def _latest_staging() -> Path | None:
    if not STAGING_ROOT.is_dir():
        return None
    candidates = [
        path for path in STAGING_ROOT.iterdir() if (path / "manifest.json").is_file()
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def show_status() -> int:
    validate_project()
    latest = _latest_staging()
    payload: dict[str, Any] = {
        "upstream_commit": _lock()["commit"],
        "live_skill_sha256": _sha256(TARGET_SKILL),
        "latest_staging": str(latest) if latest else None,
    }
    if latest:
        manifest = _read_json(latest / "manifest.json")
        payload["latest"] = {
            "accepted": manifest.get("accepted") is True,
            "candidate": (latest / "proposed_SKILL.md").is_file(),
            "provenance": (latest / "provenance.json").is_file(),
            "evaluation": (latest / "evaluation.json").is_file(),
            "review": (
                _read_json(latest / "review.json").get("status")
                if (latest / "review.json").is_file()
                else None
            ),
            "adopted": (latest / "adoption.json").is_file(),
            "rolled_back": (latest / "rollback.json").is_file(),
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _staging_dir(raw: str) -> Path:
    if not raw:
        raise SkillOptError("this action requires an explicit --staging path")
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    path = path.resolve()
    root = STAGING_ROOT.resolve()
    if not path.is_relative_to(root):
        raise SkillOptError("staging path must stay under .skillopt-sleep/staging")
    return path


def _validate_gate_report(staging_dir: Path) -> None:
    manifest = _read_json(staging_dir / "manifest.json")
    report = _read_json(staging_dir / "report.json")
    if manifest.get("accepted") is not True or report.get("accepted") is not True:
        raise SkillOptError("cannot use a proposal rejected by the held-out gate")
    edits = report.get("edits")
    if (
        not isinstance(edits, list)
        or not 1 <= len(edits) <= 2
        or any(
            not isinstance(edit, dict) or edit.get("target") != "skill" for edit in edits
        )
    ):
        raise SkillOptError("proposal violates the one-or-two skill-edit budget")
    if manifest.get("has_skill") is not True or manifest.get("has_memory") is not False:
        raise SkillOptError("proposal must contain only a skill update")
    live_path = Path(str(manifest.get("live_skill_path", ""))).resolve()
    if live_path != TARGET_SKILL.resolve():
        raise SkillOptError("proposal targets an unexpected live skill path")


def _verify_staging_binding(
    staging_dir: Path,
    *,
    require_live_baseline: bool,
) -> dict[str, Any]:
    provenance = _read_json(staging_dir / "provenance.json")
    if provenance.get("schema_version") != 1:
        raise SkillOptError("unsupported or missing staging provenance")
    expected = {
        "report_sha256": _sha256(staging_dir / "report.json"),
        "manifest_sha256": _sha256(staging_dir / "manifest.json"),
        "reviewed_tasks_sha256": _sha256(TASKS_FILE),
        "upstream_commit": _lock()["commit"],
    }
    for field, value in expected.items():
        if provenance.get(field) != value:
            raise SkillOptError(f"staging provenance mismatch: {field}")
    candidate_sha256 = provenance.get("candidate_skill_sha256")
    if not isinstance(candidate_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", candidate_sha256
    ):
        raise SkillOptError("staging provenance has no adoptable candidate hash")
    candidate = staging_dir / "proposed_SKILL.md"
    if _sha256(candidate) != candidate_sha256:
        raise SkillOptError("staged candidate bytes do not match provenance")
    baseline_sha256 = provenance.get("baseline_skill_sha256")
    if not isinstance(baseline_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", baseline_sha256
    ):
        raise SkillOptError("staging provenance has an invalid baseline hash")
    if require_live_baseline and _sha256(TARGET_SKILL) != baseline_sha256:
        raise SkillOptError("live skill no longer matches the staged baseline")
    _validate_gate_report(staging_dir)
    return provenance


def _test_items() -> list[dict[str, Any]]:
    payload = _read_json(TASKS_FILE)
    items = [
        item
        for item in payload.get("tasks", [])
        if isinstance(item, dict) and item.get("split") == "test"
    ]
    if len(items) != 3:
        raise SkillOptError("reviewed tasks must contain three final test cases")
    return items


def _staging_for_candidate(skill_path: Path) -> Path | None:
    if skill_path.resolve() == TARGET_SKILL.resolve():
        return None
    root = STAGING_ROOT.resolve()
    resolved = skill_path.resolve()
    if not resolved.is_relative_to(root) or resolved.name != "proposed_SKILL.md":
        raise SkillOptError("evaluation skill must be live or a staged proposed_SKILL.md")
    staging_dir = resolved.parent
    if staging_dir.parent != root:
        raise SkillOptError("candidate must be directly under one staging directory")
    return staging_dir


def evaluate_test(*, backend: str, model: str, skill: str) -> int:
    validate_project()
    lock = _lock()
    tool_dir = _tool_dir(lock)
    if str(tool_dir) not in sys.path:
        sys.path.insert(0, str(tool_dir))

    from skillopt_sleep.backend import build_backend
    from skillopt_sleep.replay import replay_batch
    from skillopt_sleep.types import TaskRecord

    skill_path = TARGET_SKILL
    if skill:
        skill_path = Path(skill)
        if not skill_path.is_absolute():
            skill_path = REPO_ROOT / skill_path
        skill_path = skill_path.resolve()
    staging_dir = _staging_for_candidate(skill_path)
    provenance = (
        _verify_staging_binding(staging_dir, require_live_baseline=True)
        if staging_dir
        else None
    )
    validate_candidate(skill_path)
    test_items = _test_items()
    with _isolated_model_workspace() as (project, home, codex_home):
        env = _sanitized_env(home, codex_home)
        with _temporary_environment(env):
            test_backend = build_backend(
                backend=backend,
                model=model,
                codex_path=_codex_path() if backend == "codex" else "",
                preferences=PREFERENCES,
                project_dir=str(project),
            )
            pairs = replay_batch(
                test_backend,
                [TaskRecord.from_dict(item) for item in test_items],
                skill_path.read_text(encoding="utf-8"),
                "",
            )
    results = [
        {
            "task_id": result.id,
            "hard": result.hard,
            "soft": result.soft,
            "response": result.response,
            "reason": result.fail_reason or result.judge_rationale,
            "passed": result.hard >= 1.0 and result.soft >= 0.8,
        }
        for _, result in pairs
    ]
    passed = all(item["passed"] for item in results)
    output: dict[str, Any] = {
        "backend": backend,
        "model": model,
        "skill": str(skill_path),
        "passed": passed,
        "results": results,
        "qualifying_evidence_written": False,
    }
    if staging_dir and backend == "codex" and provenance:
        evaluation = {
            "schema_version": 1,
            "candidate_skill_sha256": provenance["candidate_skill_sha256"],
            "reviewed_tasks_sha256": provenance["reviewed_tasks_sha256"],
            "upstream_commit": provenance["upstream_commit"],
            "provenance_sha256": _sha256(staging_dir / "provenance.json"),
            "backend": backend,
            "model": model,
            "passed": passed,
            "results": results,
            "evaluated_at_utc": _utc_now(),
        }
        with _project_mutation_lock():
            current = _verify_staging_binding(
                staging_dir,
                require_live_baseline=True,
            )
            if current["candidate_skill_sha256"] != evaluation["candidate_skill_sha256"]:
                raise SkillOptError("candidate changed while final evaluation was running")
            _write_json(staging_dir / "evaluation.json", evaluation)
        output["qualifying_evidence_written"] = True
        output["evaluation"] = str(staging_dir / "evaluation.json")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if passed else 1


def _validate_evaluation(
    staging_dir: Path,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    evaluation = _read_json(staging_dir / "evaluation.json")
    expected = {
        "schema_version": 1,
        "candidate_skill_sha256": provenance["candidate_skill_sha256"],
        "reviewed_tasks_sha256": provenance["reviewed_tasks_sha256"],
        "upstream_commit": provenance["upstream_commit"],
        "provenance_sha256": _sha256(staging_dir / "provenance.json"),
        "backend": "codex",
        "passed": True,
    }
    for field, value in expected.items():
        if evaluation.get(field) != value:
            raise SkillOptError(f"final evaluation is not adoptable: {field}")
    results = evaluation.get("results")
    expected_ids = {str(item["id"]) for item in _test_items()}
    if (
        not isinstance(results, list)
        or any(not isinstance(item, dict) for item in results)
        or {item.get("task_id") for item in results} != expected_ids
    ):
        raise SkillOptError("final evaluation does not cover the exact test split")
    for result in results:
        if (
            not isinstance(result, dict)
            or result.get("passed") is not True
            or not isinstance(result.get("hard"), (int, float))
            or not isinstance(result.get("soft"), (int, float))
            or result["hard"] < 1.0
            or result["soft"] < 0.8
        ):
            raise SkillOptError("final evaluation contains a failing test result")
    return evaluation


def _validate_approval(
    staging_dir: Path,
    provenance: dict[str, Any],
    evaluation: dict[str, Any],
) -> dict[str, Any]:
    review = _read_json(staging_dir / "review.json")
    expected = {
        "schema_version": 1,
        "status": "approved",
        "candidate_skill_sha256": provenance["candidate_skill_sha256"],
        "provenance_sha256": _sha256(staging_dir / "provenance.json"),
        "evaluation_sha256": _sha256(staging_dir / "evaluation.json"),
    }
    for field, value in expected.items():
        if review.get(field) != value:
            raise SkillOptError(f"project review is not adoptable: {field}")
    if not isinstance(review.get("reason"), str) or not review["reason"].strip():
        raise SkillOptError("approved review must include a reason")
    if evaluation.get("passed") is not True:
        raise SkillOptError("approved review references a failed evaluation")
    return review


def approve(staging: str, reason: str) -> int:
    with _project_mutation_lock():
        validate_project()
        staging_dir = _staging_dir(staging)
        if not reason.strip():
            raise SkillOptError("approval reason must not be empty")
        if (staging_dir / "adoption.json").exists():
            raise SkillOptError("proposal has already been adopted")
        existing_review = staging_dir / "review.json"
        if (
            existing_review.is_file()
            and _read_json(existing_review).get("status") == "rejected"
        ):
            raise SkillOptError("a rejected proposal cannot be approved; run a new optimization")
        provenance = _verify_staging_binding(staging_dir, require_live_baseline=True)
        _validate_evaluation(staging_dir, provenance)
        review = {
            "schema_version": 1,
            "status": "approved",
            "candidate_skill_sha256": provenance["candidate_skill_sha256"],
            "provenance_sha256": _sha256(staging_dir / "provenance.json"),
            "evaluation_sha256": _sha256(staging_dir / "evaluation.json"),
            "reviewed_at_utc": _utc_now(),
            "reason": reason.strip(),
        }
        _write_json(existing_review, review)
    print(f"approved staged proposal: {staging_dir}")
    return 0


def reject(staging: str, reason: str) -> int:
    with _project_mutation_lock():
        validate_project()
        staging_dir = _staging_dir(staging)
        if not reason.strip():
            raise SkillOptError("rejection reason must not be empty")
        if (staging_dir / "adoption.json").exists():
            raise SkillOptError("cannot reject a proposal after adoption")
        provenance = _verify_staging_binding(staging_dir, require_live_baseline=False)
        review = {
            "schema_version": 1,
            "status": "rejected",
            "candidate_skill_sha256": provenance["candidate_skill_sha256"],
            "provenance_sha256": _sha256(staging_dir / "provenance.json"),
            "reviewed_at_utc": _utc_now(),
            "reason": reason.strip(),
        }
        _write_json(staging_dir / "review.json", review)
    print(f"marked staged proposal as rejected: {staging_dir}")
    return 0


def _upstream_adopt(tool_dir: Path, staging_dir: Path) -> list[str]:
    if str(tool_dir) not in sys.path:
        sys.path.insert(0, str(tool_dir))
    from skillopt_sleep.staging import adopt as adopt_staging

    return adopt_staging(str(staging_dir))


def adopt(staging: str) -> int:
    with _project_mutation_lock():
        validate_project()
        staging_dir = _staging_dir(staging)
        if (staging_dir / "adoption.json").exists():
            raise SkillOptError("proposal has already been adopted")
        backup_dir = staging_dir / "backup"
        if backup_dir.exists():
            raise SkillOptError("staging already contains a backup; refusing to overwrite it")
        provenance = _verify_staging_binding(staging_dir, require_live_baseline=True)
        evaluation = _validate_evaluation(staging_dir, provenance)
        review = _validate_approval(staging_dir, provenance, evaluation)
        candidate = staging_dir / "proposed_SKILL.md"
        validate_candidate(candidate)

        original_skill = TARGET_SKILL.read_bytes()
        original_generated = GENERATED_SKILL.read_bytes()
        tool_dir = _tool_dir(_lock())
        try:
            if _sha256(TARGET_SKILL) != provenance["baseline_skill_sha256"]:
                raise SkillOptError("live skill changed immediately before adoption")
            _verify_staging_binding(staging_dir, require_live_baseline=True)
            _validate_approval(
                staging_dir,
                provenance,
                _validate_evaluation(staging_dir, provenance),
            )
            updated = [Path(path).resolve() for path in _upstream_adopt(tool_dir, staging_dir)]
            if updated != [TARGET_SKILL.resolve()]:
                raise SkillOptError(f"upstream adopt updated unexpected paths: {updated}")
            backup = backup_dir / "SKILL.md"
            if _sha256(backup) != provenance["baseline_skill_sha256"]:
                raise SkillOptError("upstream backup does not match the sealed baseline")
            if _sha256(TARGET_SKILL) != provenance["candidate_skill_sha256"]:
                raise SkillOptError("upstream adopt did not install the sealed candidate")
            sync = _run([sys.executable, str(SYNC_SCRIPT)], cwd=REPO_ROOT)
            if sync.returncode != 0:
                raise SkillOptError("adopted skill failed runtime synchronization")
            validate_project()
        except Exception:
            TARGET_SKILL.write_bytes(original_skill)
            GENERATED_SKILL.write_bytes(original_generated)
            shutil.rmtree(backup_dir, ignore_errors=True)
            raise

        adoption = {
            "schema_version": 1,
            "candidate_skill_sha256": provenance["candidate_skill_sha256"],
            "baseline_skill_sha256": provenance["baseline_skill_sha256"],
            "generated_runtime_sha256": _sha256(GENERATED_SKILL),
            "provenance_sha256": _sha256(staging_dir / "provenance.json"),
            "evaluation_sha256": _sha256(staging_dir / "evaluation.json"),
            "review_sha256": _sha256(staging_dir / "review.json"),
            "adopted_at_utc": _utc_now(),
            "review_reason": review["reason"],
        }
        _write_json(staging_dir / "adoption.json", adoption)
    print(f"adopted and synchronized reviewed proposal: {staging_dir}")
    return 0


def _validate_rollback_cas(
    staging_dir: Path,
    adoption: dict[str, Any],
    provenance: dict[str, Any],
    *,
    live_skill: Path,
    generated_skill: Path,
) -> Path:
    expected = {
        "candidate_skill_sha256": provenance["candidate_skill_sha256"],
        "baseline_skill_sha256": provenance["baseline_skill_sha256"],
        "provenance_sha256": _sha256(staging_dir / "provenance.json"),
    }
    for field, value in expected.items():
        if adoption.get(field) != value:
            raise SkillOptError(f"adoption record mismatch: {field}")
    if _sha256(live_skill) != adoption["candidate_skill_sha256"]:
        raise SkillOptError("live skill changed after adoption; refusing stale rollback")
    if _sha256(generated_skill) != adoption.get("generated_runtime_sha256"):
        raise SkillOptError("generated runtime changed after adoption; refusing stale rollback")
    backup = staging_dir / "backup" / "SKILL.md"
    if _sha256(backup) != adoption["baseline_skill_sha256"]:
        raise SkillOptError("rollback backup does not match the adopted baseline")
    return backup


def rollback(staging: str) -> int:
    with _project_mutation_lock():
        staging_dir = _staging_dir(staging)
        provenance = _verify_staging_binding(staging_dir, require_live_baseline=False)
        adoption = _read_json(staging_dir / "adoption.json")
        backup = _validate_rollback_cas(
            staging_dir,
            adoption,
            provenance,
            live_skill=TARGET_SKILL,
            generated_skill=GENERATED_SKILL,
        )
        validate_candidate(backup, reference_path=backup)
        current_skill = TARGET_SKILL.read_bytes()
        current_generated = GENERATED_SKILL.read_bytes()
        try:
            _validate_rollback_cas(
                staging_dir,
                adoption,
                provenance,
                live_skill=TARGET_SKILL,
                generated_skill=GENERATED_SKILL,
            )
            TARGET_SKILL.write_bytes(backup.read_bytes())
            sync = _run([sys.executable, str(SYNC_SCRIPT)], cwd=REPO_ROOT)
            if sync.returncode != 0:
                raise SkillOptError("rollback failed runtime synchronization")
            validate_project()
        except Exception:
            TARGET_SKILL.write_bytes(current_skill)
            GENERATED_SKILL.write_bytes(current_generated)
            raise
        rollback_record = {
            "schema_version": 1,
            "from_candidate_sha256": adoption["candidate_skill_sha256"],
            "to_baseline_sha256": adoption["baseline_skill_sha256"],
            "adoption_sha256": _sha256(staging_dir / "adoption.json"),
            "rolled_back_at_utc": _utc_now(),
        }
        _write_json(staging_dir / "rollback.json", rollback_record)
    print(f"rolled back skill from: {backup}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the review-gated SkillOpt loop for ADVX Live room-6657."
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("bootstrap", help="download and verify locked Microsoft SkillOpt")
    subparsers.add_parser("validate", help="validate tool, tasks, skill, and generated runtime")
    subparsers.add_parser("status", help="show local staging and review status")
    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="score untouched no-copy and Persona-contract tests without edits",
    )
    evaluate_parser.add_argument("--backend", choices=("mock", "codex"), default="codex")
    evaluate_parser.add_argument("--model", default="")
    evaluate_parser.add_argument("--skill", default="")

    for action in ("dry-run", "run"):
        subparser = subparsers.add_parser(action)
        subparser.add_argument(
            "--backend",
            choices=("mock", "codex"),
            default="mock" if action == "dry-run" else "codex",
        )
        subparser.add_argument("--model", default="")
        subparser.add_argument("--max-tasks", type=int, default=12)
        subparser.add_argument("--edit-budget", type=int, choices=(1, 2), default=2)

    approve_parser = subparsers.add_parser(
        "approve",
        help="bind an explicit project review to a passing final evaluation",
    )
    approve_parser.add_argument("--staging", required=True)
    approve_parser.add_argument("--reason", required=True)
    for action in ("adopt", "rollback"):
        subparser = subparsers.add_parser(action)
        subparser.add_argument("--staging", required=True)
    reject_parser = subparsers.add_parser(
        "reject",
        help="bind a project review rejection to a staged proposal",
    )
    reject_parser.add_argument("--staging", required=True)
    reject_parser.add_argument("--reason", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.action == "bootstrap":
            bootstrap_tool()
            return 0
        if args.action == "validate":
            validate_project()
            return 0
        if args.action == "status":
            return show_status()
        if args.action == "evaluate":
            return evaluate_test(backend=args.backend, model=args.model, skill=args.skill)
        if args.action in {"dry-run", "run"}:
            return run_cycle(
                args.action,
                backend=args.backend,
                model=args.model,
                max_tasks=args.max_tasks,
                edit_budget=args.edit_budget,
            )
        if args.action == "approve":
            return approve(args.staging, args.reason)
        if args.action == "adopt":
            return adopt(args.staging)
        if args.action == "reject":
            return reject(args.staging, args.reason)
        if args.action == "rollback":
            return rollback(args.staging)
        raise SkillOptError(f"unsupported action: {args.action}")
    except (OSError, SkillOptError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
