import pytest

from core.constraints import LinearConstraints2D
from core.plugin_loader import create_method_registry
from core.problem import OptimizationProblem
from core.registry import MethodRegistry
from core.storage import SQLiteStorage
from core.tasks import TaskDefinition
from plugin_api import AlgorithmParameters

METHODS = ("fixed", "variable")


@pytest.fixture
def task() -> TaskDefinition:
    storage = SQLiteStorage(":memory:")
    try:
        return storage.load_tasks()[0]
    finally:
        storage.close()


@pytest.fixture
def registry() -> MethodRegistry:
    return create_method_registry()


def test_reactor_formula(task: TaskDefinition) -> None:
    assert task.problem.objective(1, 7) == 1858
    assert task.problem.objective(3, 2) == 0
    assert task.derived_metrics[0].calculate(1858).value == 14864


def test_constraints_and_boundaries(task: TaskDefinition) -> None:
    constraints = task.problem.constraints
    assert set(constraints.vertices()) == {(1, 1), (7, 1), (1, 7)}
    assert constraints.contains((1, 7))
    assert not constraints.contains((5, 5))
    assert not constraints.contains((0, 1))
    with pytest.raises(ValueError):
        LinearConstraints2D(((2, 1), (0, 1)))


@pytest.mark.parametrize("method_id", METHODS)
def test_reference_result(method_id: str, task: TaskDefinition, registry: MethodRegistry) -> None:
    parameters = AlgorithmParameters(precision=0.01, keep_history=True)
    result = registry.run(method_id, task.problem, parameters)
    assert result.point == pytest.approx((1, 7), abs=0.01)
    assert result.value == pytest.approx(1858, abs=0.01)
    assert result.derived_metrics[0].value == pytest.approx(14864, abs=0.1)
    assert task.problem.constraints.contains(result.point)
    assert result.best_history and result.stage_history


@pytest.mark.parametrize("method_id", METHODS)
def test_minimization(method_id: str, registry: MethodRegistry) -> None:
    problem = OptimizationProblem(
        lambda x, y: (x - 2) ** 2 + (y - 3) ** 2,
        LinearConstraints2D(((0, 4), (0, 4))),
        "minimize",
    )
    result = registry.run(method_id, problem, AlgorithmParameters(precision=0.01))
    assert result.point == pytest.approx((2, 3), abs=0.01)
    assert result.value == pytest.approx(0, abs=0.001)


@pytest.mark.parametrize("method_id", METHODS)
def test_evaluation_limit(method_id: str, registry: MethodRegistry) -> None:
    calls = []
    problem = OptimizationProblem(
        lambda x, y: calls.append((x, y)) or x + y,
        LinearConstraints2D(((0, 1), (0, 1))),
    )
    result = registry.run(method_id, problem, AlgorithmParameters(max_evaluations=3))
    assert result.evaluations == len(calls) == 3
    assert result.reason == "Достигнут лимит вычислений"
    assert not result.best_history and not result.stage_history


@pytest.mark.parametrize("method_id", METHODS)
def test_empty_feasible_region(method_id: str, registry: MethodRegistry) -> None:
    problem = OptimizationProblem(
        lambda x, y: pytest.fail("Функция не должна вычисляться при пустой области"),
        LinearConstraints2D(((0, 1), (0, 1)), ((1, 1, -1),)),
    )
    result = registry.run(method_id, problem)
    assert result.point is None and result.value is None
    assert result.evaluations == 0
    assert result.reason == "Допустимых решений нет"
