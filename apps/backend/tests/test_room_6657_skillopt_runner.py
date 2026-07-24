from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = REPO_ROOT / "scripts" / "run_room_6657_skillopt.py"


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("room_6657_skillopt_runner", RUNNER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _sealed_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, dict[str, object], Path, Path]:
    target = tmp_path / "SKILL.md"
    target.write_text("baseline skill\n", encoding="utf-8")
    tasks = tmp_path / "tasks.json"
    _write_json(
        tasks,
        {
            "tasks": [
                {"id": "test-a", "split": "test"},
                {"id": "test-b", "split": "test"},
                {"id": "test-c", "split": "test"},
            ]
        },
    )
    lock = tmp_path / "skillopt.lock.json"
    _write_json(
        lock,
        {
            "repository": "https://github.com/microsoft/SkillOpt.git",
            "commit": "a" * 40,
            "local_path": ".advx-data/tools/SkillOpt",
            "license": "MIT",
        },
    )
    generated = tmp_path / "generated.json"
    generated.write_text('{"runtime": "baseline"}\n', encoding="utf-8")
    staging_root = tmp_path / ".skillopt-sleep" / "staging"
    staging = staging_root / "run"
    staging.mkdir(parents=True)
    candidate = staging / "proposed_SKILL.md"
    candidate.write_text("candidate skill\n", encoding="utf-8")
    _write_json(
        staging / "manifest.json",
        {
            "live_skill_path": str(target),
            "live_memory_path": "",
            "has_skill": True,
            "has_memory": False,
            "accepted": True,
        },
    )
    _write_json(
        staging / "report.json",
        {
            "accepted": True,
            "edits": [{"target": "skill", "op": "add", "content": "bounded rule"}],
        },
    )
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(runner, "TARGET_SKILL", target)
    monkeypatch.setattr(runner, "TASKS_FILE", tasks)
    monkeypatch.setattr(runner, "LOCK_PATH", lock)
    monkeypatch.setattr(runner, "GENERATED_SKILL", generated)
    monkeypatch.setattr(runner, "STAGING_ROOT", staging_root)
    monkeypatch.setattr(runner, "MUTATION_LOCK", tmp_path / "mutation.lock")
    provenance = runner._seal_staging(staging, runner._sha256(target))
    return staging, provenance, target, generated


def _write_passing_evaluation(
    staging: Path,
    provenance: dict[str, object],
) -> dict[str, object]:
    evaluation = {
        "schema_version": 1,
        "candidate_skill_sha256": provenance["candidate_skill_sha256"],
        "reviewed_tasks_sha256": provenance["reviewed_tasks_sha256"],
        "upstream_commit": provenance["upstream_commit"],
        "provenance_sha256": runner._sha256(staging / "provenance.json"),
        "backend": "codex",
        "model": "",
        "passed": True,
        "results": [
            {"task_id": task_id, "hard": 1.0, "soft": 0.9, "passed": True}
            for task_id in ("test-a", "test-b", "test-c")
        ],
    }
    _write_json(staging / "evaluation.json", evaluation)
    return evaluation


def test_native_codex_resolution_uses_binary_below_npm_wrapper(tmp_path: Path) -> None:
    wrapper = tmp_path / "npm" / "codex.cmd"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("@echo off\n", encoding="utf-8")
    native = (
        wrapper.parent
        / "node_modules"
        / "@openai"
        / "codex"
        / "node_modules"
        / "@openai"
        / "codex-win32-x64"
        / "vendor"
        / "x86_64-pc-windows-msvc"
        / "bin"
        / "codex.exe"
    )
    native.parent.mkdir(parents=True)
    native.write_bytes(b"binary")

    assert runner._native_codex_from_wrapper(wrapper) == native.resolve()


def test_sanitized_environment_excludes_provider_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "real-codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    monkeypatch.setenv("PYTHONPATH", "must-not-leak")
    monkeypatch.setenv("SKILLOPT_SLEEP_REPO", "must-not-leak")
    monkeypatch.setenv("HTTP_PROXY", "http://user:password@127.0.0.1:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")

    home = tmp_path / "isolated-home"
    isolated_codex_home = tmp_path / "isolated-codex-home"
    env = runner._sanitized_env(home, isolated_codex_home)

    assert env["HOME"] == str(home)
    assert env["USERPROFILE"] == str(home)
    assert env["CODEX_HOME"] == str(isolated_codex_home)
    assert env["SKILLOPT_SLEEP_WORKERS"] == "2"
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "PYTHONPATH" not in env
    assert "SKILLOPT_SLEEP_REPO" not in env
    assert "HTTP_PROXY" not in env
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:7890"


