from datetime import datetime

from graph import TaskGraph
from models import ScheduledTask


def calculate_schedule(graph: TaskGraph) -> dict[int, ScheduledTask]:
    """Рассчитать допустимое расписание задач.

    TODO:
    - топологическая сортировка
    - расчёт earliest possible start
    - учёт dependencies
    - проверка deadline
    - обнаружение конфликтов
    """
    raise NotImplementedError
