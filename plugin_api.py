from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from re import fullmatch
from typing import Literal, Protocol, runtime_checkable

API_VERSION = 1
Point = tuple[float, float]


@dataclass(frozen=True)
class MetricValue:
    key: str
    name: str
    value: float
    unit: str = ""
    symbol: str = ""


@dataclass(frozen=True)
class AlgorithmParameters:
    precision: float = 0.01
    initial_step: float = 1.0
    max_evaluations: int = 1_000_000
    regions: int = 5
    keep_history: bool = False

    def __post_init__(self) -> None:
        if not all(isfinite(v) and v > 0 for v in (self.precision, self.initial_step)):
            raise ValueError("Точность и начальный шаг должны быть конечными и положительными.")
        if self.initial_step < self.precision:
            raise ValueError("Начальный шаг не может быть меньше точности.")
        if type(self.max_evaluations) is not int or self.max_evaluations < 1:
            raise ValueError("Лимит вычислений должен быть положительным целым числом.")
        if type(self.regions) is not int or not 1 <= self.regions <= 20:
            raise ValueError("Количество перспективных областей должно быть от 1 до 20.")


@dataclass(frozen=True)
class BestPoint:
    point: Point
    value: float
    evaluation: int
    stage: int


@dataclass(frozen=True)
class SearchStage:
    number: int
    step: float
    evaluations: int
    regions: int
    best_point: Point | None
    best_value: float | None


@dataclass(frozen=True)
class OptimizationResult:
    point: Point | None
    value: float | None
    method: str
    evaluations: int
    stages: int
    reason: str
    best_history: tuple[BestPoint, ...]
    stage_history: tuple[SearchStage, ...]
    history_truncated: bool = False
    derived_metrics: tuple[MetricValue, ...] = ()


@dataclass(frozen=True)
class MethodCapabilities:
    dimensions: tuple[int, ...] = (2,)
    constraint_types: tuple[str, ...] = ("bounds", "linear_inequality")
    directions: tuple[str, ...] = ("minimize", "maximize")

    def __post_init__(self) -> None:
        if not self.dimensions or any(type(n) is not int or n < 1 for n in self.dimensions):
            raise ValueError("Нужно указать поддерживаемое количество переменных.")
        if any(not fullmatch(r"[a-z][a-z0-9_]*", kind) for kind in self.constraint_types):
            raise ValueError("Неизвестный тип ограничений метода.")
        if not self.directions or not set(self.directions) <= {"minimize", "maximize"}:
            raise ValueError("Неверные направления оптимизации метода.")


@dataclass(frozen=True)
class MethodMetadata:
    id: str
    name: str
    description: str
    category: str
    defaults: AlgorithmParameters = AlgorithmParameters()
    enabled: bool = True
    builtin: bool = False
    implementation: str = ""
    fixed_parameters: tuple[tuple[str, float], ...] = ()
    api_version: int = API_VERSION
    priority: int = 0

    def __post_init__(self) -> None:
        if not fullmatch(r"[a-z][a-z0-9_.-]*", self.id):
            raise ValueError("ID метода должен содержать латинские буквы, цифры, точки или дефисы.")
        if type(self.priority) is not int:
            raise ValueError("Приоритет метода должен быть целым числом.")
        if not all(text.strip() for text in (self.name, self.description, self.category)):
            raise ValueError("Название, описание и категория метода обязательны.")
        if not isinstance(self.defaults, AlgorithmParameters):
            raise ValueError("Параметры метода должны иметь тип AlgorithmParameters.")
        if type(self.enabled) is not bool or type(self.builtin) is not bool:
            raise ValueError("Доступность и происхождение метода задаются логическими значениями.")
        if any(not name.strip() or not isfinite(value) for name, value in self.fixed_parameters):
            raise ValueError("Фиксированным параметрам нужны названия и конечные значения.")


@runtime_checkable
class ConstraintSet(Protocol):
    bounds: tuple[Point, Point]

    @property
    def constraint_types(self) -> tuple[str, ...]: ...
    def contains(self, point: Point) -> bool: ...


@runtime_checkable
class LinearConstraintSet(ConstraintSet, Protocol):
    inequalities: tuple[tuple[float, float, float], ...]

    def halfplanes(self) -> tuple[tuple[float, float, float], ...]: ...
    def vertices(self) -> list[Point]: ...
    def with_bounds(self, bounds: tuple[Point, Point]) -> "LinearConstraintSet": ...


@runtime_checkable
class OptimizationTask(Protocol):
    objective: Callable[[float, float], float]
    constraints: ConstraintSet
    direction: Literal["maximize", "minimize"]


class OptimizationMethod(ABC):
    @property
    @abstractmethod
    def metadata(self) -> MethodMetadata: ...

    @property
    @abstractmethod
    def capabilities(self) -> MethodCapabilities: ...

    @property
    @abstractmethod
    def supported_parameters(self) -> tuple[str, ...]: ...

    @abstractmethod
    def run(
        self,
        problem: OptimizationTask,
        parameters: AlgorithmParameters,
        cancelled: Callable[[], bool] | None = None,
    ) -> OptimizationResult: ...
