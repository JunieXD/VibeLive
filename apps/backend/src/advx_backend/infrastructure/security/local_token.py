import secrets


def create_local_token() -> str:
    return secrets.token_urlsafe(32)
