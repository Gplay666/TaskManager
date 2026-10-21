"""Доменный слой. API и другие фронты вызывают только это.

Каждая операция с проектами, задачами и расписанием выполняется от имени
пользователя (uid) и не видит чужие данные: чужая сущность — это NotFound.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import psycopg

import auth
import db
from graph import build_graph
from models import ScheduledTask, Task, User
from scheduler import Violation
from scheduler import autofill as _autofill
from scheduler import validate as _validate


class NotFound(Exception):
    """Запрошенного объекта нет (или он не принадлежит пользователю)."""


class BadRequest(Exception):
    """Некорректный запрос (например, цикл в зависимостях)."""


class Unauthorized(Exception):
    """Нет валидной сессии."""


class Conflict(Exception):
    """Нарушено ограничение уникальности (например, занятый логин)."""


SESSION_TTL = timedelta(days=30)
MIN_USERNAME_LENGTH = 3
MAX_USERNAME_LENGTH = 32
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


# ---------------------------------------------------------------------------
# аккаунты и сессии
# ---------------------------------------------------------------------------

def _validate_credentials(username: str, password: str) -> None:
    if not isinstance(username, str) or not username.strip():
        raise BadRequest("username must not be empty")
    if not (MIN_USERNAME_LENGTH <= len(username) <= MAX_USERNAME_LENGTH):
        raise BadRequest(
            f"username must be {MIN_USERNAME_LENGTH}..{MAX_USERNAME_LENGTH} characters"
        )
    if not _USERNAME_RE.match(username):
        raise BadRequest("username may contain only letters, digits, '_', '.' and '-'")
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise BadRequest(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise BadRequest(f"password must be at most {MAX_PASSWORD_LENGTH} characters")


def register(username: str, password: str) -> tuple[User, str]:
    """Создать аккаунт, его проект 'default' и сразу открыть сессию."""
    _validate_credentials(username, password)
    if db.get_user_credentials(username) is not None:
        raise Conflict("username already taken")
    try:
        user = db.create_user(username, auth.hash_password(password))
    except psycopg.errors.UniqueViolation as e:
        # гонка между проверкой и INSERT
        raise Conflict("username already taken") from e
    db.create_project("default", user.id)
    return user, _issue_session(user.id)


def login(username: str, password: str) -> tuple[User, str]:
    row = db.get_user_credentials(username)
    if row is None:
        auth.verify_password(password, auth.dummy_hash())  # выравниваем время ответа
        raise Unauthorized("invalid username or password")
    user, password_hash = row
    if not auth.verify_password(password, password_hash):
        raise Unauthorized("invalid username or password")
    db.delete_expired_sessions()
    return user, _issue_session(user.id)


def logout(token: str) -> None:
    db.delete_session(auth.token_hash(token))


def user_by_token(token: str | None) -> User | None:
    if not token:
        return None
    return db.get_session_user(auth.token_hash(token))


def _issue_session(uid: int) -> str:
    token = auth.new_token()
    db.create_session(auth.token_hash(token), uid, datetime.now() + SESSION_TTL)
    return token


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------

def list_projects(uid: int) -> list[tuple[int, str]]:
    return db.list_projects(uid)


def get_project(pid: int, uid: int) -> tuple[int, str]:
    p = db.get_project(pid, uid)
    if p is None:
        raise NotFound(f"project {pid}")
    return p


def create_project(uid: int, name: str) -> tuple[int, str]:
    if not name.strip():
        raise BadRequest("project name must not be empty")
    pid = db.create_project(name, uid)
    return (pid, name)


def rename_project(pid: int, uid: int, name: str) -> tuple[int, str]:
    get_project(pid, uid)
    if not name.strip():
        raise BadRequest("project name must not be empty")
    db.update_project(pid, name)
    return (pid, name)


def delete_project(pid: int, uid: int) -> None:
    get_project(pid, uid)
    db.delete_project(pid)


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------

def load_tasks(pid: int, uid: int) -> dict[int, Task]:
    get_project(pid, uid)
    return db.load_tasks(pid)


def get_task(tid: int, uid: int) -> Task:
    t = db.get_task_owned(tid, uid)
    if t is None:
        raise NotFound(f"task {tid}")
    return t


def create_task(pid: int,
                uid: int,
                name: str,
                duration: timedelta,
                earliest_start: datetime | None,
                deadline: datetime | None,
                dependencies: set[int]) -> Task:
    get_project(pid, uid)
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


def update_task(tid: int, uid: int, fields: dict) -> Task:
    get_task(tid, uid)
    if "duration" in fields and fields["duration"] is not None:
        if fields["duration"] <= timedelta(0):
            raise BadRequest("duration must be positive")
    if "name" in fields and fields["name"] is not None and not fields["name"].strip():
        raise BadRequest("task name must not be empty")
    db.update_task(tid, fields)
    return db.get_task(tid)


def delete_task(tid: int, uid: int) -> None:
    get_task(tid, uid)
    db.delete_task(tid)


# ---------------------------------------------------------------------------
# dependencies
# ---------------------------------------------------------------------------

def add_dependency(tid: int, dep_id: int, uid: int) -> None:
    get_task(tid, uid)
    get_task(dep_id, uid)

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


def remove_dependency(tid: int, dep_id: int, uid: int) -> None:
    get_task(tid, uid)
    db.remove_dependency(tid, dep_id)


# ---------------------------------------------------------------------------
# schedule
# ---------------------------------------------------------------------------

def load_schedule(pid: int, uid: int) -> dict[int, ScheduledTask]:
    get_project(pid, uid)
    return db.load_schedule(pid)


def schedule_task(tid: int, uid: int, start: datetime) -> ScheduledTask:
    task = get_task(tid, uid)
    db.set_start(tid, start)
    return ScheduledTask.create(task, start)


def unschedule_task(tid: int, uid: int) -> None:
    get_task(tid, uid)
    db.unset_start(tid)


# ---------------------------------------------------------------------------
# bulk
# ---------------------------------------------------------------------------

def autofill_project(pid: int, uid: int) -> dict[int, ScheduledTask]:
    tasks = load_tasks(pid, uid)
    schedule = db.load_schedule(pid)
    try:
        new = _autofill(tasks, schedule)
    except ValueError as e:
        raise BadRequest(str(e)) from e
    for tid, st in new.items():
        if tid not in schedule:
            db.set_start(tid, st.start)
    return new


def validate_project(pid: int, uid: int) -> list[Violation]:
    tasks = load_tasks(pid, uid)
    schedule = db.load_schedule(pid)
    return _validate(tasks, schedule)
