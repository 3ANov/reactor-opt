import pytest

from core.constraints import LinearConstraints2D, PredicateConstraints2D
from core.problem import OptimizationProblem
from plugin_api import AlgorithmParameters
from plugins.methods.nelder_mead import NelderMeadMethod


@pytest.mark.parametrize("direction, sign, scale", [("minimize", 1, 1e-20), ("maximize", -1, 1e20)])
def test_interior_optimum_with_extreme_objective_scale(
    direction: str, sign: int, scale: float
) -> None:
    problem = OptimizationProblem(
        lambda x, y: sign * scale * ((x - 1.234) ** 2 + 3 * (y - 2.345) ** 2),
        LinearConstraints2D(((0, 4), (0, 4))),
        direction,
    )
    result = NelderMeadMethod().run(problem, AlgorithmParameters(precision=1e-5))
    assert result.point == pytest.approx((1.234, 2.345), abs=1e-5)
    assert result.value / scale == pytest.approx(0, abs=1e-10)


@pytest.mark.parametrize(
    "constraints, expected",
    [
        (LinearConstraints2D(((0, 1), (0, 1))), (1, 0.4)),
        (LinearConstraints2D(((0, 1), (0, 1)), ((1, 1, 1),)), (0.9, 0.1)),
        (LinearConstraints2D(((0, 1), (0, 1)), ((1, 1, 0.5),)), (0.5, 0)),
    ],
)
def test_boundary_optima_and_feasible_objective_calls(constraints, expected) -> None:
    def objective(x, y):
        assert constraints.contains((x, y)), f"Недопустимая точка {(x, y)}"
        return (x - 1.2) ** 2 + (y - 0.4) ** 2

    problem = OptimizationProblem(objective, constraints, "minimize")
    result = NelderMeadMethod().run(problem, AlgorithmParameters(precision=1e-5))
    assert constraints.contains(result.point)
    assert result.point == pytest.approx(expected, abs=1e-5)


def test_rotated_convex_bowl_does_not_collapse_onto_boundary() -> None:
    constraints = LinearConstraints2D(((-2, 2), (-2, 2)))

    def objective(x, y):
        assert constraints.contains((x, y))
        dx, dy = x - 1.75, y + 1.75
        return (0.15 * dx + 0.99 * dy) ** 2 + 80 * (-0.99 * dx + 0.15 * dy) ** 2

    result = NelderMeadMethod().run(
        OptimizationProblem(objective, constraints, "minimize"),
        AlgorithmParameters(precision=1e-5),
    )
    assert result.point == pytest.approx((1.75, -1.75), abs=1e-5)
    assert result.value == pytest.approx(0, abs=1e-10)


def test_small_coordinate_domain() -> None:
    target = (3e-10, 7e-10)
    problem = OptimizationProblem(
        lambda x, y: (x - target[0]) ** 2 + (y - target[1]) ** 2,
        LinearConstraints2D(((0, 1e-9), (0, 1e-9))),
        "minimize",
    )
    result = NelderMeadMethod().run(
        problem, AlgorithmParameters(precision=1e-13, initial_step=1e-10)
    )
    assert result.point == pytest.approx(target, abs=1e-13)


def test_rotated_narrow_polygon_remains_two_dimensional() -> None:
    width = 1e-7
    constraints = LinearConstraints2D(
        ((-0.4 - 0.8 * width, 4.4 + 0.8 * width), (-6.2 - 0.6 * width, 0.2 + 0.6 * width)),
        ((0.6, 0.8, 2.8), (-0.6, -0.8, 5.2), (-0.8, 0.6, -3.4 + width), (0.8, -0.6, 3.4 + width)),
    )
    target = (2.42 - 1.6e-8, -2.44 + 1.2e-8)

    def objective(x, y):
        assert constraints.contains((x, y))
        return (x - target[0]) ** 2 + (y - target[1]) ** 2

    result = NelderMeadMethod().run(
        OptimizationProblem(objective, constraints, "minimize"),
        AlgorithmParameters(precision=1e-9, initial_step=0.5, max_evaluations=5000),
    )
    assert result.point == pytest.approx(target, abs=1e-9)
    assert result.evaluations < 5000


def test_cancellation_before_first_evaluation() -> None:
    problem = OptimizationProblem(
        lambda x, y: pytest.fail("Функция не должна вычисляться после отмены"),
        LinearConstraints2D(((0, 1), (0, 1))),
    )
    result = NelderMeadMethod().run(problem, AlgorithmParameters(), cancelled=lambda: True)
    assert result.evaluations == 0
    assert result.reason == "Расчёт отменён"
    assert result.point is result.value is None


@pytest.mark.parametrize(
    "constraints, expected",
    [
        (LinearConstraints2D(((0, 3), (0.5, 0.5))), (1.3, 0.5)),
        (LinearConstraints2D(((0, 3), (0, 3)), ((1, -1, 0), (-1, 1, 0))), (0.9, 0.9)),
        (LinearConstraints2D(((1, 1), (2, 2))), (1, 2)),
    ],
)
def test_degenerate_line_and_point_domains(constraints, expected) -> None:
    def objective(x, y):
        assert constraints.contains((x, y))
        return (x - 1.3) ** 2 + (y - 0.5) ** 2

    problem = OptimizationProblem(objective, constraints, "minimize")
    result = NelderMeadMethod().run(problem, AlgorithmParameters(precision=1e-6))
    assert result.point == pytest.approx(expected, abs=1e-6)
    assert constraints.contains(result.point)


def test_history_is_optional_and_bounded() -> None:
    problem = OptimizationProblem(lambda x, y: 1, LinearConstraints2D(((0, 1), (0, 1))))
    without_history = NelderMeadMethod().run(problem, AlgorithmParameters())
    assert not without_history.best_history and not without_history.stage_history
    with_history = NelderMeadMethod().run(
        problem,
        AlgorithmParameters(precision=1e-210, max_evaluations=25_000, keep_history=True),
    )
    assert len(with_history.best_history) <= 2000
    assert len(with_history.stage_history) == 2000
    assert with_history.history_truncated


def test_nonfinite_objective_is_rejected() -> None:
    problem = OptimizationProblem(lambda x, y: float("nan"), LinearConstraints2D(((0, 1), (0, 1))))
    with pytest.raises(ValueError, match="неконечное значение"):
        NelderMeadMethod().run(problem, AlgorithmParameters())


def test_predicate_constraints_are_unsupported() -> None:
    problem = OptimizationProblem(
        lambda x, y: x + y,
        PredicateConstraints2D(((0, 1), (0, 1)), (lambda x, y: x * x + y * y <= 1,)),
    )
    with pytest.raises(ValueError, match="линейные ограничения"):
        NelderMeadMethod().run(problem, AlgorithmParameters())
