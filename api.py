"""HTTP API. Тонкие хендлеры: распарсили → позвали services → отдали JSON.

Авторизация — серверные сессии в Postgres. Токен живёт в HttpOnly cookie
tm_session; клиент (браузер или CLI) шлёт его автоматически.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

import auth
import services
from models import ScheduledTask, Task, User

app = FastAPI(title="Task Planner API", version="0.3.0")

# Фронтенд отдаётся с того же origin (/), поэтому CORS по умолчанию выключен.
# Если UI открывают с другого адреса — перечисли origin'ы через запятую:
#   TASK_PLANNER_CORS_ORIGINS=http://localhost:5173
_cors_origins = [
    o.strip()
    for o in os.environ.get("TASK_PLANNER_CORS_ORIGINS", "").split(",")
    if o.strip()
]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Secure-cookie включается, когда API работает по HTTPS.
COOKIE_SECURE = os.environ.get("TASK_PLANNER_COOKIE_SECURE", "0") == "1"

BASE_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# отображение ошибок
# ---------------------------------------------------------------------------

@app.exception_handler(services.NotFound)
async def _not_found(_: Request, exc: services.NotFound):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(services.BadRequest)
async def _bad_request(_: Request, exc: services.BadRequest):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(services.Unauthorized)
async def _unauthorized(_: Request, exc: services.Unauthorized):
    return JSONResponse(status_code=401, content={"detail": str(exc)})


@app.exception_handler(services.Conflict)
async def _conflict(_: Request, exc: services.Conflict):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


# ---------------------------------------------------------------------------
# текущий пользователь
# ---------------------------------------------------------------------------

def current_user(request: Request) -> User:
    user = services.user_by_token(request.cookies.get(auth.SESSION_COOKIE))
    if user is None:
        raise services.Unauthorized("not signed in")
    return user


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=int(services.SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )


# ---------------------------------------------------------------------------
# схемы
# ---------------------------------------------------------------------------

class AuthIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str

    @classmethod
    def of(cls, u: User) -> "UserOut":
        return cls(id=u.id, username=u.username)


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
# auth
# ---------------------------------------------------------------------------

@app.post("/auth/register", response_model=UserOut,
          status_code=status.HTTP_201_CREATED)
def register(payload: AuthIn, response: Response):
    user, token = services.register(payload.username, payload.password)
    _set_session_cookie(response, token)
    return UserOut.of(user)


@app.post("/auth/login", response_model=UserOut)
def login(payload: AuthIn, response: Response):
    user, token = services.login(payload.username, payload.password)
    _set_session_cookie(response, token)
    return UserOut.of(user)


@app.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response):
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        services.logout(token)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")


@app.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return UserOut.of(user)


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True}


@app.get("/projects", response_model=list[ProjectOut])
def list_projects(user: User = Depends(current_user)):
    return [ProjectOut(id=pid, name=name)
            for pid, name in services.list_projects(user.id)]


@app.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectIn, user: User = Depends(current_user)):
    pid, name = services.create_project(user.id, payload.name)
    return ProjectOut(id=pid, name=name)


@app.get("/projects/{pid}", response_model=ProjectOut)
def get_project(pid: int, user: User = Depends(current_user)):
    pid, name = services.get_project(pid, user.id)
    return ProjectOut(id=pid, name=name)


@app.patch("/projects/{pid}", response_model=ProjectOut)
def patch_project(pid: int, payload: ProjectIn, user: User = Depends(current_user)):
    pid, name = services.rename_project(pid, user.id, payload.name)
    return ProjectOut(id=pid, name=name)


@app.delete("/projects/{pid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(pid: int, user: User = Depends(current_user)):
    services.delete_project(pid, user.id)


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------

@app.get("/projects/{pid}/tasks", response_model=list[TaskOut])
def list_tasks(pid: int, user: User = Depends(current_user)):
    return [TaskOut.of(t) for t in services.load_tasks(pid, user.id).values()]


@app.post("/projects/{pid}/tasks",
          response_model=TaskOut,
          status_code=status.HTTP_201_CREATED)
def create_task(pid: int, payload: TaskIn, user: User = Depends(current_user)):
    task = services.create_task(
        pid=pid,
        uid=user.id,
        name=payload.name,
        duration=timedelta(seconds=payload.duration_seconds),
        earliest_start=payload.earliest_start,
        deadline=payload.deadline,
        dependencies=set(payload.dependencies),
    )
    return TaskOut.of(task)


@app.get("/tasks/{tid}", response_model=TaskOut)
def get_task(tid: int, user: User = Depends(current_user)):
    return TaskOut.of(services.get_task(tid, user.id))


@app.patch("/tasks/{tid}", response_model=TaskOut)
def patch_task(tid: int, payload: TaskPatch, user: User = Depends(current_user)):
    fields = payload.model_dump(exclude_unset=True)
    if "duration_seconds" in fields:
        fields["duration"] = timedelta(seconds=fields.pop("duration_seconds"))
    return TaskOut.of(services.update_task(tid, user.id, fields))


@app.delete("/tasks/{tid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(tid: int, user: User = Depends(current_user)):
    services.delete_task(tid, user.id)


# ---------------------------------------------------------------------------
# dependencies
# ---------------------------------------------------------------------------

@app.post("/tasks/{tid}/dependencies", status_code=status.HTTP_204_NO_CONTENT)
def add_dependency(tid: int, payload: DependencyIn,
                   user: User = Depends(current_user)):
    services.add_dependency(tid, payload.depends_on, user.id)


@app.delete("/tasks/{tid}/dependencies/{dep_id}",
            status_code=status.HTTP_204_NO_CONTENT)
def remove_dependency(tid: int, dep_id: int, user: User = Depends(current_user)):
    services.remove_dependency(tid, dep_id, user.id)


# ---------------------------------------------------------------------------
# schedule
# ---------------------------------------------------------------------------

@app.get("/projects/{pid}/schedule", response_model=list[ScheduledTaskOut])
def get_schedule(pid: int, user: User = Depends(current_user)):
    return [ScheduledTaskOut.of(st)
            for st in services.load_schedule(pid, user.id).values()]


@app.put("/tasks/{tid}/schedule", response_model=ScheduledTaskOut)
def place_task(tid: int, payload: ScheduleIn, user: User = Depends(current_user)):
    return ScheduledTaskOut.of(services.schedule_task(tid, user.id, payload.start))


@app.delete("/tasks/{tid}/schedule", status_code=status.HTTP_204_NO_CONTENT)
def unplace_task(tid: int, user: User = Depends(current_user)):
    services.unschedule_task(tid, user.id)


@app.post("/projects/{pid}/autofill", response_model=list[ScheduledTaskOut])
def autofill(pid: int, user: User = Depends(current_user)):
    return [ScheduledTaskOut.of(st)
            for st in services.autofill_project(pid, user.id).values()]


@app.get("/projects/{pid}/violations", response_model=list[ViolationOut])
def violations(pid: int, user: User = Depends(current_user)):
    return [ViolationOut(task_id=v.task_id, kind=v.kind, message=v.message)
            for v in services.validate_project(pid, user.id)]


# ---------------------------------------------------------------------------
# фронтенд
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def index():
    # FIXME: на Python 3.14 uvicorn/h11 зависает/теряет тело, если отдавать
    # большие файлы через обычный Response/HTMLResponse/FileResponse —
    # уходят заголовки, а тело не отправляется (проверено: FileResponse
    # отдаёт 0 байт). StreamingResponse работает. Правильное решение —
    # откатиться на Python 3.12/3.13.
    def stream():
        with open(BASE_DIR / "index.html", "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(stream(), media_type="text/html; charset=utf-8")
