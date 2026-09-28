"""Доменный слой. API и другие фронты вызывают только это."""

from __future__ import annotations

from datetime import datetime, timedelta

import db
from graph import build_graph
from models import ScheduledTask, Task
from scheduler import Violation
from scheduler import autofill as _autofill
from scheduler import validate as _validate


class NotFound(Exception):
    """Запрошенного объекта нет."""


class BadRequest(Exception):
    """Некорректный запрос (например, цикл в зависимостях)."""


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------

def list_projects() -> list[tuple[int, str]]:
    return db.list_projects()


def get_project(pid: int) -> tuple[int, str]:
    p = db.get_project(pid)
    if p is None:
        raise NotFound(f"project {pid}")
    return p


def create_project(name: str) -> tuple[int, str]:
    if not name.strip():
        raise BadRequest("project name must not be empty")
    pid = db.create_project(name)
    return (pid, name)
def rename_project(pid: int, name: str) -> tuple[int, str]:
    get_project(pid)
    if not name.strip():
        raise BadRequest("project name must not be empty")
    db.update_project(pid, name)
    return (pid, name)


def delete_project(pid: int) -> None:
    get_project(pid)
    db.delete_project(pid)


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------

def load_tasks(pid: int) -> dict[int, Task]:
    get_project(pid)
    return db.load_tasks(pid)


def get_task(tid: int) -> Task:
    t = db.get_task(tid)
    if t is None:
        raise NotFound(f"task {tid}")
    return t


def create_task(pid: int,
                name: str,
                duration: timedelta,
                earliest_start: datetime | None,
                deadline: datetime | None,
                dependencies: set[int]) -> Task:
    get_project(pid)
    if not name.strip():
        raise BadRequest("task name must not be empty")
    if duration <= timedelta(0):
        raise BadRequest("duration must be positive")

    if dependencies:
        existing = db.load_tasks(pid)
        for d in dependencies:
            if d not in existing:
                raise BadRequest(
                    f"dependency task {d} not found in project {pid}"
                )

    tid = db.insert_task(pid, name, duration, earliest_start, deadline, dependencies)
    return db.get_task(tid)


def update_task(tid: int, fields: dict) -> Task:
    get_task(tid)
    if "duration" in fields and fields["duration"] is not None:
        if fields["duration"] <= timedelta(0):
            raise BadRequest("duration must be positive")
    if "name" in fields and fields["name"] is not None and not fields["name"].strip():
        raise BadRequest("task name must not be empty")
    db.update_task(tid, fields)
    return db.get_task(tid)


def delete_task(tid: int) -> None:
    get_task(tid)
    db.delete_task(tid)


# ---------------------------------------------------------------------------
# dependencies
# ---------------------------------------------------------------------------

def add_dependency(tid: int, dep_id: int) -> None:
    get_task(tid)
    get_task(dep_id)

    pid = db.get_task_project(tid)
    if db.get_task_project(dep_id) != pid:
        raise BadRequest("tasks belong to different projects")

    existing = db.load_tasks(pid)
    g = build_graph(existing)
    try:
        g.add_dependency(dep_id, tid)  # dep_id до tid
    except (ValueError, KeyError) as e:
        raise BadRequest(str(e)) from e

    db.add_dependency(tid, dep_id, pid)


def remove_dependency(tid: int, dep_id: int) -> None:
    get_task(tid)
    db.remove_dependency(tid, dep_id)


# ---------------------------------------------------------------------------
# schedule
# ---------------------------------------------------------------------------

def load_schedule(pid: int) -> dict[int, ScheduledTask]:
    get_project(pid)
    return db.load_schedule(pid)


def schedule_task(tid: int, start: datetime) -> ScheduledTask:
    task = get_task(tid)
    db.set_start(tid, start)
    return ScheduledTask.create(task, start)


def unschedule_task(tid: int) -> None:
    get_task(tid)
    db.unset_start(tid)


# ---------------------------------------------------------------------------
# bulk
# ---------------------------------------------------------------------------

def autofill_project(pid: int) -> dict[int, ScheduledTask]:
    tasks = load_tasks(pid)
    schedule = db.load_schedule(pid)
    try:
        new = _autofill(tasks, schedule)
    except ValueError as e:
        raise BadRequest(str(e)) from e
    for tid, st in new.items():
        if tid not in schedule:
            db.set_start(tid, st.start)
    return new


def validate_project(pid: int) -> list[Violation]:
    tasks = load_tasks(pid)
    schedule = db.load_schedule(pid)
    return _validate(tasks, schedule)