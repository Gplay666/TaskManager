"""PostgreSQL-хранилище. Только SQL, никакой доменной логики.

Все «объектные» запросы либо принимают owner_id, либо используются сервисным
слоем после проверки владельца — фильтрация по пользователю обязательна.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import psycopg

from models import ScheduledTask, Task, User


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://taskmanager:taskmanager@localhost:5432/taskmanager",
)


def connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL)


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------

def create_user(username: str, password_hash: str) -> User:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, %s) "
            "RETURNING id",
            (username, password_hash),
        )
        return User(cur.fetchone()[0], username)


def get_user(uid: int) -> User | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, username FROM users WHERE id = %s", (uid,))
        row = cur.fetchone()
        return User(row[0], row[1]) if row else None


def get_user_credentials(username: str) -> tuple[User, str] | None:
    """(пользователь, password_hash) по логину без учёта регистра."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, username, password_hash FROM users "
            "WHERE lower(username) = lower(%s)",
            (username,),
        )
        row = cur.fetchone()
        return (User(row[0], row[1]), row[2]) if row else None


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------

def create_session(token_hash: str, uid: int, expires_at: datetime) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) "
            "VALUES (%s, %s, %s)",
            (token_hash, uid, expires_at),
        )


def get_session_user(token_hash: str) -> User | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.id, u.username
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = %s AND s.expires_at > now()
            """,
            (token_hash,),
        )
        row = cur.fetchone()
        return User(row[0], row[1]) if row else None


def delete_session(token_hash: str) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash,))


def delete_expired_sessions() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE expires_at <= now()")


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------

def list_projects(owner_id: int) -> list[tuple[int, str]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, name FROM projects WHERE owner_id = %s ORDER BY id",
            (owner_id,),
        )
        return list(cur.fetchall())


def get_project(pid: int, owner_id: int) -> tuple[int, str] | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, name FROM projects WHERE id = %s AND owner_id = %s",
            (pid, owner_id),
        )
        row = cur.fetchone()
        return tuple(row) if row else None


def create_project(name: str, owner_id: int) -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO projects (name, owner_id) VALUES (%s, %s) RETURNING id",
            (name, owner_id),
        )
        return cur.fetchone()[0]


def update_project(pid: int, name: str) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE projects SET name = %s WHERE id = %s", (name, pid))


def delete_project(pid: int) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM projects WHERE id = %s", (pid,))


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------

def load_tasks(pid: int) -> dict[int, Task]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, earliest_start, duration, deadline
            FROM tasks WHERE project_id = %s ORDER BY id
            """,
            (pid,),
        )
        rows = cur.fetchall()

        cur.execute(
            "SELECT task_id, depends_on FROM dependencies WHERE project_id = %s",
            (pid,),
        )
        dep_rows = cur.fetchall()

    deps: dict[int, set[int]] = {}
    for task_id, dep_id in dep_rows:
        deps.setdefault(task_id, set()).add(dep_id)

    return {
        tid: Task(tid, name, earliest, duration, deadline, deps.get(tid, set()))
        for tid, name, earliest, duration, deadline in rows
    }


def get_task(tid: int) -> Task | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, earliest_start, duration, deadline "
            "FROM tasks WHERE id = %s",
            (tid,),
        )
        row = cur.fetchone()
        if not row:
            return None
        tid, name, earliest, duration, deadline = row

        cur.execute(
            "SELECT depends_on FROM dependencies WHERE task_id = %s",
            (tid,),
        )
        deps = {r[0] for r in cur.fetchall()}

    return Task(tid, name, earliest, duration, deadline, deps)


def get_task_owned(tid: int, owner_id: int) -> Task | None:
    """Задача глазами владельца: чужие задачи неотличимы от несуществующих."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.id, t.name, t.earliest_start, t.duration, t.deadline
            FROM tasks t
            JOIN projects p ON p.id = t.project_id
            WHERE t.id = %s AND p.owner_id = %s
            """,
            (tid, owner_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        tid, name, earliest, duration, deadline = row

        cur.execute(
            "SELECT depends_on FROM dependencies WHERE task_id = %s",
            (tid,),
        )
        deps = {r[0] for r in cur.fetchall()}

    return Task(tid, name, earliest, duration, deadline, deps)


def get_task_project(tid: int) -> int | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT project_id FROM tasks WHERE id = %s", (tid,))
        row = cur.fetchone()
        return row[0] if row else None


def insert_task(pid: int,
                name: str,
                duration: timedelta,
                earliest_start: datetime | None,
                deadline: datetime | None,
                dependencies: set[int]) -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tasks (project_id, name, earliest_start, duration, deadline)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (pid, name, earliest_start, duration, deadline),
        )
        tid = cur.fetchone()[0]
        for dep_id in dependencies:
            cur.execute(
                "INSERT INTO dependencies (task_id, depends_on, project_id) "
                "VALUES (%s, %s, %s)",
                (tid, dep_id, pid),
            )
        return tid


def update_task(tid: int, fields: dict) -> None:
    allowed = ("name", "duration", "earliest_start", "deadline")
    sets: list[str] = []
    values: list = []
    for k in allowed:
        if k in fields:
            sets.append(f"{k} = %s")
            values.append(fields[k])
    if not sets:
        return
    values.append(tid)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = %s",
            values,
        )


def delete_task(tid: int) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM tasks WHERE id = %s", (tid,))


# ---------------------------------------------------------------------------
# dependencies
# ---------------------------------------------------------------------------

def add_dependency(tid: int, dep_id: int, pid: int) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO dependencies (task_id, depends_on, project_id) "
            "VALUES (%s, %s, %s)",
            (tid, dep_id, pid),
        )


def remove_dependency(tid: int, dep_id: int) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM dependencies WHERE task_id = %s AND depends_on = %s",
            (tid, dep_id),
        )


# ---------------------------------------------------------------------------
# schedule
# ---------------------------------------------------------------------------

def load_schedule(pid: int) -> dict[int, ScheduledTask]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.task_id, s.start_at, t.duration
            FROM schedule s
            JOIN tasks t ON t.id = s.task_id
            WHERE t.project_id = %s
            """,
            (pid,),
        )
        rows = cur.fetchall()
    return {
        tid: ScheduledTask(tid, start, start + duration)
        for tid, start, duration in rows
    }


def set_start(tid: int, start: datetime) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO schedule (task_id, start_at) VALUES (%s, %s)
            ON CONFLICT (task_id) DO UPDATE SET start_at = EXCLUDED.start_at
            """,
            (tid, start),
        )


def unset_start(tid: int) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM schedule WHERE task_id = %s", (tid,))
