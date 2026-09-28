"""HTTP API. Тонкие хендлеры: распарсили → позвали services → отдали JSON."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import services
from models import ScheduledTask, Task


app = FastAPI(title="Task Planner API", version="0.2.0")

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],           # для локалки — ок; для прода сузь
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
# ---------------------------------------------------------------------------
# отображение ошибок
# ---------------------------------------------------------------------------

@app.exception_handler(services.NotFound)
async def _not_found(_: Request, exc: services.NotFound):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(services.BadRequest)
async def _bad_request(_: Request, exc: services.BadRequest):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ---------------------------------------------------------------------------
# схемы
# ---------------------------------------------------------------------------

class ProjectIn(BaseModel):
    name: str


class ProjectOut(BaseModel):
    id: int
    name: str


class TaskIn(BaseModel):
    name: str
    duration_seconds: int = Field(gt=0)
    earliest_start: datetime | None = None
    deadline: datetime | None = None
    dependencies: list[int] = Field(default_factory=list)


class TaskPatch(BaseModel):
    name: str | None = None
    duration_seconds: int | None = Field(default=None, gt=0)
    earliest_start: datetime | None = None
    deadline: datetime | None = None


class TaskOut(BaseModel):
    id: int
    name: str
    earliest_start: datetime | None
    duration_seconds: int
    deadline: datetime | None
    dependencies: list[int]

    @classmethod
    def of(cls, t: Task) -> "TaskOut":
        return cls(
            id=t.id,
            name=t.name,
            earliest_start=t.earliest_start,
            duration_seconds=int(t.duration.total_seconds()),
            deadline=t.deadline,
            dependencies=sorted(t.dependencies),
        )


class ScheduleIn(BaseModel):
    start: datetime


class ScheduledTaskOut(BaseModel):
    task_id: int
    start: datetime
    end: datetime

    @classmethod
    def of(cls, st: ScheduledTask) -> "ScheduledTaskOut":
        return cls(task_id=st.task_id, start=st.start, end=st.end)


class DependencyIn(BaseModel):
    depends_on: int


class ViolationOut(BaseModel):
    task_id: int
    kind: str
    message: str


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True}


@app.get("/projects", response_model=list[ProjectOut])
def list_projects():
    return [ProjectOut(id=pid, name=name)
            for pid, name in services.list_projects()]


@app.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectIn):
    pid, name = services.create_project(payload.name)
    return ProjectOut(id=pid, name=name)


@app.get("/projects/{pid}", response_model=ProjectOut)
def get_project(pid: int):
    pid, name = services.get_project(pid)
    return ProjectOut(id=pid, name=name)


@app.delete("/projects/{pid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(pid: int):
    services.delete_project(pid)


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------

@app.get("/projects/{pid}/tasks", response_model=list[TaskOut])
def list_tasks(pid: int):
    return [TaskOut.of(t) for t in services.load_tasks(pid).values()]


@app.post("/projects/{pid}/tasks",
          response_model=TaskOut,
          status_code=status.HTTP_201_CREATED)
def create_task(pid: int, payload: TaskIn):
    task = services.create_task(
        pid=pid,
        name=payload.name,
        duration=timedelta(seconds=payload.duration_seconds),
        earliest_start=payload.earliest_start,
        deadline=payload.deadline,
        dependencies=set(payload.dependencies),
    )
    return TaskOut.of(task)


@app.get("/tasks/{tid}", response_model=TaskOut)
def get_task(tid: int):
    return TaskOut.of(services.get_task(tid))


@app.patch("/tasks/{tid}", response_model=TaskOut)
def patch_task(tid: int, payload: TaskPatch):
    fields = payload.model_dump(exclude_unset=True)
    if "duration_seconds" in fields:
        fields["duration"] = timedelta(seconds=fields.pop("duration_seconds"))
    return TaskOut.of(services.update_task(tid, fields))


@app.delete("/tasks/{tid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(tid: int):
    services.delete_task(tid)


# ---------------------------------------------------------------------------
# dependencies
# ---------------------------------------------------------------------------

@app.post("/tasks/{tid}/dependencies", status_code=status.HTTP_204_NO_CONTENT)
def add_dependency(tid: int, payload: DependencyIn):
    services.add_dependency(tid, payload.depends_on)


@app.delete("/tasks/{tid}/dependencies/{dep_id}",
            status_code=status.HTTP_204_NO_CONTENT)
def remove_dependency(tid: int, dep_id: int):
    services.remove_dependency(tid, dep_id)


# ---------------------------------------------------------------------------
# schedule
# ---------------------------------------------------------------------------

@app.get("/projects/{pid}/schedule", response_model=list[ScheduledTaskOut])
def get_schedule(pid: int):
    return [ScheduledTaskOut.of(st)
            for st in services.load_schedule(pid).values()]


@app.put("/tasks/{tid}/schedule", response_model=ScheduledTaskOut)
def place_task(tid: int, payload: ScheduleIn):
    return ScheduledTaskOut.of(services.schedule_task(tid, payload.start))


@app.delete("/tasks/{tid}/schedule", status_code=status.HTTP_204_NO_CONTENT)
def unplace_task(tid: int):
    services.unschedule_task(tid)


@app.post("/projects/{pid}/autofill", response_model=list[ScheduledTaskOut])
def autofill(pid: int):
    return [ScheduledTaskOut.of(st)
            for st in services.autofill_project(pid).values()]


@app.get("/projects/{pid}/violations", response_model=list[ViolationOut])
def violations(pid: int):
    return [ViolationOut(task_id=v.task_id, kind=v.kind, message=v.message)
            for v in services.validate_project(pid)]