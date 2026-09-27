CREATE TABLE projects (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE tasks (
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
CREATE TABLE schedule (
    task_id  INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    start_at TIMESTAMP NOT NULL
);

CREATE TABLE dependencies (
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

CREATE INDEX idx_dependencies_depends_on ON dependencies(depends_on);

-- Дефолтный проект: удобно сразу после создания схемы.
INSERT INTO projects (id, name) VALUES (1, 'default')
ON CONFLICT (id) DO NOTHING;
SELECT setval('projects_id_seq',
              (SELECT MAX(id) FROM projects), true);