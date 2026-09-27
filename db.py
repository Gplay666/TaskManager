import psycopg


DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/task_planner"


def connect():
    return psycopg.connect(DATABASE_URL)


def load_tasks():
    # TODO: получить задачи из PostgreSQL
    raise NotImplementedError
