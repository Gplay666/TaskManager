"""PostgreSQL-хранилище. Только SQL, никакой доменной логики."""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import psycopg

from models import ScheduledTask, Task


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/task_planner",
)


def connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL)


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------

def list_projects() -> list[tuple[int, str]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name FROM projects ORDER BY id")
        return list(cur.fetchall())


def get_project(pid: int) -> tuple[int, str] | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name FROM projects WHERE id = %s", (pid,))
        row = cur.fetchone()
        return tuple(row) if row else None


def create_project(name: str) -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO projects (name) VALUES (%s) RETURNING id",
            (name,),
        )
        return cur.fetchone()[0]


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

def update_project(pid: int, name: str) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE projects SET name = %s WHERE id = %s", (name, pid))