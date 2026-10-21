# Task Planner

A small project for planning tasks on a timeline: a web UI (drag-and-drop
timeline), an HTTP API, and a CLI.

## Concept

Tasks form a directed acyclic graph (DAG).

Each task has:

- `id`
- `name`
- `earliest_start` — the earliest allowed start
- `duration`
- `deadline` — the latest allowed finish
- `dependencies` — tasks that must finish before this one starts

Constraints:

```text
earliest_start <= start
start + duration <= deadline
end(dependency) <= start(task)
```

The planner is an editor, not an auto-scheduler: violations are shown but
never block anything. `autofill` is a separate operation that schedules only
the tasks that have no position yet.

## Accounts and authentication

- Every user owns their projects and tasks; other users' data is invisible
  (a foreign entity responds with `404`, as if it did not exist).
- Registration is open; a personal `default` project is created on sign-up.
- Authentication uses server-side sessions in PostgreSQL:
  - passwords are stored as scrypt hashes with a random salt;
  - only the SHA-256 of a session token is stored in the database;
  - the browser receives the token in an `HttpOnly` `tm_session` cookie
    (`SameSite=Lax`);
  - the CLI keeps the token in `~/.config/task-planner/session.json`
    (chmod 600) and sends it in the `Cookie` header.
- A session lives for 30 days; `POST /auth/logout` revokes it server-side.

## Running

### 1. PostgreSQL

Create a dedicated role and database (example — use your own credentials):

```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE taskmanager LOGIN PASSWORD 'taskmanager' CREATEDB;
CREATE DATABASE taskmanager OWNER taskmanager;
SQL
```

`CREATEDB` is only needed by the tests so they can create
`taskmanager_test`. If you provide your own `DATABASE_URL`, creating the
role is optional.

### 2. Dependencies

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. Schema (idempotent, also serves as a migration)

```bash
.venv/bin/python migrate.py
```

### 4. Server

```bash
.venv/bin/uvicorn api:app --port 8000
```

Web UI: <http://localhost:8000/> — register on the first visit
(“Create account”).

The `--reload` flag requires `pip install watchfiles`.

### 5. CLI

```bash
.venv/bin/python cli.py register        # create an account
.venv/bin/python cli.py login           # sign in on another machine
.venv/bin/python cli.py                 # interactive menu
.venv/bin/python cli.py show
.venv/bin/python cli.py projects
.venv/bin/python cli.py place 2 11:00
.venv/bin/python cli.py autofill
.venv/bin/python cli.py check
```

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Tests use a separate `taskmanager_test` database (created automatically;
the schema is recreated on startup). Override it with `TEST_DATABASE_URL`.

## Project layout

```text
TaskManager/
├── main.py       # CLI entry point (python main.py)
├── api.py        # FastAPI: HTTP API + serves index.html
├── cli.py        # console client (httpx + server-side session)
├── services.py   # domain layer: validation, auth, ownership
├── db.py         # PostgreSQL, SQL only
├── auth.py       # scrypt password hashes and session tokens (stdlib)
├── models.py     # simple data models
├── graph.py      # task graph and cycle detection
├── scheduler.py  # schedule validation and autofill
├── render.py     # ASCII timeline for the CLI
├── schema.sql    # PostgreSQL schema (idempotent)
├── migrate.py    # applies schema.sql
├── index.html    # web UI (no build step, no frameworks)
└── tests/        # API tests (unittest + TestClient)
```

## Environment variables

| Variable                    | Default                                                   | Purpose                              |
| --------------------------- | --------------------------------------------------------- | ------------------------------------ |
| `DATABASE_URL`              | `postgresql://taskmanager:taskmanager@localhost:5432/taskmanager` | database connection         |
| `TASK_PLANNER_API`          | `http://localhost:8000`                                   | API base URL for the CLI             |
| `TASK_PLANNER_SESSION`      | `~/.config/task-planner/session.json`                     | CLI session file                     |
| `TASK_PLANNER_CORS_ORIGINS` | empty (CORS disabled)                                     | comma-separated origins, if the UI is served from elsewhere |
| `TASK_PLANNER_COOKIE_SECURE`| `0`                                                       | `1` when running over HTTPS          |

## API

Every `/projects`, `/tasks` and `/schedule` endpoint requires a session
(cookie).

| Method | Path | Description |
| ------ | ---- | ----------- |
| `POST` | `/auth/register` | create an account `{username, password}` and sign in |
| `POST` | `/auth/login` | sign in |
| `POST` | `/auth/logout` | sign out (revoke the session) |
| `GET` | `/auth/me` | current user |
| `GET` | `/health` | liveness check |
| `GET/POST` | `/projects` | list / create |
| `GET/PATCH/DELETE` | `/projects/{pid}` | read / rename / delete |
| `GET/POST` | `/projects/{pid}/tasks` | project tasks |
| `GET/PATCH/DELETE` | `/tasks/{tid}` | a single task |
| `POST` | `/tasks/{tid}/dependencies` | add a dependency `{depends_on}` |
| `DELETE` | `/tasks/{tid}/dependencies/{dep_id}` | remove a dependency |
| `GET` | `/projects/{pid}/schedule` | project schedule |
| `PUT/DELETE` | `/tasks/{tid}/schedule` | place a task on the timeline / remove it |
| `POST` | `/projects/{pid}/autofill` | schedule all unplaced tasks |
| `GET` | `/projects/{pid}/violations` | constraint violations |

Registration rules: username is 3–32 characters (`A-Za-z0-9_.-`),
password is at least 8 characters.

## Known issues

- On Python 3.14, uvicorn/h11 drops the body of large responses served via
  `Response`/`HTMLResponse`/`FileResponse` (`FileResponse` returns 0 bytes).
  That is why `/` serves `index.html` through `StreamingResponse`.
  The proper fix is to use Python 3.12/3.13.
