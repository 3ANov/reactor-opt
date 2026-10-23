from collections import deque
from collections.abc import Callable
from math import hypot, isfinite

from plugin_api import (
    AlgorithmParameters,
    LinearConstraintSet,
    MethodCapabilities,
    MethodMetadata,
    OptimizationMethod,
    OptimizationResult,
    OptimizationTask,
    Point,
)

from ._search import SearchState

ScoredPoint = tuple[float, Point]


def _cross(origin: Point, a: Point, b: Point) -> float:
    return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])


def _convex_hull(points: list[Point]) -> list[Point]:
    """Order the vertices and remove collinear interior points without an area tolerance."""
    ordered = sorted(set(points))
    if len(ordered) <= 1:
        return ordered
    lower: list[Point] = []
    upper: list[Point] = []
    for chain, vertices in ((lower, ordered), (upper, reversed(ordered))):
        for point in vertices:
            while len(chain) >= 2 and _cross(chain[-2], chain[-1], point) <= 0:
                chain.pop()
            chain.append(point)
    return lower[:-1] + upper[:-1]


def _distance(a: Point, b: Point) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])


def _interpolate(a: Point, b: Point, fraction: float) -> Point:
    return a[0] + fraction * (b[0] - a[0]), a[1] + fraction * (b[1] - a[1])


def _towards(a: Point, b: Point, step: float) -> Point:
    distance = _distance(a, b)
    return a if distance == 0 else _interpolate(a, b, min(1.0, step / distance))


def _onto_segment(point: Point, a: Point, b: Point) -> Point:
    # Normalize first: squaring the length underflows for very small feasible domains.
    length = _distance(a, b)
    if length == 0:
        return a
    dx, dy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    fraction = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length
    if fraction <= 0:
        return a
    if fraction >= 1:
        return b
    return _interpolate(a, b, fraction)


def _projector(constraints: LinearConstraintSet, hull: list[Point]) -> Callable[[Point], Point]:
    def project(point: Point) -> Point:
        if constraints.contains(point):
            return point
        projections = (_onto_segment(point, a, b) for a, b in zip(hull, hull[1:] + hull[:1]))
        candidate = min(projections, key=lambda p: _distance(point, p))
        # Interpolation on an edge can round a coordinate just past a strict box bound.
        candidate = tuple(
            min(high, max(low, value)) for value, (low, high) in zip(candidate, constraints.bounds)
        )
        if constraints.contains(candidate):
            return candidate
        # A vertex is always feasible, even when edge interpolation loses precision.
        return min(hull, key=lambda p: _distance(point, p))

    return project


def _diameter(points: list[Point]) -> float:
    return max((_distance(a, b) for a in points for b in points), default=0.0)


def _stopped(state: SearchState) -> bool:
    if state.reason:
        return True
    if state.cancelled is not None and state.cancelled():
        state.reason = "Расчёт отменён"
    elif state.evaluations >= state.parameters.max_evaluations:
        state.reason = "Достигнут лимит вычислений"
    return bool(state.reason)


def _score(state: SearchState, point: Point) -> ScoredPoint | None:
    if _stopped(state):
        return None
    # An infinite barrier rejects infeasible trials without evaluating the objective.
    # Unlike projection of 2D trials, this preserves a full-dimensional simplex.
    if not state.problem.constraints.contains(point):
        return float("inf"), point
    value = state.evaluate(point)
    return None if value is None else (-state.sign * value, point)


def _iteration(
    state: SearchState,
    simplex: list[ScoredPoint],
    project: Callable[[Point], Point] | None,
) -> list[ScoredPoint]:
    """One standard Nelder--Mead iteration (alpha=1, gamma=2, rho=sigma=0.5)."""
    simplex.sort()
    best, worst = simplex[0], simplex[-1]
    dimension = len(simplex) - 1
    centroid = tuple(sum(vertex[1][axis] / dimension for vertex in simplex[:-1]) for axis in (0, 1))

    def trial(factor: float) -> ScoredPoint | None:
        point = _interpolate(centroid, worst[1], factor)
        return _score(state, point if project is None else project(point))

    reflected = trial(-1.0)
    if reflected is None:
        return simplex
    if reflected[0] < best[0]:
        expanded = trial(-2.0)
        simplex[-1] = expanded if expanded is not None and expanded[0] < reflected[0] else reflected
        return simplex
    if reflected[0] < simplex[-2][0]:
        simplex[-1] = reflected
        return simplex

    if reflected[0] < worst[0]:
        contracted = trial(-0.5)  # Outside contraction towards the reflected point.
        if contracted is not None and contracted[0] <= reflected[0]:
            simplex[-1] = contracted
            return simplex
    else:
        contracted = trial(0.5)  # Inside contraction towards the old worst point.
        if contracted is not None and contracted[0] < worst[0]:
            simplex[-1] = contracted
            return simplex
    if contracted is None:
        return simplex

    for index in range(1, len(simplex)):
        point = _interpolate(best[1], simplex[index][1], 0.5)
        shrunk = _score(state, point if project is None else project(point))
        if shrunk is None:
            break
        simplex[index] = shrunk
    return simplex


