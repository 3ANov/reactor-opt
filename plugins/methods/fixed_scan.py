from collections.abc import Callable

from plugin_api import (
    AlgorithmParameters,
    LinearConstraintSet,
    MethodCapabilities,
    MethodMetadata,
    OptimizationMethod,
    OptimizationResult,
    OptimizationTask,
)

from ._scan import scan_points
from ._search import SearchState


class FixedScanMethod(OptimizationMethod):
    metadata = MethodMetadata(
        id="fixed",
        name="Метод полного перебора с постоянным шагом",
        description="Просмотр сетки с заданным шагом, включая границы допустимой области. "
        "В режиме точности по координатам просматривается вся сетка; "
        "точность по функции допускает ранний останов между улучшениями результата. "
        "Лимит вычислений и отмена действуют во всех режимах.",
        category="Сеточные методы",
        builtin=True,
    )
    capabilities = MethodCapabilities()
    supported_parameters = (
        "precision",
        "function_precision",
        "stopping_criterion",
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
        state.begin_stage()
        # Vertices establish the initial approximation; they are not convergence steps.
        for point in problem.constraints.vertices():
            if state.evaluate(point) is None:
                break
        if not state.reason:
            for point in scan_points(
                problem.constraints, parameters.precision, include_vertices=False
            ):
                previous_point, previous_value = state.best_point, state.best_value
                if state.evaluate(point) is None:
                    break
                if state.function_converged(previous_point, previous_value):
                    break
        state.finish_stage(parameters.precision, regions=1)
        if not state.reason:
            state.reason = (
                "Допустимых решений нет"
                if state.best_point is None
                else "Сетка с заданным шагом просмотрена"
            )
        return state.result(self.metadata.name)
