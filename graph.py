from models import Task


class TaskGraph:
    def __init__(self):
        self.tasks: dict[int, Task] = {}

    def add_task(self, task: Task):
        self.tasks[task.id] = task

    def add_dependency(self, before: int, after: int):
        # TODO: проверить существование задач
        # TODO: проверить, что новая связь не создаёт цикл
        self.tasks[after].dependencies.add(before)

    def get_dependencies(self, task_id: int) -> set[int]:
        return self.tasks[task_id].dependencies
