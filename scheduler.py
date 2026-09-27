"""Логика валидации и (вспомогательного) автозаполнения расписания.

Программа — редактор, а не автошедулер. `validate` вызывается всегда и
показывает нарушения, но ничего не блокирует. `autofill` — отдельная
операция «разложи то, что ещё не поставлено»; существующие позиции не
трогает.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from models import ScheduledTask, Task


@dataclass
class Violation:
    task_id: int
    kind: str      # "earliest_start" | "deadline" | "dep_missing" | "dep_order" | "orphan"
    message: str

    def __str__(self) -> str:
        return f"[{self.task_id}] {self.message}"


def validate(tasks: dict[int, Task],
             schedule: dict[int, ScheduledTask]) -> list[Violation]:
    errors: list[Violation] = []
    for tid, st in schedule.items():
        task = tasks.get(tid)
        if task is None:
            errors.append(Violation(tid, "orphan", "scheduled but task missing"))
            continue

        if task.earliest_start and st.start < task.earliest_start:
            errors.append(Violation(
                tid, "earliest_start",
                f"{task.name}: start {st.start:%H:%M} "
                f"< earliest {task.earliest_start:%H:%M}",
            ))
        if task.deadline and st.end > task.deadline:
            errors.append(Violation(
                tid, "deadline",
                f"{task.name}: end {st.end:%H:%M} "
                f"> deadline {task.deadline:%H:%M}",
            ))
        for dep in task.dependencies:
            dep_st = schedule.get(dep)
            if dep_st is None:
                errors.append(Violation(
                    tid, "dep_missing",
                    f"{task.name}: dependency [{dep}] is not scheduled",
                ))
            elif dep_st.end > st.start:
                errors.append(Violation(
                    tid, "dep_order",
                    f"{task.name}: starts before dependency [{dep}] ends",
                ))
    return errors


def autofill(tasks: dict[int, Task],
             schedule: dict[int, ScheduledTask],
             base: datetime | None = None) -> dict[int, ScheduledTask]:
    """Дозаполнить задачи, которых нет в schedule. Существующие не трогает.

    Для каждой новой задачи старт = max(earliest_start, концы зависимостей).
    Возвращает новый dict, исходный не мутирует.
    """
    _check_dependencies_exist(tasks)

    result = dict(schedule)

    if base is None:
        if schedule:
            base = min(s.start for s in schedule.values())
        elif any(t.earliest_start for t in tasks.values()):
            base = min(t.earliest_start for t in tasks.values() if t.earliest_start)
        else:
            raise ValueError("cannot determine base time for autofill")

    for tid in _topo_order(tasks):
        if tid in result:
            continue
        task = tasks[tid]
        start = task.earliest_start or base

        placed_deps = [result[d] for d in task.dependencies if d in result]
        if placed_deps:
            latest_end = max(st.end for st in placed_deps)
            if latest_end > start:
                start = latest_end

        result[tid] = ScheduledTask.create(task, start)
    return result


def _check_dependencies_exist(tasks: dict[int, Task]) -> None:
    for tid, task in tasks.items():
        for d in task.dependencies:
            if d not in tasks:
                raise ValueError(
                    f"task {tid} ({task.name}) depends on unknown task {d}"
                )


def _topo_order(tasks: dict[int, Task]) -> list[int]:
    indeg = {tid: len(t.dependencies) for tid, t in tasks.items()}
    children: dict[int, set[int]] = {tid: set() for tid in tasks}
    for tid, task in tasks.items():
        for d in task.dependencies:
            # d гарантированно есть в tasks — см. _check_dependencies_exist
            children[d].add(tid)

    ready = [tid for tid, d in indeg.items() if d == 0]
    order: list[int] = []
    while ready:
        tid = ready.pop()
        order.append(tid)
        for c in children[tid]:
            indeg[c] -= 1
            if indeg[c] == 0:
                ready.append(c)
    if len(order) != len(tasks):
        # сюда мы попадаем только если в графе реальный цикл
        raise ValueError("cycle detected in task graph")
    return order