from pathlib import Path

import advx_backend.bootstrap as bootstrap


def test_desktop_managed_backend_can_disable_file_logging(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    runtime = object()

    monkeypatch.setenv(bootstrap.DATA_DIRECTORY_ENV, str(tmp_path))
    monkeypatch.setenv(bootstrap.FILE_LOGGING_ENV, "0")
    for name in (
        bootstrap.MODEL_BASE_URL_ENV,
        bootstrap.MODEL_NAME_ENV,
        bootstrap.MODEL_API_KEY_ENV,
        bootstrap.ASR_API_KEY_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        bootstrap,
        "configure_logging",
        lambda *, log_directory: captured.update(log_directory=log_directory),
    )
    monkeypatch.setattr(bootstrap, "build_runtime", lambda **_: runtime)

    assert bootstrap.build_runtime_from_environment() is runtime
    assert captured == {"log_directory": None}
