from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from core.problem import OptimizationProblem
from plugin_api import LinearConstraintSet
from visualization.plot_data import feasible_polygon

MAX_RESOLUTION = 201


@dataclass(frozen=True)
class SurfaceMesh:
    points: np.ndarray
    triangles: np.ndarray


@dataclass(frozen=True)
class SurfaceData:
    full: SurfaceMesh
    feasible: SurfaceMesh
    boundary: np.ndarray
    boundary_lines: np.ndarray


def evaluate_surface(
    problem: OptimizationProblem, xy: np.ndarray, cancelled: Callable[[], bool] | None = None
) -> np.ndarray:
    values = np.full(len(xy), np.nan)
    for start in range(0, len(xy), 2048):
        if cancelled is not None and cancelled():
            raise InterruptedError
        block = xy[start : start + 2048]
        if problem.array_objective is not None:
            try:
                with np.errstate(all="ignore"):
                    values[start : start + len(block)] = problem.array_objective(
                        block[:, 0], block[:, 1]
                    )
                continue
            except (ValueError, ArithmeticError):
                pass
        for i, (x, y) in enumerate(block, start):
            try:
                values[i] = float(problem.objective(float(x), float(y)))
            except (ValueError, ArithmeticError):
                continue
    values[~np.isfinite(values)] = np.nan
    return values


def compact_mesh(points: np.ndarray, triangles: np.ndarray) -> SurfaceMesh:
    triangles = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    triangles = triangles[np.isfinite(points[triangles]).all(axis=(1, 2))]
    if not len(triangles):
        return SurfaceMesh(np.empty((0, 3)), np.empty((0, 3), dtype=np.int64))
    xy = points[triangles, :2]
    cross = (xy[:, 1, 0] - xy[:, 0, 0]) * (xy[:, 2, 1] - xy[:, 0, 1]) - (
        xy[:, 1, 1] - xy[:, 0, 1]
    ) * (xy[:, 2, 0] - xy[:, 0, 0])
    triangles = triangles[cross > 0]
    indices, inverse = np.unique(triangles, return_inverse=True)
    return SurfaceMesh(points[indices], inverse.reshape(-1, 3))


def clip_cell(
    polygon: list[tuple[float, float]], halfplanes: tuple[tuple[float, float, float], ...]
) -> list[tuple[float, float]]:
    for a, b, c in halfplanes:
        output = []
        if not polygon:
            break
        previous = polygon[-1]
        previous_value = a * previous[0] + b * previous[1] - c
        for current in polygon:
            current_value = a * current[0] + b * current[1] - c
            if (previous_value <= 0) != (current_value <= 0):
                t = previous_value / (previous_value - current_value)
                output.append(
                    (
                        previous[0] + t * (current[0] - previous[0]),
                        previous[1] + t * (current[1] - previous[1]),
                    )
                )
            if current_value <= 0:
                output.append(current)
            previous, previous_value = current, current_value
        polygon = output
    return polygon


def compute_surface(
    problem: OptimizationProblem, resolution: int = 81, cancelled: Callable[[], bool] | None = None
) -> SurfaceData:
    if not isinstance(problem.constraints, LinearConstraintSet):
        raise ValueError("3D-область пока поддерживает только линейные ограничения.")
    if type(resolution) is not int or not 3 <= resolution <= MAX_RESOLUTION:
        raise ValueError(f"Детализация сетки должна быть от 3 до {MAX_RESOLUTION}.")
    (xmin, xmax), (ymin, ymax) = problem.constraints.bounds
    x, y = np.linspace(xmin, xmax, resolution), np.linspace(ymin, ymax, resolution)
    xx, yy = np.meshgrid(x, y, indexing="ij")
    xy = np.column_stack((xx.ravel(), yy.ravel()))
    points = np.column_stack((xy, evaluate_surface(problem, xy, cancelled)))
    indices = np.arange(resolution**2).reshape(resolution, resolution)
    cells = np.column_stack(
        (
            indices[:-1, :-1].ravel(),
            indices[1:, :-1].ravel(),
            indices[1:, 1:].ravel(),
            indices[:-1, 1:].ravel(),
        )
    )
    triangles = np.concatenate((cells[:, [0, 1, 2]], cells[:, [0, 2, 3]]))
    full = compact_mesh(points, triangles)
    cell_xy = xy[cells]
    inside = np.ones(len(cells), dtype=bool)
    possible = inside.copy()
    halfplanes = problem.constraints.halfplanes()
    for a, b, c in halfplanes:
        distance = a * cell_xy[:, :, 0] + b * cell_xy[:, :, 1] - c
        inside &= (distance <= 0).all(axis=1)
        possible &= (distance <= 0).any(axis=1)
    interior = cells[inside]
    feasible_triangles = [*interior[:, [0, 1, 2]], *interior[:, [0, 2, 3]]]
    extra = []
    for count, cell in enumerate(cells[possible & ~inside]):
        if count % 128 == 0 and cancelled is not None and cancelled():
            raise InterruptedError
        polygon = clip_cell([tuple(p) for p in xy[cell]], halfplanes)
        offset = len(points) + len(extra)
        extra.extend(polygon)
        feasible_triangles.extend(
            (offset, offset + i, offset + i + 1) for i in range(1, len(polygon) - 1)
        )
    if extra:
        extra_xy = np.asarray(extra)
        points = np.concatenate(
            (points, np.column_stack((extra_xy, evaluate_surface(problem, extra_xy, cancelled))))
        )
    feasible = compact_mesh(points, feasible_triangles)
    polygon = feasible_polygon(problem.constraints)
    boundary_xy = []
    boundary_lines = []
    edge_count = len(polygon) if len(polygon) > 2 else max(0, len(polygon) - 1)
    for i in range(edge_count):
        start, end = polygon[i], polygon[(i + 1) % len(polygon)]
        offset = len(boundary_xy)
        boundary_xy.extend(np.linspace(start, end, resolution))
        boundary_lines.extend((offset + j, offset + j + 1) for j in range(resolution - 1))
    boundary_xy = np.asarray(boundary_xy, dtype=float).reshape(-1, 2)
    boundary = np.column_stack((boundary_xy, evaluate_surface(problem, boundary_xy, cancelled)))
    boundary_lines = np.asarray(boundary_lines, dtype=np.int64).reshape(-1, 2)
    boundary_lines = boundary_lines[np.isfinite(boundary[boundary_lines]).all(axis=(1, 2))]
    return SurfaceData(full, feasible, boundary, boundary_lines)
