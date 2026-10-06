from collections.abc import Callable
from math import hypot

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

from ._scan import SearchState, scan_points


def select_candidate(
    candidates: list[tuple[float, Point]], point: Point, score: float, step: float, count: int
) -> None:
    nearby = next(
        (
            i
            for i, (_, p) in enumerate(candidates)
            if hypot(p[0] - point[0], p[1] - point[1]) < step * 0.9
        ),
        None,
    )
    if nearby is not None:
        if score > candidates[nearby][0]:
            candidates[nearby] = (score, point)
    else:
        candidates.append((score, point))
    candidates.sort(reverse=True)
    del candidates[count:]


def refinement_regions(
    constraints: LinearConstraintSet, candidates: list[tuple[float, Point]], radius: float
) -> list[LinearConstraintSet]:
    regions = []
    (xmin, xmax), (ymin, ymax) = constraints.bounds
    for _, (x, y) in candidates:
        bounds = (
            (max(xmin, x - radius), min(xmax, x + radius)),
            (max(ymin, y - radius), min(ymax, y + radius)),
        )
        regions.append(constraints.with_bounds(bounds))
    return regions


class VariableScanMethod(OptimizationMethod):
    metadata = MethodMetadata(
        id="variable",
        name="Метод сканирования с переменным шагом поиска",
        description="Предварительный обзор всей области "
        "и уточнение нескольких перспективных участков. "
        "Шаг уменьшается вдвое до заданной точности. "
        "Глобальная оптимальность не гарантируется.",
        category="Сеточные методы",
        builtin=True,
        priority=10,
        fixed_parameters=(("step_reduction", 0.5),),
    )
    capabilities = MethodCapabilities()
    supported_parameters = (
        "precision",
        "initial_step",
        "regions",
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
        step = parameters.initial_step
        regions = [problem.constraints]
        while regions:
            state.begin_stage()
            candidates: list[tuple[float, Point]] = []
            for region in regions:
                for point in scan_points(region, step):
                    value = state.evaluate(point)
                    if value is None:
                        break
                    select_candidate(
                        candidates, point, state.sign * value, step, parameters.regions
                    )
                if state.reason:
                    break
            state.finish_stage(step, len(regions))
            if state.reason:
                break
            if state.best_point is None:
                state.reason = "Допустимых решений нет"
                break
            if step <= parameters.precision:
                state.reason = "Завершено уточнение с заданным шагом"
                break
            old_step = step
            step = max(parameters.precision, step / 2)
            regions = refinement_regions(problem.constraints, candidates, old_step)
        return state.result(self.metadata.name)
