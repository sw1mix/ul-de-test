import os
import time
from datetime import date
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]


def split_sql(script: str) -> list[str]:
    statements: list[str] = []
    buf: list[str] = []
    inside_dollar = False
    for line in script.splitlines(keepends=True):
        buf.append(line)
        if line.count("$$") % 2 == 1:
            inside_dollar = not inside_dollar
        if not inside_dollar and line.strip().endswith(";"):
            statement = "".join(buf).strip()
            if _executable(statement):
                statements.append(statement)
            buf = []
    tail = "".join(buf).strip()
    if _executable(tail):
        statements.append(tail)
    return statements


def _executable(statement: str) -> bool:
    for line in statement.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return True
    return False


def connect(attempts: int = 30) -> psycopg.Connection:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return psycopg.connect(
                host=os.environ.get("PGHOST", "localhost"),
                port=os.environ.get("PGPORT", "5432"),
                user=os.environ.get("PGUSER", "deals"),
                password=os.environ.get("PGPASSWORD", "deals"),
                dbname=os.environ.get("PGDATABASE", "deals_bigdata"),
                connect_timeout=5,
            )
        except psycopg.OperationalError as exc:
            last_error = exc
            time.sleep(2)
            if attempt == attempts:
                break
    raise RuntimeError(f"postgres is not reachable: {last_error}")


def execute_script(conn: psycopg.Connection, path: Path) -> None:
    script = path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        for statement in split_sql(script):
            cur.execute(statement)


def read_date(name: str, default: str) -> date:
    raw = os.environ.get(name, default)
    return date.fromisoformat(raw)
