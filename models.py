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


@dataclass
class ScheduledTask:
    task_id: int
    start: datetime
    end: datetime
