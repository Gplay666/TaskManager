-- Структура PostgreSQL. Файл идемпотентен: его можно применять повторно,
-- поэтому он же служит миграцией (см. migrate.py).

-- ---------------------------------------------------------------------------
-- пользователи и сессии
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT now()
);

-- Логин без учёта регистра: Alice и alice — один и тот же пользователь.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower
    ON users (lower(username));

-- Серверные сессии. Хранится хеш токена: утечка БД не даёт войти по чужим
-- сессиям, как и утечка password_hash не даёт узнать пароль.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    expires_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id    ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);

-- ---------------------------------------------------------------------------
-- проекты
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS projects (
    id       SERIAL PRIMARY KEY,
    owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    name     TEXT NOT NULL
);

-- Миграция старой схемы: projects.owner_id появился вместе с аккаунтами.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'projects'
          AND column_name = 'owner_id'
    ) THEN
        ALTER TABLE projects
            ADD COLUMN owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE;
    END IF;
END
$$;

-- owner_id = NULL означает «легаси-строка без владельца»: такие проекты
-- не видит никто. Обычные проекты всегда создаются с владельцем.
CREATE INDEX IF NOT EXISTS idx_projects_owner_id ON projects(owner_id);

-- ---------------------------------------------------------------------------
-- задачи
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,

    name TEXT NOT NULL,

    earliest_start TIMESTAMP,
    duration INTERVAL NOT NULL,
    deadline TIMESTAMP,

    CHECK (duration > INTERVAL '0'),

    UNIQUE (id, project_id)   -- цель составного FK из dependencies
);

-- Где задача стоит на таймлайне. Строки нет ⇒ задача не запланирована.
CREATE TABLE IF NOT EXISTS schedule (
    task_id  INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    start_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS dependencies (
    task_id    INTEGER NOT NULL,
    depends_on INTEGER NOT NULL,
    project_id INTEGER NOT NULL,

    PRIMARY KEY (task_id, depends_on),

    FOREIGN KEY (task_id, project_id)
        REFERENCES tasks (id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (depends_on, project_id)
        REFERENCES tasks (id, project_id) ON DELETE CASCADE,

    CHECK (task_id <> depends_on)
);

CREATE INDEX IF NOT EXISTS idx_dependencies_depends_on ON dependencies(depends_on);
