from collections import deque
from collections.abc import Callable
from math import isfinite

from plugin_api import (
    AlgorithmParameters,
    BestPoint,
    OptimizationResult,
    OptimizationTask,
    Point,
    SearchStage,
)


class SearchState:
    def __init__(
        self,
        problem: OptimizationTask,
        parameters: AlgorithmParameters,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.problem = problem
        self.parameters = parameters
        self.cancelled = cancelled
        self.sign = 1 if problem.direction == "maximize" else -1
        self.best_point: Point | None = None
        self.best_value: float | None = None
        self.evaluations = 0
        self.stage_number = 0
        self.reason = ""
        self.best_history: deque[BestPoint] = deque(maxlen=2000)
        self.stage_history: deque[SearchStage] = deque(maxlen=2000)
        self.history_truncated = False
        self._stage_start = 0

    def begin_stage(self) -> None:
        self.stage_number += 1
        self._stage_start = self.evaluations

    def evaluate(self, point: Point) -> float | None:
        if self.cancelled is not None and self.cancelled():
            self.reason = "Расчёт отменён"
            return None
        if self.evaluations >= self.parameters.max_evaluations:
            self.reason = "Достигнут лимит вычислений"
            return None
        value = float(self.problem.objective(*point))
        if not isfinite(value):
            raise ValueError("Целевая функция вернула неконечное значение.")
        self.evaluations += 1
        if self.best_value is None or self.sign * value > self.sign * self.best_value:
            self.best_point, self.best_value = point, value
            if self.parameters.keep_history:
                self.history_truncated |= len(self.best_history) == self.best_history.maxlen
                self.best_history.append(
                    BestPoint(
                        point,
                        value,
                        self.evaluations,
                        self.stage_number,
                    )
                )
        return value

    def finish_stage(self, step: float, regions: int) -> None:
        if self.parameters.keep_history:
            self.history_truncated |= len(self.stage_history) == self.stage_history.maxlen
            self.stage_history.append(
                SearchStage(
                    self.stage_number,
                    step,
                    self.evaluations - self._stage_start,
                    regions,
                    self.best_point,
                    self.best_value,
                )
            )

    def result(self, method_name: str) -> OptimizationResult:
        return OptimizationResult(
            self.best_point,
            self.best_value,
            method_name,
            self.evaluations,
            self.stage_number,
            self.reason,
            tuple(self.best_history),
            tuple(self.stage_history),
            self.history_truncated,
        )