def test_isolated_model_project_starts_with_guard_file_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_codex_home = tmp_path / "source-codex-home"
    source_codex_home.mkdir()
    (source_codex_home / "auth.json").write_text('{"auth": true}\n', encoding="utf-8")
    (source_codex_home / "config.toml").write_text("[mcp]\n", encoding="utf-8")
    (source_codex_home / "archived_sessions").mkdir()
    monkeypatch.setattr(runner, "_actual_codex_home", lambda: source_codex_home)

    with runner._isolated_model_workspace() as (project, home, codex_home):
        root = project.parent
        assert [path.name for path in project.iterdir()] == ["AGENTS.md"]
        assert "Do not inspect files" in (project / "AGENTS.md").read_text(encoding="utf-8")
        config = json.loads(
            (home / ".skillopt-sleep" / "config.json").read_text(encoding="utf-8")
        )
        assert config["evolve_memory"] is False
        assert config["evidence_log"] is False
        assert str(REPO_ROOT) not in json.dumps(config)
        assert {path.name for path in codex_home.iterdir()} == {"auth.json", "config.toml"}
        assert (codex_home / "config.toml").read_text(encoding="utf-8") == (
            "prefer_websockets = false\n"
        )

    assert not root.exists()


@pytest.mark.parametrize(
    "artifact",
    ("proposed_SKILL.md", "report.json", "manifest.json"),
)
def test_staging_provenance_rejects_tampered_gate_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    artifact: str,
) -> None:
    staging, _, _, _ = _sealed_staging(monkeypatch, tmp_path)
    runner._verify_staging_binding(staging, require_live_baseline=True)
    path = staging / artifact
    path.write_bytes(path.read_bytes() + b"tampered")

    with pytest.raises(runner.SkillOptError):
        runner._verify_staging_binding(staging, require_live_baseline=True)


def test_rejected_review_cannot_satisfy_approval_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    staging, provenance, _, _ = _sealed_staging(monkeypatch, tmp_path)
    evaluation = _write_passing_evaluation(staging, provenance)
    runner._validate_evaluation(staging, provenance)
    _write_json(
        staging / "review.json",
        {
            "schema_version": 1,
            "status": "rejected",
            "candidate_skill_sha256": provenance["candidate_skill_sha256"],
            "provenance_sha256": runner._sha256(staging / "provenance.json"),
            "evaluation_sha256": runner._sha256(staging / "evaluation.json"),
            "reason": "unsafe Persona override",
        },
    )

    with pytest.raises(runner.SkillOptError, match="status"):
        runner._validate_approval(staging, provenance, evaluation)


def test_approval_is_bound_to_exact_evaluation_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    staging, provenance, _, _ = _sealed_staging(monkeypatch, tmp_path)
    evaluation = _write_passing_evaluation(staging, provenance)
    _write_json(
        staging / "review.json",
        {
            "schema_version": 1,
            "status": "approved",
            "candidate_skill_sha256": provenance["candidate_skill_sha256"],
            "provenance_sha256": runner._sha256(staging / "provenance.json"),
            "evaluation_sha256": runner._sha256(staging / "evaluation.json"),
            "reason": "all final cases preserve the target contracts",
        },
    )
    runner._validate_approval(staging, provenance, evaluation)
    (staging / "evaluation.json").write_bytes(
        (staging / "evaluation.json").read_bytes() + b" "
    )

    with pytest.raises(runner.SkillOptError, match="evaluation_sha256"):
        runner._validate_approval(staging, provenance, evaluation)


def test_rollback_compare_and_swap_rejects_newer_live_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    staging, provenance, target, generated = _sealed_staging(monkeypatch, tmp_path)
    baseline = target.read_bytes()
    backup = staging / "backup" / "SKILL.md"
    backup.parent.mkdir()
    backup.write_bytes(baseline)
    target.write_bytes((staging / "proposed_SKILL.md").read_bytes())
    adoption = {
        "candidate_skill_sha256": provenance["candidate_skill_sha256"],
        "baseline_skill_sha256": provenance["baseline_skill_sha256"],
        "generated_runtime_sha256": runner._sha256(generated),
        "provenance_sha256": runner._sha256(staging / "provenance.json"),
    }
    assert (
        runner._validate_rollback_cas(
            staging,
            adoption,
            provenance,
            live_skill=target,
            generated_skill=generated,
        )
        == backup
    )
    target.write_text("newer manual edit\n", encoding="utf-8")

    with pytest.raises(runner.SkillOptError, match="changed after adoption"):
        runner._validate_rollback_cas(
            staging,
            adoption,
            provenance,
            live_skill=target,
            generated_skill=generated,
        )