def _minimize_simplex(
    state: SearchState,
    points: list[Point],
    project: Callable[[Point], Point] | None = None,
) -> bool:
    if _stopped(state):
        return False
    state.begin_stage()
    simplex = []
    for point in points:
        scored = _score(state, point if project is None else project(point))
        if scored is None:
            break
        simplex.append(scored)
    state.finish_stage(_diameter(points), 1)
    if len(simplex) != len(points):
        return False

    recent: deque[tuple[ScoredPoint, ...]] = deque(maxlen=32)
    while not _stopped(state):
        diameter = _diameter([point for _, point in simplex])
        criterion = state.parameters.stopping_criterion
        if criterion == "coordinates" and diameter <= state.parameters.precision:
            return True
        if criterion == "function":
            scores = [score for score, _ in simplex]
            if all(isfinite(score) for score in scores) and (
                max(scores) - min(scores) <= state.parameters.function_precision
            ):
                return True
        signature = tuple(sorted(simplex))
        if signature in recent:
            return False  # Floating-point arithmetic has entered a cycle for this start.
        recent.append(signature)
        previous_score, previous_point = min(simplex)
        state.begin_stage()
        simplex = _iteration(state, simplex, project)
        state.finish_stage(_diameter([point for _, point in simplex]), 1)
        score, point = min(simplex)
        if (
            not _stopped(state)
            and criterion == "function"
            and point != previous_point
            and score < previous_score
            and previous_score - score <= state.parameters.function_precision
        ):
            return True
    return False


class NelderMeadMethod(OptimizationMethod):
    metadata = MethodMetadata(
        id="nelder-mead",
        name="Метод Нелдера — Мида",
        description="Локальный симплексный поиск с отражением, растяжением и сжатием. "
        "Запуски из центра, вершин и вдоль рёбер допустимой области; "
        "вычисления выполняются только в допустимых точках. "
        "Критерий точности завершает каждый локальный запуск; "
        "лимит вычислений общий для всех запусков. "
        "Глобальная оптимальность не гарантируется.",
        category="Симплексные методы",
        builtin=True,
        priority=20,
    )
    capabilities = MethodCapabilities()
    supported_parameters = (
        "precision",
        "function_precision",
        "stopping_criterion",
        "initial_step",
        "max_evaluations",
        "keep_history",
    )

    def run(
        self,
        problem: OptimizationTask,
        parameters: AlgorithmParameters,
        cancelled: Callable[[], bool] | None = None,
    ) -> OptimizationResult:
        if not isinstance(problem.constraints, LinearConstraintSet):
            raise ValueError("Метод поддерживает только границы и линейные ограничения.")
        state = SearchState(problem, parameters, cancelled)
        if _stopped(state):
            return state.result(self.metadata.name)
        hull = _convex_hull(problem.constraints.vertices())
        if not hull:
            state.reason = "Допустимых решений нет"
            return state.result(self.metadata.name)

        # Preserve exact boundary optima and select useful starts without a grid search.
        state.begin_stage()
        for vertex in hull:
            if state.evaluate(vertex) is None:
                break
        state.finish_stage(parameters.initial_step, 1)
        if _stopped(state):
            return state.result(self.metadata.name)
        if len(hull) == 1:
            state.reason = "Допустимая область состоит из одной точки"
            return state.result(self.metadata.name)

        project = _projector(problem.constraints, hull)
        center = tuple(sum(point[axis] / len(hull) for point in hull) for axis in (0, 1))
        step = parameters.initial_step
        accuracy_reached = []
        if len(hull) > 2:
            # Pick two independent directions for the central simplex.
            second = max(hull[1:], key=lambda p: abs(_cross(center, hull[0], p)))
            accuracy_reached.append(
                _minimize_simplex(
                    state,
                    [
                        project(center),
                        project(_towards(center, hull[0], step)),
                        project(_towards(center, second, step)),
                    ],
                )
            )
            for vertex in hull:
                if _stopped(state):
                    break
                first = max(hull, key=lambda point: _distance(vertex, point))
                second = max(hull, key=lambda point: abs(_cross(vertex, first, point)))
                accuracy_reached.append(
                    _minimize_simplex(
                        state,
                        [
                            vertex,
                            project(_towards(vertex, first, step)),
                            project(_towards(vertex, second, step)),
                        ],
                    )
                )

        # Independent 1D simplices also search boundary optima. Projection is safe
        # in one dimension and naturally handles an equality/line feasible domain.
        edges = list(zip(hull, hull[1:] + hull[:1])) if len(hull) > 2 else [(hull[0], hull[1])]
        for a, b in edges:
            if _stopped(state):
                break
            midpoint = _interpolate(a, b, 0.5)

            def segment_project(point: Point, a: Point = a, b: Point = b) -> Point:
                return project(_onto_segment(point, a, b))

            accuracy_reached.append(
                _minimize_simplex(state, [midpoint, _towards(midpoint, b, step)], segment_project)
            )
        if not _stopped(state):
            if accuracy_reached and all(accuracy_reached):
                state.reason = (
                    "Достигнута точность по функции"
                    if parameters.stopping_criterion == "function"
                    else "Достигнута точность по координатам"
                )
            else:
                state.reason = "Завершён локальный симплексный поиск"
        return state.result(self.metadata.name)
