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
    deadline TIMESTAMP
);

CREATE TABLE dependencies (
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,

    PRIMARY KEY (task_id, depends_on),

    CHECK (task_id <> depends_on)
);
