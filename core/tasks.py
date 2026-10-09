from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from math import isfinite
from re import fullmatch
from uuid import uuid4

from core.auth import Session
from core.constraints import LinearConstraints2D
from core.expressions import DerivedMetric, ExpressionModel
from core.problem import OptimizationProblem
from plugin_api import ConstraintSet, LinearConstraintSet


@dataclass(frozen=True)
class TaskDefinition:
    id: str
    name: str
    description: str
    problem: OptimizationProblem
    variable_names: tuple[str, ...] = ("x", "y")
    variable_units: tuple[str, ...] = ("", "")
    model_parameters: object | None = None
    builtin: bool = False
    objective_label: str = "f"
    objective_unit: str = ""

    @property
    def formula(self) -> str:
        if isinstance(self.model_parameters, ExpressionModel):
            return self.model_parameters.formula
        raise ValueError("Формула не задана в сохраняемом виде.")

    @property
    def derived_metrics(self) -> tuple[DerivedMetric, ...]:
        return self.problem.derived_metrics

    @property
    def coefficients(self) -> tuple[tuple[str, float], ...]:
        if isinstance(self.model_parameters, ExpressionModel):
            return self.model_parameters.coefficients
        return ()

    def __post_init__(self) -> None:
        if not fullmatch(r"[a-z][a-z0-9_.-]*", self.id):
            raise ValueError("Нужен ID задачи из латинских букв, цифр, точек или дефисов.")
        if not self.name.strip() or not self.description.strip():
            raise ValueError("Название и описание задачи обязательны.")
        if not isinstance(self.problem, OptimizationProblem):
            raise ValueError("Задача должна содержать OptimizationProblem.")
        count = len(self.problem.constraints.bounds)
        if len(self.variable_names) != count or len(self.variable_units) != count:
            raise ValueError("Названия и единицы должны быть заданы для каждой переменной.")
        if len(set(self.variable_names)) != count or any(
            not name.strip() or len(name) > 32 for name in self.variable_names
        ):
            raise ValueError("Переменным нужны уникальные непустые названия до 32 символов.")
        if any(len(unit) > 32 for unit in self.variable_units):
            raise ValueError("Единицы измерения не должны превышать 32 символа.")
        if (
            not isinstance(self.objective_label, str)
            or not self.objective_label.strip()
            or len(self.objective_label) > 32
            or not isinstance(self.objective_unit, str)
            or len(self.objective_unit) > 32
        ):
            raise ValueError("Обозначение и единица функции — строки длиной до 32 символов.")


class TaskLibrary:
    def __init__(self) -> None:
        self._session: Session | None = None
        self._tasks: dict[str, TaskDefinition] = {}
        self._save: Callable[[TaskDefinition], None] | None = None
        self._delete: Callable[[str], None] | None = None

    def bind_session(self, session: Session) -> None:
        if self._session is not None:
            raise ValueError("Контекст доступа уже установлен.")
        session.user
        self._session = session

    def _require_write(self) -> None:
        if self._session is not None:
            self._session.require_admin()

    def set_persistence(
        self, save: Callable[[TaskDefinition], None], delete: Callable[[str], None]
    ) -> None:
        self._save, self._delete = save, delete

    def add(self, task: TaskDefinition) -> None:
        self._require_write()
        if task.id in self._tasks:
            raise ValueError(f"Задача «{task.id}» уже существует.")
        if self._save is not None:
            self._save(task)
        self._tasks[task.id] = task

    def load(self, tasks: Sequence[TaskDefinition]) -> None:
        self._require_write()
        if self._save is not None:
            raise ValueError("Библиотека уже подключена к хранилищу.")
        loaded = {task.id: task for task in tasks}
        if len(loaded) != len(tasks):
            raise ValueError("В заданиях повторяются идентификаторы.")
        self._tasks = loaded

    def get(self, task_id: str) -> TaskDefinition:
        try:
            return self._tasks[task_id]
        except KeyError:
            raise ValueError(f"Задача «{task_id}» не найдена.") from None

    def list_tasks(self) -> tuple[TaskDefinition, ...]:
        return tuple(self._tasks.values())

    def update(self, task: TaskDefinition) -> None:
        self._require_write()
        existing = self.get(task.id)
        if task.builtin != existing.builtin:
            raise ValueError("Нельзя менять происхождение задания.")
        if self._save is not None:
            self._save(task)
        self._tasks[task.id] = task

    def duplicate(
        self, task_id: str, *, new_id: str | None = None, name: str | None = None
    ) -> TaskDefinition:
        self._require_write()
        task = self.get(task_id)
        copied = replace(
            task,
            id=new_id or "task-" + uuid4().hex,
            name=name or task.name + " (копия)",
            builtin=False,
        )
        self.add(copied)
        return copied

    def remove(self, task_id: str) -> None:
        self._require_write()
        if self.get(task_id).builtin:
            raise ValueError("Встроенную задачу нельзя удалить.")
        if self._delete is not None:
            self._delete(task_id)
        del self._tasks[task_id]


