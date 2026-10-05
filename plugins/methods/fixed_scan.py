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

from ._scan import SearchState, scan_points


class FixedScanMethod(OptimizationMethod):
    metadata = MethodMetadata(
        id="fixed",
        name="Метод полного перебора с постоянным шагом",
        description="Полный просмотр сетки с заданным шагом, включая границы допустимой области. "
        "Поиск завершается после просмотра сетки, по лимиту вычислений или при отмене.",
        category="Сеточные методы",
        builtin=True,
    )
    capabilities = MethodCapabilities()
    supported_parameters = ("precision", "max_evaluations", "keep_history")

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
        for point in scan_points(problem.constraints, parameters.precision):
            if state.evaluate(point) is None:
                break
        state.finish_stage(parameters.precision, regions=1)
        if not state.reason:
            state.reason = (
                "Допустимых решений нет"
                if state.best_point is None
                else "Сетка с заданным шагом просмотрена"
            )
        return state.result(self.metadata.name)
