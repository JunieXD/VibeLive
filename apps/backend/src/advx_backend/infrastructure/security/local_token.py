import secrets


def create_local_token() -> str:
    return secrets.token_urlsafe(32)


def local_token_matches(expected: str, candidate: str | None) -> bool:
    if candidate is None:
        return False
    return secrets.compare_digest(expected, candidate)
