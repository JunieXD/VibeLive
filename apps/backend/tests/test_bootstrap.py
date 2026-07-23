from pathlib import Path

from advx_backend.bootstrap import (
    DATA_DIRECTORY_ENV,
    LOCAL_TOKEN_ENV,
    build_runtime_from_environment,
)


def test_runtime_reads_ephemeral_local_token_without_revealing_it(
    monkeypatch,
    tmp_path: Path,
) -> None:
    token = "injected-local-token"
    monkeypatch.setenv(LOCAL_TOKEN_ENV, token)
    monkeypatch.setenv(DATA_DIRECTORY_ENV, str(tmp_path))

    runtime = build_runtime_from_environment()

    assert runtime.local_token == token
    assert runtime.database.path == tmp_path / "advx.sqlite3"
    assert token not in repr(runtime)