def linear_constraints(
    bounds: tuple[tuple[float, float], tuple[float, float]],
    rows: Sequence[tuple[float, float, str, float]],
) -> LinearConstraints2D:
    if len(rows) > 64:
        raise ValueError("Допустимо до 64 линейных ограничений.")
    inequalities = []
    for a, b, comparison, c in rows:
        if not all(isfinite(v) and abs(v) <= 1e12 for v in (a, b, c)):
            raise ValueError("Коэффициенты ограничений должны быть конечными, по модулю до 10¹².")
        if comparison not in ("<=", ">=", "="):
            raise ValueError("Разрешены только сравнения <=, >= и =.")
        if comparison in ("<=", "="):
            inequalities.append((a, b, c))
        if comparison in (">=", "="):
            inequalities.append((-a, -b, -c))
    constraints = LinearConstraints2D(bounds, tuple(inequalities))
    if len(inequalities) > 64:
        raise ValueError("Допустимо до 64 неравенств; равенство занимает два неравенства.")
    if not constraints.vertices():
        raise ValueError("Ограничения противоречивы: допустимая область пуста.")
    return constraints


def model_task(
    task_id: str,
    name: str,
    description: str,
    model: ExpressionModel,
    constraints: ConstraintSet,
    direction: str,
    variable_names: tuple[str, str],
    variable_units: tuple[str, str] = ("", ""),
    derived_metrics: tuple[DerivedMetric, ...] | None = None,
    objective_label: str | None = None,
    objective_unit: str | None = None,
) -> TaskDefinition:
    if tuple(variable_names) != model.variables:
        raise ValueError("Имена переменных должны совпадать с именами в формуле.")
    if any(abs(value) > 1e9 for bound in constraints.bounds for value in bound):
        raise ValueError("Допустимо до 64 неравенств; границы по модулю не более 10⁹.")
    if isinstance(constraints, LinearConstraintSet):
        if len(constraints.inequalities) > 64:
            raise ValueError("Допустимо до 64 неравенств.")
        if any(abs(value) > 1e12 for row in constraints.inequalities for value in row):
            raise ValueError("Коэффициенты ограничений по модулю не должны превышать 10¹².")
    metrics = derived_metrics or ()
    problem = OptimizationProblem(
        model.objective,
        constraints,
        direction,
        array_objective=model.array_objective,
        derived_metrics=metrics,
    )
    points = constraints.vertices() if isinstance(constraints, LinearConstraintSet) else []
    if isinstance(constraints, LinearConstraintSet) and not points:
        raise ValueError("Допустимая область пуста.")
    if points:
        center = tuple(sum(p[i] for p in points) / len(points) for i in (0, 1))
        if constraints.contains(center):
            points.append(center)
    for point in points:
        value = problem.objective(*point)
        if not isfinite(value):
            raise ValueError("Функция должна быть конечной в допустимой области.")
        for metric in metrics:
            metric.calculate(value)
    for i in range(5):
        for j in range(5):
            (xmin, xmax), (ymin, ymax) = constraints.bounds
            point = (xmin + (xmax - xmin) * i / 4, ymin + (ymax - ymin) * j / 4)
            if constraints.contains(point):
                problem.objective(*point)
    label = objective_label if objective_label is not None else "f"
    unit = objective_unit if objective_unit is not None else ""
    return TaskDefinition(
        task_id,
        name,
        description,
        problem,
        variable_names,
        variable_units,
        model,
        objective_label=label,
        objective_unit=unit,
    )
