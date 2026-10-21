"""Тесты API: авторизация, изоляция пользователей, задачи и расписание.

Запуск (из корня проекта):
    python -m unittest discover -s tests -v

Используется отдельная БД (taskmanager_test), схема пересоздаётся на старте.
Строка подключения переопределяется через TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import unittest
from urllib.parse import urlsplit, urlunsplit

TEST_DSN = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://taskmanager:taskmanager@localhost:5432/taskmanager_test",
)
# db.py читает DATABASE_URL при импорте — тесты должны подменить его раньше.
os.environ["DATABASE_URL"] = TEST_DSN

import psycopg                                    # noqa: E402
from fastapi.testclient import TestClient         # noqa: E402

import migrate                                    # noqa: E402
from api import app                               # noqa: E402


def _ensure_database(dsn: str) -> None:
    """Создать тестовую БД, если её ещё нет."""
    parts = urlsplit(dsn)
    name = parts.path.lstrip("/")
    maintenance = urlunsplit(parts._replace(path="/postgres"))
    with psycopg.connect(maintenance, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{name}"')


def setUpModule():
    _ensure_database(TEST_DSN)
    with psycopg.connect(TEST_DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    migrate.apply_schema(TEST_DSN)


def tearDownModule():
    if os.environ.get("TEST_DATABASE_KEEP"):
        return
    with psycopg.connect(TEST_DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


class ApiTestCase(unittest.TestCase):
    """Свежие данные и свежие cookie на каждый тест."""

    def setUp(self):
        with psycopg.connect(TEST_DSN, autocommit=True) as conn:
            conn.execute(
                "TRUNCATE users, projects, tasks, schedule, dependencies "
                "RESTART IDENTITY CASCADE"
            )
        self.client = TestClient(app)

    # -- вспомогательное ---------------------------------------------------

    def register(self, username: str, password: str = "secret123") -> TestClient:
        client = TestClient(app)
        r = client.post("/auth/register",
                        json={"username": username, "password": password})
        self.assertEqual(r.status_code, 201, r.text)
        return client

    def login(self, username: str, password: str = "secret123") -> TestClient:
        client = TestClient(app)
        r = client.post("/auth/login",
                        json={"username": username, "password": password})
        self.assertEqual(r.status_code, 200, r.text)
        return client

    def default_project(self, client: TestClient) -> int:
        projects = client.get("/projects").json()
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["name"], "default")
        return projects[0]["id"]


class AuthTests(ApiTestCase):

    def test_register_login_logout(self):
        client = self.register("alice")
        self.assertEqual(client.get("/auth/me").json()["username"], "alice")

        self.assertEqual(client.post("/auth/logout").status_code, 204)
        self.assertEqual(client.get("/auth/me").status_code, 401)

        client = self.login("alice")
        self.assertEqual(client.get("/auth/me").json()["username"], "alice")

    def test_login_is_case_insensitive(self):
        self.register("Alice")
        client = self.login("aLiCe")
        self.assertEqual(client.get("/auth/me").json()["username"], "Alice")

    def test_password_is_not_stored_in_plain_text(self):
        self.register("alice", "topsecret42")
        with psycopg.connect(TEST_DSN) as conn:
            row = conn.execute(
                "SELECT password_hash FROM users WHERE username = 'alice'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertNotIn("topsecret42", row[0])
        self.assertTrue(row[0].startswith("scrypt$"))

    def test_validation_rules(self):
        r = self.client.post("/auth/register",
                             json={"username": "alice", "password": "short"})
        self.assertEqual(r.status_code, 400)

        r = self.client.post("/auth/register",
                             json={"username": "no spaces", "password": "secret123"})
        self.assertEqual(r.status_code, 400)

        r = self.client.post("/auth/register",
                             json={"username": "ab", "password": "secret123"})
        self.assertEqual(r.status_code, 400)

    def test_duplicate_username_rejected(self):
        self.register("alice")
        r = TestClient(app).post("/auth/register",
                                 json={"username": "ALICE", "password": "secret123"})
        self.assertEqual(r.status_code, 409)

    def test_wrong_password_rejected(self):
        self.register("alice")
        r = TestClient(app).post("/auth/login",
                                 json={"username": "alice", "password": "wrongpass"})
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("Set-Cookie", r.headers)

    def test_unknown_user_rejected(self):
        r = TestClient(app).post("/auth/login",
                                 json={"username": "nobody", "password": "secret123"})
        self.assertEqual(r.status_code, 401)

    def test_session_cookie_flags(self):
        client = TestClient(app)
        r = client.post("/auth/register",
                        json={"username": "alice", "password": "secret123"})
        cookie = r.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)

    def test_endpoints_require_auth(self):
        cases = [
            ("GET", "/projects", None),
            ("POST", "/projects", {"name": "x"}),
            ("GET", "/projects/1/tasks", None),
            ("GET", "/tasks/1", None),
            ("DELETE", "/tasks/1", None),
            ("GET", "/projects/1/schedule", None),
            ("POST", "/projects/1/autofill", None),
            ("GET", "/projects/1/violations", None),
        ]
        for method, url, body in cases:
            with self.subTest(method=method, url=url):
                r = self.client.request(method, url, json=body)
                self.assertEqual(r.status_code, 401, r.text)

    def test_logout_revokes_session_server_side(self):
        client = self.register("alice")
        token = client.cookies.get("tm_session")
        self.assertTrue(token)

        client.post("/auth/logout")

        # тот же токен в другом клиенте больше не работает
        other = TestClient(app)
        other.cookies.set("tm_session", token)
        self.assertEqual(other.get("/auth/me").status_code, 401)


class IsolationTests(ApiTestCase):

    def test_each_user_starts_with_own_default_project(self):
        alice = self.register("alice")
        bob = self.register("bob")
        alice_pid = self.default_project(alice)
        bob_pid = self.default_project(bob)
        self.assertNotEqual(alice_pid, bob_pid)

    def test_users_do_not_see_each_others_data(self):
        alice = self.register("alice")
        alice_pid = self.default_project(alice)
        task = alice.post(f"/projects/{alice_pid}/tasks",
                          json={"name": "secret", "duration_seconds": 3600}).json()
        alice.put(f"/tasks/{task['id']}/schedule",
                  json={"start": "2026-10-22T09:00:00"})

        bob = self.register("bob")

        self.assertEqual(bob.get("/projects").json()[0]["id"] != alice_pid, True)
        cases = [
            ("GET", f"/projects/{alice_pid}", None),
            ("PATCH", f"/projects/{alice_pid}", {"name": "stolen"}),
            ("DELETE", f"/projects/{alice_pid}", None),
            ("GET", f"/projects/{alice_pid}/tasks", None),
            ("GET", f"/projects/{alice_pid}/schedule", None),
            ("GET", f"/projects/{alice_pid}/violations", None),
            ("GET", f"/tasks/{task['id']}", None),
            ("PATCH", f"/tasks/{task['id']}", {"name": "stolen"}),
            ("DELETE", f"/tasks/{task['id']}", None),
            ("PUT", f"/tasks/{task['id']}/schedule",
             {"start": "2026-10-22T10:00:00"}),
        ]
        for method, url, body in cases:
            with self.subTest(method=method, url=url):
                r = bob.request(method, url, json=body)
                self.assertEqual(r.status_code, 404, r.text)

        # чужая задача не появилась среди задач Боба
        bob_pid = self.default_project(bob)
        self.assertEqual(bob.get(f"/projects/{bob_pid}/tasks").json(), [])

    def test_cannot_depend_on_foreign_task(self):
        alice = self.register("alice")
        alice_pid = self.default_project(alice)
        foreign = alice.post(f"/projects/{alice_pid}/tasks",
                             json={"name": "foreign", "duration_seconds": 60}).json()

        bob = self.register("bob")
        bob_pid = self.default_project(bob)
        own = bob.post(f"/projects/{bob_pid}/tasks",
                       json={"name": "own", "duration_seconds": 60}).json()

        r = bob.post(f"/tasks/{own['id']}/dependencies",
                     json={"depends_on": foreign["id"]})
        self.assertEqual(r.status_code, 404)


class TaskFlowTests(ApiTestCase):

    def setUp(self):
        super().setUp()
        self.alice = self.register("alice")
        self.pid = self.default_project(self.alice)

    def create_task(self, **overrides) -> dict:
        payload = {"name": "task", "duration_seconds": 3600}
        payload.update(overrides)
        r = self.alice.post(f"/projects/{self.pid}/tasks", json=payload)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def test_crud_roundtrip(self):
        t = self.create_task(name="write docs")
        self.assertEqual(t["dependencies"], [])

        r = self.alice.patch(f"/tasks/{t['id']}",
                             json={"name": "write docs v2", "duration_seconds": 7200})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["name"], "write docs v2")

        # earliest_start можно сбросить в null
        r = self.alice.patch(f"/tasks/{t['id']}", json={"earliest_start": None})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["earliest_start"])

        self.assertEqual(self.alice.delete(f"/tasks/{t['id']}").status_code, 204)
        self.assertEqual(self.alice.get(f"/tasks/{t['id']}").status_code, 404)

    def test_dependencies_and_cycles(self):
        a = self.create_task(name="a")
        b = self.create_task(name="b")

        r = self.alice.post(f"/tasks/{b['id']}/dependencies",
                            json={"depends_on": a["id"]})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.alice.get(f"/tasks/{b['id']}").json()["dependencies"],
                         [a["id"]])

        # a не может зависеть от b — это цикл
        r = self.alice.post(f"/tasks/{a['id']}/dependencies",
                            json={"depends_on": b["id"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("cycle", r.json()["detail"])

        # self-dependency запрещён схемой/графом
        r = self.alice.post(f"/tasks/{a['id']}/dependencies",
                            json={"depends_on": a["id"]})
        self.assertEqual(r.status_code, 400)

        r = self.alice.delete(f"/tasks/{b['id']}/dependencies/{a['id']}")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.alice.get(f"/tasks/{b['id']}").json()["dependencies"], [])

    def test_dependency_must_be_in_same_project(self):
        other_pid = self.alice.post("/projects", json={"name": "other"}).json()["id"]
        foreign = self.alice.post(f"/projects/{other_pid}/tasks",
                                  json={"name": "foreign", "duration_seconds": 60}).json()
        own = self.create_task(name="own")

        r = self.alice.post(f"/tasks/{own['id']}/dependencies",
                            json={"depends_on": foreign["id"]})
        self.assertEqual(r.status_code, 400)

    def test_schedule_and_violations(self):
        a = self.create_task(name="a", duration_seconds=3600)
        b = self.create_task(name="b", duration_seconds=3600,
                             earliest_start="2026-10-22T08:00:00")

        # b начата раньше earliest_start и раньше конца a -> две ошибки
        self.alice.put(f"/tasks/{b['id']}/schedule",
                       json={"start": "2026-10-22T07:00:00"})
        self.alice.post(f"/tasks/{b['id']}/dependencies", json={"depends_on": a["id"]})

        kinds = {v["kind"] for v in
                 self.alice.get(f"/projects/{self.pid}/violations").json()}
        self.assertIn("earliest_start", kinds)
        self.assertIn("dep_missing", kinds)

        # ставим a корректно -> dep_order остаётся только у b
        self.alice.put(f"/tasks/{a['id']}/schedule",
                       json={"start": "2026-10-22T09:00:00"})
        kinds = {v["kind"] for v in
                 self.alice.get(f"/projects/{self.pid}/violations").json()}
        self.assertIn("dep_order", kinds)

    def test_autofill_places_dependencies_in_order(self):
        a = self.create_task(name="a", duration_seconds=3600,
                             earliest_start="2026-10-22T09:00:00")
        b = self.create_task(name="b", duration_seconds=3600)
        self.alice.post(f"/tasks/{b['id']}/dependencies",
                        json={"depends_on": a["id"]})

        placed = {st["task_id"]: st for st in
                  self.alice.post(f"/projects/{self.pid}/autofill").json()}
        self.assertEqual(placed[a["id"]]["start"], "2026-10-22T09:00:00")
        self.assertEqual(placed[b["id"]]["start"], "2026-10-22T10:00:00")

        # повторный autofill ничего не двигает
        again = {st["task_id"]: st for st in
                 self.alice.post(f"/projects/{self.pid}/autofill").json()}
        self.assertEqual(again[b["id"]]["start"], "2026-10-22T10:00:00")

    def test_unschedule(self):
        t = self.create_task()
        self.alice.put(f"/tasks/{t['id']}/schedule",
                       json={"start": "2026-10-22T09:00:00"})
        self.assertEqual(
            len(self.alice.get(f"/projects/{self.pid}/schedule").json()), 1)
        self.alice.delete(f"/tasks/{t['id']}/schedule")
        self.assertEqual(
            self.alice.get(f"/projects/{self.pid}/schedule").json(), [])

    def test_project_rename_and_delete(self):
        r = self.alice.patch(f"/projects/{self.pid}", json={"name": "renamed"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["name"], "renamed")

        self.assertEqual(self.alice.delete(f"/projects/{self.pid}").status_code, 204)
        self.assertEqual(self.alice.get("/projects").json(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