def test_staging_path_cannot_escape_project_staging_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    staging_root = tmp_path / ".skillopt-sleep" / "staging"
    staging_root.mkdir(parents=True)
    monkeypatch.setattr(runner, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(runner, "STAGING_ROOT", staging_root)

    with pytest.raises(runner.SkillOptError, match="must stay under"):
        runner._staging_dir(str(tmp_path.parent))


def test_approve_command_refuses_a_previously_rejected_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    staging, provenance, _, _ = _sealed_staging(monkeypatch, tmp_path)
    _write_passing_evaluation(staging, provenance)
    _write_json(
        staging / "review.json",
        {
            "schema_version": 1,
            "status": "rejected",
            "candidate_skill_sha256": provenance["candidate_skill_sha256"],
            "provenance_sha256": runner._sha256(staging / "provenance.json"),
            "reason": "Persona contract regression",
        },
    )
    monkeypatch.setattr(runner, "validate_project", lambda: None)

    with pytest.raises(runner.SkillOptError, match="cannot be approved"):
        runner.approve(str(staging), "changed our mind")


def test_adopt_command_requires_an_approved_review(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    staging, provenance, target, _ = _sealed_staging(monkeypatch, tmp_path)
    _write_passing_evaluation(staging, provenance)
    monkeypatch.setattr(runner, "validate_project", lambda: None)
    monkeypatch.setattr(runner, "validate_candidate", lambda *args, **kwargs: None)
    called = False

    def unexpected_adopt(*args: object, **kwargs: object) -> list[str]:
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(runner, "_upstream_adopt", unexpected_adopt)

    with pytest.raises(runner.SkillOptError, match="cannot read JSON"):
        runner.adopt(str(staging))

    assert called is False
    assert target.read_text(encoding="utf-8") == "baseline skill\n"


def test_adopt_failure_restores_live_and_generated_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    staging, provenance, target, generated = _sealed_staging(monkeypatch, tmp_path)
    _write_passing_evaluation(staging, provenance)
    monkeypatch.setattr(runner, "validate_project", lambda: None)
    monkeypatch.setattr(runner, "validate_candidate", lambda *args, **kwargs: None)
    runner.approve(str(staging), "final cases pass and the edit stays Persona-scoped")
    original_skill = target.read_bytes()
    original_generated = generated.read_bytes()

    def fake_upstream_adopt(tool_dir: Path, staging_dir: Path) -> list[str]:
        del tool_dir
        backup = staging_dir / "backup" / "SKILL.md"
        backup.parent.mkdir()
        backup.write_bytes(target.read_bytes())
        target.write_bytes((staging_dir / "proposed_SKILL.md").read_bytes())
        return [str(target)]

    def failing_sync(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        capture: bool = False,
    ) -> SimpleNamespace:
        del command, cwd, env, capture
        generated.write_text('{"runtime": "partial"}\n', encoding="utf-8")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(runner, "_upstream_adopt", fake_upstream_adopt)
    monkeypatch.setattr(runner, "_run", failing_sync)

    with pytest.raises(runner.SkillOptError, match="failed runtime synchronization"):
        runner.adopt(str(staging))

    assert target.read_bytes() == original_skill
    assert generated.read_bytes() == original_generated
    assert not (staging / "backup").exists()
    assert not (staging / "adoption.json").exists()


def test_adopt_then_rollback_command_rejects_newer_live_edit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    staging, provenance, target, generated = _sealed_staging(monkeypatch, tmp_path)
    _write_passing_evaluation(staging, provenance)
    monkeypatch.setattr(runner, "validate_project", lambda: None)
    monkeypatch.setattr(runner, "validate_candidate", lambda *args, **kwargs: None)
    runner.approve(str(staging), "candidate passed all final cases")

    def fake_upstream_adopt(tool_dir: Path, staging_dir: Path) -> list[str]:
        del tool_dir
        backup = staging_dir / "backup" / "SKILL.md"
        backup.parent.mkdir()
        backup.write_bytes(target.read_bytes())
        target.write_bytes((staging_dir / "proposed_SKILL.md").read_bytes())
        return [str(target)]

    def successful_sync(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        capture: bool = False,
    ) -> SimpleNamespace:
        del command, cwd, env, capture
        generated.write_text(
            json.dumps({"skill_sha256": runner._sha256(target)}) + "\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner, "_upstream_adopt", fake_upstream_adopt)
    monkeypatch.setattr(runner, "_run", successful_sync)
    runner.adopt(str(staging))
    assert (staging / "adoption.json").is_file()
    target.write_text("newer live edit\n", encoding="utf-8")

    with pytest.raises(runner.SkillOptError, match="changed after adoption"):
        runner.rollback(str(staging))

    assert target.read_text(encoding="utf-8") == "newer live edit\n"
    assert not (staging / "rollback.json").exists()
