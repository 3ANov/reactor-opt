from dataclasses import replace

import pytest

from core.constraints import LinearConstraints2D
from core.plugin_loader import create_method_registry
from core.problem import OptimizationProblem
from plugin_api import AlgorithmParameters

METHODS = ("fixed", "variable", "nelder-mead")
FUNCTION_REASON = "Достигнута точность по функции"


@pytest.fixture(scope="module")
def registry():
    return create_method_registry()


@pytest.mark.parametrize("method_id", METHODS)
def test_function_precision_can_end_search_before_coordinate_precision(registry, method_id):
    problem = OptimizationProblem(
        lambda x, y: (x - 0.3) ** 2 + (y - 0.7) ** 2,
        LinearConstraints2D(((0, 1), (0, 1))),
        "minimize",
    )
    parameters = AlgorithmParameters(
        precision=0.025,
        initial_step=0.5,
        function_precision=1,
        max_evaluations=5000,
    )

    coordinates = registry.run(method_id, problem, parameters)
    function = registry.run(method_id, problem, replace(parameters, stopping_criterion="function"))
    evaluations = registry.run(
        method_id, problem, replace(parameters, stopping_criterion="evaluations")
    )

    assert function.reason == FUNCTION_REASON
    assert function.evaluations < coordinates.evaluations
    assert function.evaluations < evaluations.evaluations
    assert coordinates.reason != FUNCTION_REASON
    assert evaluations.reason != FUNCTION_REASON
    assert problem.constraints.contains(function.point)


@pytest.mark.parametrize("direction, sign", [("maximize", 1), ("minimize", -1)])
def test_fixed_function_precision_does_not_interrupt_boundary_initialization(
    registry, direction, sign
):
    problem = OptimizationProblem(
        lambda x, y: sign * (x + 0.01 * y),
        LinearConstraints2D(((0, 2), (0, 2))),
        direction,
    )
    parameters = AlgorithmParameters(
        precision=0.25,
        function_precision=0.1,
        stopping_criterion="function",
        keep_history=True,
    )

    result = registry.run("fixed", problem, parameters)
    assert result.point == (2, 2)
    assert result.value == pytest.approx(sign * 2.02)
    assert result.reason == "Сетка с заданным шагом просмотрена"


def test_equal_samples_do_not_stop_fixed_scan_before_better_interior_point(registry):
    problem = OptimizationProblem(
        lambda x, y: 10.0 if (x, y) == (1, 1) else 0.0,
        LinearConstraints2D(((0, 2), (0, 2))),
    )

    result = registry.run(
        "fixed",
        problem,
        AlgorithmParameters(
            precision=1,
            function_precision=0.1,
            stopping_criterion="function",
        ),
    )

    assert result.point == (1, 1)
    assert result.value == 10
    assert result.reason == "Сетка с заданным шагом просмотрена"


@pytest.mark.parametrize(
    "method_id, reason",
    [
        ("fixed", "Сетка с заданным шагом просмотрена"),
        ("variable", "Завершено уточнение с заданным шагом"),
    ],
)
def test_evaluation_mode_does_not_pad_completed_grid_with_extra_calls(registry, method_id, reason):
    calls = []

    def objective(x, y):
        calls.append((x, y))
        return x + y

    parameters = AlgorithmParameters(
        precision=1,
        initial_step=1,
        stopping_criterion="evaluations",
        max_evaluations=1000,
    )
    result = registry.run(
        method_id,
        OptimizationProblem(objective, LinearConstraints2D(((0, 1), (0, 1)))),
        parameters,
    )

    assert result.reason == reason
    assert result.evaluations == len(calls) < parameters.max_evaluations
    assert result.point == (1, 1)
    assert result.value == 2


@pytest.mark.parametrize(
    "method_id, criterion, limit",
    [
        ("fixed", "coordinates", 1),
        ("fixed", "function", 2),
        ("variable", "coordinates", 2),
        ("variable", "evaluations", 3),
        ("nelder-mead", "function", 1),
        ("nelder-mead", "evaluations", 17),
    ],
)
def test_evaluation_budget_remains_exact(registry, method_id, criterion, limit):
    calls = []
    constraints = LinearConstraints2D(((0, 2), (0, 2)))

    def objective(x, y):
        assert constraints.contains((x, y))
        calls.append((x, y))
        return x + 2 * y

    result = registry.run(
        method_id,
        OptimizationProblem(objective, constraints),
        AlgorithmParameters(
            precision=1e-8,
            function_precision=1e-12,
            stopping_criterion=criterion,
            max_evaluations=limit,
            keep_history=True,
        ),
    )

    assert result.evaluations == len(calls) == limit
    assert result.reason == "Достигнут лимит вычислений"
    assert result.value == max(x + 2 * y for x, y in calls)
    assert sum(stage.evaluations for stage in result.stage_history) == limit


