from typing import Any


def configure_sqlite_connection(
    dbapi_connection: Any,
    _connection_record: Any,
    *,
    busy_timeout_ms: int,
) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms:d}")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()
