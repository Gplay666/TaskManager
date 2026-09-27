from models import Task


class TaskGraph:
    """Задачи + связи «X зависит от Y» с защитой от циклов.

    Направление: add_dependency(before, after) означает,
    что `before` должно завершиться до начала `after`.
    """

    def __init__(self) -> None:
        self.tasks: dict[int, Task] = {}

    def add_task(self, task: Task) -> None:
        if task.id in self.tasks:
            raise ValueError(f"task {task.id} already exists")
        self.tasks[task.id] = task

    def add_dependency(self, before: int, after: int) -> None:
        if before not in self.tasks:
            raise KeyError(f"unknown task {before}")
        if after not in self.tasks:
            raise KeyError(f"unknown task {after}")
        if before == after:
            raise ValueError(f"task {before} cannot depend on itself")
        if self._creates_cycle(before, after):
            raise ValueError(f"dependency {before} -> {after} creates a cycle")
        self.tasks[after].dependencies.add(before)

    def get_dependencies(self, task_id: int) -> set[int]:
        return set(self.tasks[task_id].dependencies)  # копия, не живой set

    def _creates_cycle(self, before: int, after: int) -> bool:
        """Цикл возникнет, если before (транзитивно) уже зависит от after."""
        stack = [before]
        seen: set[int] = set()
        while stack:
            node = stack.pop()
            if node == after:
                return True
            if node in seen:
                continue
            seen.add(node)
            task = self.tasks.get(node)
            if task:
                stack.extend(task.dependencies)
        return False


def build_graph(tasks: dict[int, Task]) -> TaskGraph:
    g = TaskGraph()
    for task in tasks.values():
        g.add_task(task)
    return g