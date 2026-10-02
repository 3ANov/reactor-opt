from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from core.expressions import DerivedMetric
from plugin_api import ConstraintSet


@dataclass(frozen=True)
class OptimizationProblem:
    objective: Callable[[float, float], float]
    constraints: ConstraintSet
    direction: Literal["maximize", "minimize"] = "maximize"
    array_objective: Callable | None = None
    derived_metrics: tuple[DerivedMetric, ...] = ()

    def __post_init__(self) -> None:
        if self.direction not in ("maximize", "minimize"):
            raise ValueError("Направление поиска: maximize или minimize.")
        if not callable(self.objective):
            raise ValueError("Нужно задать целевую функцию.")
        if len(self.derived_metrics) > 16 or len({m.key for m in self.derived_metrics}) != len(
            self.derived_metrics
        ):
            raise ValueError("Допустимо до 16 показателей с уникальными идентификаторами.")
