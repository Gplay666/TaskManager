# Task Planner

Учебный проект для планирования задач на временной шкале. Есть веб-интерфейс
(таймлайн с drag & drop), HTTP API и CLI.

## Идея

Задачи образуют ориентированный ациклический граф (DAG).

У каждой задачи есть:

- `id`
- `name`
- `earliest_start` — самое раннее допустимое начало
- `duration` — продолжительность
- `deadline` — крайний момент завершения
- `dependencies` — задачи, которые должны завершиться до этой задачи

Основные ограничения:

```text
earliest_start <= start
start + duration <= deadline
end(dependency) <= start(task)
```

Планировщик — редактор, а не автошедулер: нарушения показываются, но ничего
не блокируют. Автозаполнение (`autofill`) — отдельная операция, оно
расставляет только незапланированные задачи.

## Аккаунты и авторизация

- У каждого пользователя свои проекты и задачи; чужие данные не видны
  (чужая сущность отвечает `404`, как несуществующая).
- Регистрация открытая, при регистрации создаётся личный проект `default`.
- Аутентификация — серверные сессии в PostgreSQL:
  - пароль хранится как `scrypt`-хеш со случайной солью;
  - в БД лежит только `sha256` от токена сессии;
  - браузер получает токен в `HttpOnly` cookie `tm_session` (`SameSite=Lax`);
  - CLI хранит токен в `~/.config/task-planner/session.json` (chmod 600)
    и шлёт его заголовком `Cookie`.
- Сессия живёт 30 дней; `POST /auth/logout` удаляет её на сервере.

## Запуск

### 1. PostgreSQL

Нужна отдельная роль и база (пример команд; пароль/имя можно свои):

```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE taskmanager LOGIN PASSWORD 'taskmanager' CREATEDB;
CREATE DATABASE taskmanager OWNER taskmanager;
SQL
```

`CREATEDB` нужен только тестам, чтобы создавать `taskmanager_test`.
Если используется свой `DATABASE_URL`, роль создавать не обязательно.

### 2. Зависимости

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. Схема (идемпотентно, применяется и как миграция)

```bash
.venv/bin/python migrate.py
```

### 4. Сервер

```bash
.venv/bin/uvicorn api:app --port 8000
```

Веб-интерфейс: <http://localhost:8000/> — при первом заходе нужно
зарегистрироваться («Create account»).

Флаг `--reload` требует отдельной установки `pip install watchfiles`.

### 5. CLI

```bash
.venv/bin/python cli.py register        # создать аккаунт
.venv/bin/python cli.py login           # войти на другом устройстве
.venv/bin/python cli.py                 # интерактивное меню
.venv/bin/python cli.py show
.venv/bin/python cli.py projects
.venv/bin/python cli.py place 2 11:00
.venv/bin/python cli.py autofill
.venv/bin/python cli.py check
```

## Тесты

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Используется отдельная БД `taskmanager_test` (создаётся автоматически,
схема пересоздаётся на старте). Свою БД можно указать через
`TEST_DATABASE_URL`.

## Структура

```text
TaskManager/
├── main.py       # точка входа CLI (python main.py)
├── api.py        # FastAPI: HTTP API + раздача index.html
├── cli.py        # консольный клиент (httpx + серверная сессия)
├── services.py   # доменный слой: проверки, авторизация, владельцы
├── db.py         # PostgreSQL, только SQL
├── auth.py       # scrypt-хеши паролей и токены сессий (stdlib)
├── models.py     # простые модели данных
├── graph.py      # граф задач и защита от циклов
├── scheduler.py  # валидация и автозаполнение расписания
├── render.py     # ASCII-таймлайн для CLI
├── schema.sql    # структура PostgreSQL (идемпотентна)
├── migrate.py    # применение schema.sql
├── index.html    # веб-интерфейс (без сборки и фреймворков)
└── tests/        # тесты API (unittest + TestClient)
```

## Переменные окружения

| Переменная                 | По умолчанию                                              | Зачем                                |
| -------------------------- | --------------------------------------------------------- | ------------------------------------ |
| `DATABASE_URL`             | `postgresql://taskmanager:taskmanager@localhost:5432/taskmanager` | подключение к БД            |
| `TASK_PLANNER_API`         | `http://localhost:8000`                                   | адрес API для CLI                    |
| `TASK_PLANNER_SESSION`     | `~/.config/task-planner/session.json`                     | файл сессии CLI                      |
| `TASK_PLANNER_CORS_ORIGINS`| пусто (CORS выключен)                                     | origin'ы через запятую, если UI не с того же адреса |
| `TASK_PLANNER_COOKIE_SECURE` | `0`                                                     | `1` при работе по HTTPS              |

## API

Все `/projects`, `/tasks` и `/schedule` требуют сессию (cookie).

| Метод | Путь | Что делает |
| ----- | ---- | ---------- |
| `POST` | `/auth/register` | создать аккаунт `{username, password}` и войти |
| `POST` | `/auth/login` | войти |
| `POST` | `/auth/logout` | выйти (удалить сессию) |
| `GET` | `/auth/me` | текущий пользователь |
| `GET` | `/health` | проверка живости |
| `GET/POST` | `/projects` | список / создание |
| `GET/PATCH/DELETE` | `/projects/{pid}` | чтение / переименование / удаление |
| `GET/POST` | `/projects/{pid}/tasks` | задачи проекта |
| `GET/PATCH/DELETE` | `/tasks/{tid}` | конкретная задача |
| `POST` | `/tasks/{tid}/dependencies` | добавить зависимость `{depends_on}` |
| `DELETE` | `/tasks/{tid}/dependencies/{dep_id}` | убрать зависимость |
| `GET` | `/projects/{pid}/schedule` | расписание проекта |
| `PUT/DELETE` | `/tasks/{tid}/schedule` | поставить / снять задачу с таймлайна |
| `POST` | `/projects/{pid}/autofill` | расставить незапланированные задачи |
| `GET` | `/projects/{pid}/violations` | нарушения ограничений |

Регистрация: логин 3–32 символа (`A-Za-z0-9_.-`), пароль от 8 символов.

## Известные особенности

- На Python 3.14 uvicorn/h11 теряет тело большого ответа, если отдавать его
  через `Response`/`HTMLResponse`/`FileResponse` (`FileResponse` отдаёт
  0 байт). Поэтому `/` отдаёт `index.html` через `StreamingResponse`.
  Правильное лечение — Python 3.12/3.13.
