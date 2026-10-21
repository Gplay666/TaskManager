"""Применяет schema.sql к базе из DATABASE_URL (или к DSN из аргумента).

schema.sql идемпотентен, поэтому скрипт можно запускать повторно — он же
служит миграцией для базы, созданной до появления аккаунтов.

    python migrate.py
    python migrate.py postgresql://user:pass@host:5432/dbname
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg

import db

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def apply_schema(dsn: str | None = None) -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with psycopg.connect(dsn or db.DATABASE_URL) as conn:
        conn.execute(sql)


def _mask(dsn: str) -> str:
    parts = urlsplit(dsn)
    if parts.password:
        netloc = parts.netloc.replace(f":{parts.password}@", ":***@")
        parts = parts._replace(netloc=netloc)
    return urlunsplit(parts)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    dsn = argv[0] if argv else db.DATABASE_URL
    print(f"applying {SCHEMA_PATH.name} to {_mask(dsn)}")
    try:
        apply_schema(dsn)
    except psycopg.OperationalError as e:
        print(f"cannot connect: {e}", file=sys.stderr)
        return 1
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
