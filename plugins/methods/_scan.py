from collections import deque
from collections.abc import Callable, Iterator
from math import ceil, isfinite

from plugin_api import (
    AlgorithmParameters,
    BestPoint,
    LinearConstraintSet,
    OptimizationResult,
    OptimizationTask,
    Point,
    SearchStage,
)


def axis_values(low: float, high: float, step: float) -> Iterator[float]:
    yield low
    if low == high:
        return
    if low + step == low:
        raise ValueError("Шаг слишком мал для заданного диапазона координат.")
    for index in range(1, ceil((high - low) / step)):
        value = low + index * step
        if value < high:
            yield value
    yield high


def scan_points(constraints: LinearConstraintSet, step: float) -> Iterator[Point]:
    corners = constraints.vertices()
    if not corners:
        return
    yield from corners
    xmin, xmax = min(p[0] for p in corners), max(p[0] for p in corners)
    ymin, ymax = min(p[1] for p in corners), max(p[1] for p in corners)
    for x in axis_values(xmin, xmax, step):
        low, high = ymin, ymax
        for a, b, c in constraints.halfplanes():
            if b > 0:
                high = min(high, (c - a * x) / b)
            elif b < 0:
                low = max(low, (c - a * x) / b)
        if low <= high:
            for y in axis_values(low, high, step):
                if constraints.contains((x, y)):
                    yield x, y
    # Horizontal slices also cover edges nearly parallel to the x axis.
    for y in axis_values(ymin, ymax, step):
        low, high = xmin, xmax
        for a, b, c in constraints.halfplanes():
            if a > 0:
                high = min(high, (c - b * y) / a)
            elif a < 0:
                low = max(low, (c - b * y) / a)
        for point in ((low, y), (high, y)):
            if low <= high and constraints.contains(point):
                yield point


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
