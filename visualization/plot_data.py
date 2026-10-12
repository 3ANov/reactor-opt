from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import atan2, isfinite

import numpy as np
from pyqtgraph.functions import isocurve

from core.problem import OptimizationProblem
from plugin_api import ConstraintSet, LinearConstraintSet, Point

ViewBounds = tuple[Point, Point]


def feasible_polygon(constraints: ConstraintSet) -> np.ndarray:
    if not isinstance(constraints, LinearConstraintSet):
        raise ValueError("Точное построение области поддерживает только линейные ограничения.")
    points = constraints.vertices()
    if not points:
        return np.empty((0, 2))
    center = np.mean(points, axis=0)
    points.sort(key=lambda p: atan2(p[1] - center[1], p[0] - center[0]))
    return np.asarray(points, dtype=float)


def view_bounds(constraints: ConstraintSet) -> ViewBounds:
    polygon = feasible_polygon(constraints) if isinstance(constraints, LinearConstraintSet) else []
    bounds = (
        constraints.bounds
        if not len(polygon)
        else tuple(
            (float(polygon[:, axis].min()), float(polygon[:, axis].max())) for axis in (0, 1)
        )
    )
    return tuple(
        (low - ((high - low) / 12 or 0.5), high + ((high - low) / 12 or 0.5))
        for low, high in bounds
    )


def constraint_segments(constraints: LinearConstraintSet, bounds: ViewBounds) -> np.ndarray:
    (xmin, xmax), (ymin, ymax) = bounds
    segments = []
    for a, b, c in constraints.halfplanes():
        points = []
        if b != 0:
            points.extend((x, (c - a * x) / b) for x in (xmin, xmax))
        if a != 0:
            points.extend(((c - b * y) / a, y) for y in (ymin, ymax))
        inside = list(
            dict.fromkeys((x, y) for x, y in points if xmin <= x <= xmax and ymin <= y <= ymax)
        )
        if len(inside) >= 2:
            segments.extend((inside[0], inside[-1]))
    return np.asarray(segments, dtype=float).reshape(-1, 2)


@dataclass(frozen=True)
class FunctionGrid:
    x: np.ndarray
    y: np.ndarray
    values: np.ndarray
    feasible: np.ndarray

    def interpolate(self, x: float, y: float) -> float | None:
        if not (self.x[0] <= x <= self.x[-1] and self.y[0] <= y <= self.y[-1]):
            return None
        i = min(len(self.x) - 2, max(0, int(np.searchsorted(self.x, x) - 1)))
        j = min(len(self.y) - 2, max(0, int(np.searchsorted(self.y, y) - 1)))
        tx = (x - self.x[i]) / (self.x[i + 1] - self.x[i])
        ty = (y - self.y[j]) / (self.y[j + 1] - self.y[j])
        block = self.values[i : i + 2, j : j + 2]
        if not np.isfinite(block).all():
            return None
        return float(np.array([1 - tx, tx]) @ block @ np.array([1 - ty, ty]))


@dataclass(frozen=True)
class Contour:
    level: float
    points: np.ndarray


@dataclass(frozen=True)
class PlotData:
    grid: FunctionGrid
    contours: tuple[Contour, ...]


def compute_grid(
    problem: OptimizationProblem, resolution: int = 151, cancelled: Callable[[], bool] | None = None
) -> FunctionGrid:
    if type(resolution) is not int or not 3 <= resolution <= 1001:
        raise ValueError("Размер сетки должен быть целым числом от 3 до 1001.")
    bounds = view_bounds(problem.constraints)
    x, y = (np.linspace(low, high, resolution) for low, high in bounds)
    values = np.full((resolution, resolution), np.nan)
    feasible = np.zeros(values.shape, dtype=bool)
    if problem.array_objective is not None:
        for i, a in enumerate(x):
            if cancelled is not None and cancelled():
                raise InterruptedError
            values[i] = problem.array_objective(a, y)
            feasible[i] = [problem.constraints.contains((float(a), float(b))) for b in y]
        return FunctionGrid(x, y, values, feasible)
    for i, a in enumerate(x):
        if cancelled is not None and cancelled():
            raise InterruptedError
        for j, b in enumerate(y):
            feasible[i, j] = problem.constraints.contains((float(a), float(b)))
            try:
                value = float(problem.objective(float(a), float(b)))
            except (ValueError, ArithmeticError):
                continue
            if isfinite(value):
                values[i, j] = value
    return FunctionGrid(x, y, values, feasible)


def build_contours(
    grid: FunctionGrid,
    levels: Sequence[float] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[Contour, ...]:
    finite = grid.values[grid.feasible & np.isfinite(grid.values)]
    if not len(finite):
        finite = grid.values[np.isfinite(grid.values)]
    if not len(finite):
        return ()
    if levels is None:
        if finite.min() == finite.max():
            return ()
        levels = np.unique(np.quantile(finite, np.linspace(0.05, 0.95, 12)))
    contours = []
    scale = np.array([grid.x[1] - grid.x[0], grid.y[1] - grid.y[0]])
    origin = np.array([grid.x[0], grid.y[0]])
    for level in levels:
        if cancelled is not None and cancelled():
            raise InterruptedError
        with np.errstate(invalid="ignore", divide="ignore"):
            segments = np.asarray(isocurve(grid.values, float(level)), dtype=float)
            segments = segments.reshape(-1, 2, 2)
        segments = segments[np.isfinite(segments).all(axis=(1, 2))]
        if len(segments):
            points = ((segments - 0.5) * scale + origin).reshape(-1, 2)
            contours.append(Contour(float(level), points))
    return tuple(contours)


def sampled_points(points: Sequence[Point], limit: int) -> np.ndarray:
    if limit < 1:
        raise ValueError("Лимит маркеров должен быть положительным.")
    indices = np.linspace(0, len(points) - 1, min(len(points), limit), dtype=int)
    return np.asarray([points[i] for i in indices], dtype=float).reshape(-1, 2)
