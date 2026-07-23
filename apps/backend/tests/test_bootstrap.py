from advx_backend.bootstrap import LOCAL_TOKEN_ENV, build_runtime_from_environment


def test_runtime_reads_ephemeral_local_token_without_revealing_it(
    monkeypatch,
) -> None:
    token = "injected-local-token"
    monkeypatch.setenv(LOCAL_TOKEN_ENV, token)

    runtime = build_runtime_from_environment()

    assert runtime.local_token == token
    assert token not in repr(runtime)
