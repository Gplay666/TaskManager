from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class Task:
    id: int
    name: str
    earliest_start: datetime | None
    duration: timedelta
    deadline: datetime | None
    dependencies: set[int]


@dataclass(frozen=True)
class ScheduledTask:
    """Где задача стоит на таймлайне. Не хранится в БД — только start.

    end всегда согласован с start + task.duration, поэтому создавать
    объект нужно через ScheduledTask.create(task, start).
    """
    task_id: int
    start: datetime
    end: datetime

    @classmethod
    def create(cls, task: Task, start: datetime) -> "ScheduledTask":
        return cls(task.id, start, start + task.duration)