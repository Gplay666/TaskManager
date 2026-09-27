"""CLI-фронт. Общается с API по HTTP, рисует таймлайн локально.

Запуск сервера:
    uvicorn api:app --reload

Запуск CLI:
    python cli.py                      # интерактив с меню
    python cli.py show
    python cli.py place 2 11:00
    python cli.py autofill
    ...

URL API по умолчанию: http://localhost:8000
Переопределяется через TASK_PLANNER_API=...
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta

import httpx

import render
from models import ScheduledTask, Task


API_URL = os.environ.get("TASK_PLANNER_API", "http://localhost:8000")


# ---------------------------------------------------------------------------
# HTTP-клиент
# ---------------------------------------------------------------------------

class ApiError(Exception):
    pass


class Api:
    def __init__(self, url: str):
        self.client = httpx.Client(base_url=url, timeout=10.0)

    def _do(self, method: str, path: str, **kw):
        try:
            r = self.client.request(method, path, **kw)
        except httpx.RequestError as e:
            raise ApiError(f"cannot reach API at {self.client.base_url}: {e}") from e
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            raise ApiError(f"{r.status_code}: {detail}")
        return r.json() if r.content else None

    def get(self, path, **kw):     return self._do("GET", path, **kw)
    def post(self, path, **kw):    return self._do("POST", path, **kw)
    def put(self, path, **kw):     return self._do("PUT", path, **kw)
    def patch(self, path, **kw):   return self._do("PATCH", path, **kw)
    def delete(self, path, **kw):  return self._do("DELETE", path, **kw)


# ---------------------------------------------------------------------------
# (де)сериализация
# ---------------------------------------------------------------------------

def _task_from_json(d: dict) -> Task:
    return Task(
        id=d["id"],
        name=d["name"],
        earliest_start=datetime.fromisoformat(d["earliest_start"])
            if d["earliest_start"] else None,
        duration=timedelta(seconds=d["duration_seconds"]),
        deadline=datetime.fromisoformat(d["deadline"])
            if d["deadline"] else None,
        dependencies=set(d["dependencies"]),
    )


def _sched_from_json(d: dict) -> ScheduledTask:
    return ScheduledTask(
        task_id=d["task_id"],
        start=datetime.fromisoformat(d["start"]),
        end=datetime.fromisoformat(d["end"]),
    )


def _fetch_state(api: Api, pid: int) -> tuple[dict[int, Task], dict[int, ScheduledTask]]:
    raw_tasks = api.get(f"/projects/{pid}/tasks")
    raw_sched = api.get(f"/projects/{pid}/schedule")
    tasks = {d["id"]: _task_from_json(d) for d in raw_tasks}
    schedule = {d["task_id"]: _sched_from_json(d) for d in raw_sched}
    return tasks, schedule


# ---------------------------------------------------------------------------
# парсинг ввода
# ---------------------------------------------------------------------------

def _parse_time(s: str) -> datetime:
    """'HH:MM' → сегодня в это время; иначе — ISO 8601."""
    s = s.strip()
    if "T" in s or "-" in s:
        return datetime.fromisoformat(s)
    hh, mm = s.split(":")
    now = datetime.now().replace(second=0, microsecond=0)
    return now.replace(hour=int(hh), minute=int(mm))


def _fmt_duration(td: timedelta) -> str:
    total = int(td.total_seconds() // 60)
    h, m = divmod(total, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


# ---------------------------------------------------------------------------
# вывод
# ---------------------------------------------------------------------------

def _print_summary(tasks: dict[int, Task],
                   schedule: dict[int, ScheduledTask],
                   api: Api, pid: int,
                   minutes_per_char: int,
                   ascii_only: bool) -> None:
    print(render.timeline(tasks, schedule,
                          minutes_per_char=minutes_per_char,
                          ascii_only=ascii_only))

    unplaced = [tid for tid in sorted(tasks) if tid not in schedule]
    if unplaced:
        print()
        print("Not scheduled:")
        for tid in unplaced:
            t = tasks[tid]
            deps = ", ".join(str(d) for d in sorted(t.dependencies)) or "—"
            print(f"  [{tid}] {t.name}  ({_fmt_duration(t.duration)}, deps: {deps})")

    try:
        viols = api.get(f"/projects/{pid}/violations")
    except ApiError:
        viols = []
    if viols:
        print()
        print("Violations:")
        for v in viols:
            print(f"  ! [{v['task_id']}] {v['message']}")


def _pick(items: list[tuple[int, str]], prompt: str = "> ") -> int | None:
    if not items:
        return None
    for i, (_, label) in enumerate(items, 1):
        print(f"  [{i}] {label}")
    print("  [0] back")
    while True:
        raw = input(prompt).strip()
        if raw in ("", "0"):
            return None
        try:
            n = int(raw)
        except ValueError:
            print(f"  введи число 1..{len(items)} или 0")
            continue
        if 1 <= n <= len(items):
            return items[n - 1][0]
        print(f"  введи число 1..{len(items)} или 0")


# ---------------------------------------------------------------------------
# интерактив
# ---------------------------------------------------------------------------

def _menu_text(minutes_per_char: int, ascii_only: bool) -> None:
    print()
    print("[1]  Schedule / move a task")
    print("[2]  Unschedule a task")
    print("[3]  Auto-fill unscheduled")
    print("[4]  Create task")
    print("[5]  Delete task")
    print("[6]  Add dependency")
    print("[7]  Remove dependency")
    print("[8]  Check violations")
    print("[9]  Zoom in")
    print("[10] Zoom out")
    print(f"[11] ASCII mode: {'on' if ascii_only else 'off'}")
    print("[0]  Exit")


def _flow_place(api: Api, pid: int,
                tasks: dict[int, Task],
                schedule: dict[int, ScheduledTask]) -> None:
    items = []
    for tid in sorted(tasks):
        t = tasks[tid]
        if tid in schedule:
            st = schedule[tid]
            label = f"[{tid}] {t.name}  ({st.start:%H:%M}–{st.end:%H:%M})"
        else:
            deps = ", ".join(str(d) for d in sorted(t.dependencies)) or "—"
            label = f"[{tid}] {t.name}  ({_fmt_duration(t.duration)}, deps: {deps})"
        items.append((tid, label))

    print()
    print("Choose task:")
    tid = _pick(items, "task> ")
    if tid is None:
        return

    raw = input(f"start for [{tid}] {tasks[tid].name} (HH:MM or ISO): ").strip()
    if not raw:
        return
    try:
        start = _parse_time(raw)
    except ValueError:
        print(f"  bad time {raw!r}")
        return

    api.put(f"/tasks/{tid}/schedule", json={"start": start.isoformat()})
    print(f"  placed [{tid}] at {start:%Y-%m-%d %H:%M}")


def _flow_unplace(api: Api, pid: int,
                  tasks: dict[int, Task],
                  schedule: dict[int, ScheduledTask]) -> None:
    if not schedule:
        print("  nothing to unschedule")
        return
    items = [(tid, f"[{tid}] {tasks[tid].name}  "
                   f"({schedule[tid].start:%H:%M}–{schedule[tid].end:%H:%M})")
             for tid in sorted(schedule, key=lambda i: schedule[i].start)]
    print()
    print("Choose task to unschedule:")
    tid = _pick(items, "task> ")
    if tid is None:
        return
    api.delete(f"/tasks/{tid}/schedule")
    print(f"  unscheduled [{tid}]")


def _flow_create(api: Api, pid: int) -> None:
    name = input("name: ").strip()
    if not name:
        return
    raw = input("duration (e.g. 2h, 90m, 1h30m): ").strip()
    try:
        secs = _parse_duration(raw)
    except ValueError as e:
        print(f"  {e}")
        return
    raw = input("earliest_start (HH:MM / ISO / empty): ").strip()
    es = _parse_time(raw) if raw else None
    raw = input("deadline (HH:MM / ISO / empty): ").strip()
    dl = _parse_time(raw) if raw else None

    payload = {
        "name": name,
        "duration_seconds": secs,
        "earliest_start": es.isoformat() if es else None,
        "deadline": dl.isoformat() if dl else None,
        "dependencies": [],
    }
    t = api.post(f"/projects/{pid}/tasks", json=payload)
    print(f"  created task [{t['id']}] {t['name']}")


def _parse_duration(s: str) -> int:
    """'2h', '90m', '1h30m' → секунды."""
    s = s.strip().lower()
    if not s:
        raise ValueError("empty duration")
    total = 0
    num = ""
    for ch in s:
        if ch.isdigit():
            num += ch
        elif ch == "h" and num:
            total += int(num) * 3600; num = ""
        elif ch == "m" and num:
            total += int(num) * 60; num = ""
        elif ch == "s" and num:
            total += int(num); num = ""
        else:
            raise ValueError(f"bad duration {s!r}")
    if num:
        raise ValueError("missing unit (h/m/s)")
    if total <= 0:
        raise ValueError("duration must be positive")
    return total


def _flow_delete(api: Api, tasks: dict[int, Task]) -> None:
    items = [(tid, f"[{tid}] {tasks[tid].name}") for tid in sorted(tasks)]
    print()
    print("Choose task to delete:")
    tid = _pick(items, "task> ")
    if tid is None:
        return
    confirm = input(f"really delete [{tid}] {tasks[tid].name}? [y/N]: ").strip().lower()
    if confirm != "y":
        print("  cancelled")
        return
    api.delete(f"/tasks/{tid}")
    print(f"  deleted [{tid}]")


def _flow_add_dep(api: Api, tasks: dict[int, Task]) -> None:
    items = [(tid, f"[{tid}] {tasks[tid].name}") for tid in sorted(tasks)]
    print()
    print("Choose the task (that will depend on another):")
    tid = _pick(items, "task> ")
    if tid is None:
        return
    print("Choose the task it depends on:")
    dep = _pick(items, "dep> ")
    if dep is None or dep == tid:
        return
    api.post(f"/tasks/{tid}/dependencies", json={"depends_on": dep})
    print(f"  added: [{tid}] depends on [{dep}]")


def _flow_rm_dep(api: Api, tasks: dict[int, Task]) -> None:
    with_deps = [tid for tid in sorted(tasks) if tasks[tid].dependencies]
    if not with_deps:
        print("  no dependencies to remove")
        return
    items = [
        (tid, f"[{tid}] {tasks[tid].name}  →  "
              f"{', '.join(str(d) for d in sorted(tasks[tid].dependencies))}")
        for tid in with_deps
    ]
    print()
    print("Choose task whose dependency to remove:")
    tid = _pick(items, "task> ")
    if tid is None:
        return
    deps = sorted(tasks[tid].dependencies)
    dep_items = [(d, f"[{d}] {tasks[d].name}") for d in deps if d in tasks]
    print("Choose the dependency to remove:")
    dep = _pick(dep_items, "dep> ")
    if dep is None:
        return
    api.delete(f"/tasks/{tid}/dependencies/{dep}")
    print(f"  removed: [{tid}] no longer depends on [{dep}]")


def interactive(api: Api, pid: int,
                minutes_per_char: int = 10,
                ascii_only: bool = False) -> None:
    while True:
        try:
            tasks, schedule = _fetch_state(api, pid)
        except ApiError as e:
            print(f"error: {e}")
            return

        if sys.stdout.isatty():
            print("\033[2J\033[H", end="")

        _print_summary(tasks, schedule, api, pid, minutes_per_char, ascii_only)
        _menu_text(minutes_per_char, ascii_only)

        try:
            choice = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        try:
            if choice in ("", "0"):
                return
            elif choice == "1":
                _flow_place(api, pid, tasks, schedule)
            elif choice == "2":
                _flow_unplace(api, pid, tasks, schedule)
            elif choice == "3":
                api.post(f"/projects/{pid}/autofill")
                print("  autofilled")
            elif choice == "4":
                _flow_create(api, pid)
            elif choice == "5":
                _flow_delete(api, tasks)
            elif choice == "6":
                _flow_add_dep(api, tasks)
            elif choice == "7":
                _flow_rm_dep(api, tasks)
            elif choice == "8":
                continue  # покажется на след. итерации
            elif choice == "9":
                minutes_per_char = max(1, minutes_per_char // 2)
            elif choice == "10":
                minutes_per_char = min(240, minutes_per_char * 2)
            elif choice == "11":
                ascii_only = not ascii_only
            else:
                print(f"  unknown: {choice!r}")
        except ApiError as e:
            print(f"  error: {e}")
            input("press Enter...")


# ---------------------------------------------------------------------------
# одноразовые команды
# ---------------------------------------------------------------------------

def _oneshot(api: Api, argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="task-planner")
    parser.add_argument("--project", "-p", type=int, default=1)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_show = sub.add_parser("show")
    p_show.add_argument("--minutes-per-char", type=int, default=10)
    p_show.add_argument("--ascii", action="store_true")

    p_place = sub.add_parser("place")
    p_place.add_argument("id", type=int)
    p_place.add_argument("time")

    p_unplace = sub.add_parser("unplace")
    p_unplace.add_argument("id", type=int)

    sub.add_parser("autofill")
    sub.add_parser("check")
    sub.add_parser("tasks")

    args = parser.parse_args(argv)
    pid = args.project

    if args.cmd == "show":
        tasks, schedule = _fetch_state(api, pid)
        _print_summary(tasks, schedule, api, pid,
                       args.minutes_per_char, args.ascii)
        return 0

    if args.cmd == "tasks":
        tasks, schedule = _fetch_state(api, pid)
        for tid in sorted(tasks):
            t = tasks[tid]
            pos = (f"{schedule[tid].start:%H:%M}–{schedule[tid].end:%H:%M}"
                   if tid in schedule else "(unscheduled)")
            deps = ", ".join(str(d) for d in sorted(t.dependencies)) or "—"
            print(f"[{tid}] {t.name:<16} {pos:<20} "
                  f"{_fmt_duration(t.duration):<6} deps: {deps}")
        return 0

    if args.cmd == "place":
        start = _parse_time(args.time)
        api.put(f"/tasks/{args.id}/schedule",
                json={"start": start.isoformat()})
        print(f"placed [{args.id}] at {start:%Y-%m-%d %H:%M}")
        return 0

    if args.cmd == "unplace":
        api.delete(f"/tasks/{args.id}/schedule")
        print(f"unplaced [{args.id}]")
        return 0

    if args.cmd == "autofill":
        api.post(f"/projects/{pid}/autofill")
        print("autofilled")
        return 0

    if args.cmd == "check":
        viols = api.get(f"/projects/{pid}/violations")
        if not viols:
            print("OK: no violations")
            return 0
        for v in viols:
            print(f"! [{v['task_id']}] {v['message']}")
        return 1

    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    api = Api(API_URL)

    if not argv:
        try:
            interactive(api, pid=1)
        except ApiError as e:
            print(f"error: {e}")
            return 1
        return 0

    return _oneshot(api, argv)


if __name__ == "__main__":
    sys.exit(main())