@pytest.mark.parametrize(
    "method_id, criterion",
    [("fixed", "coordinates"), ("variable", "function"), ("nelder-mead", "evaluations")],
)
def test_collecting_history_does_not_change_stopping_behavior(registry, method_id, criterion):
    problem = OptimizationProblem(
        lambda x, y: (x - 0.3) ** 2 + (y - 0.7) ** 2,
        LinearConstraints2D(((0, 1), (0, 1))),
        "minimize",
    )
    parameters = AlgorithmParameters(
        precision=0.1,
        initial_step=0.5,
        function_precision=0.01,
        stopping_criterion=criterion,
        max_evaluations=2000,
    )

    without_history = registry.run(method_id, problem, parameters)
    with_history = registry.run(method_id, problem, replace(parameters, keep_history=True))

    assert without_history.point == with_history.point
    assert without_history.value == with_history.value
    assert without_history.reason == with_history.reason
    assert without_history.evaluations == with_history.evaluations
    assert without_history.stages == with_history.stages
    assert not without_history.best_history and not without_history.stage_history
    assert with_history.best_history and with_history.stage_history
    assert (
        sum(stage.evaluations for stage in with_history.stage_history) == with_history.evaluations
    )


@pytest.mark.parametrize(
    "method_id, criterion",
    [("fixed", "function"), ("variable", "coordinates"), ("nelder-mead", "evaluations")],
)
def test_cancellation_is_independent_of_selected_accuracy_criterion(registry, method_id, criterion):
    calls = []

    def objective(x, y):
        calls.append((x, y))
        return x + 2 * y

    result = registry.run(
        method_id,
        OptimizationProblem(objective, LinearConstraints2D(((0, 2), (0, 2)))),
        AlgorithmParameters(
            precision=1e-8,
            function_precision=1e-12,
            stopping_criterion=criterion,
        ),
        cancelled=lambda: len(calls) >= 2,
    )

    assert result.evaluations == len(calls) == 2
    assert result.reason == "Расчёт отменён"
    assert result.value == max(x + 2 * y for x, y in calls)


def test_nelder_mead_function_criterion_does_not_require_coordinate_precision(registry):
    problem = OptimizationProblem(
        lambda x, y: 7,
        LinearConstraints2D(((0, 1), (0, 1))),
    )
    parameters = AlgorithmParameters(
        precision=0.1,
        initial_step=0.5,
        function_precision=1e-6,
        stopping_criterion="function",
        max_evaluations=5000,
    )

    coarse_coordinates = registry.run("nelder-mead", problem, parameters)
    fine_coordinates = registry.run("nelder-mead", problem, replace(parameters, precision=1e-8))
    coordinate_mode = registry.run(
        "nelder-mead",
        problem,
        replace(parameters, precision=1e-8, stopping_criterion="coordinates"),
    )

    assert coarse_coordinates.reason == fine_coordinates.reason == FUNCTION_REASON
    assert coarse_coordinates.evaluations == fine_coordinates.evaluations
    assert coarse_coordinates.point == fine_coordinates.point
    assert coarse_coordinates.value == fine_coordinates.value == 7
    assert fine_coordinates.evaluations < coordinate_mode.evaluations


def test_smaller_function_precision_requires_more_nelder_mead_search(registry):
    problem = OptimizationProblem(
        lambda x, y: (x - 0.3) ** 2 + (y - 0.7) ** 2,
        LinearConstraints2D(((0, 1), (0, 1))),
        "minimize",
    )
    parameters = AlgorithmParameters(
        precision=1e-10,
        initial_step=0.5,
        function_precision=0.01,
        stopping_criterion="function",
        max_evaluations=5000,
    )

    coarse = registry.run("nelder-mead", problem, parameters)
    fine = registry.run("nelder-mead", problem, replace(parameters, function_precision=1e-10))

    assert coarse.reason == fine.reason == FUNCTION_REASON
    assert coarse.evaluations < fine.evaluations
