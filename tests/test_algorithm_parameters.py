import pytest

from plugin_api import AlgorithmParameters


@pytest.mark.parametrize("value", (0, float("nan")))
def test_function_precision_rejects_nonpositive_or_nonfinite_values(value: float) -> None:
    with pytest.raises(ValueError, match="Точность по функции"):
        AlgorithmParameters(function_precision=value)


def test_function_precision_is_independent_of_coordinate_precision() -> None:
    parameters = AlgorithmParameters(precision=0.1, initial_step=0.5, function_precision=1e-12)
    assert parameters.function_precision == 1e-12


def test_existing_positional_parameters_keep_their_meaning() -> None:
    parameters = AlgorithmParameters(0.1, 0.5, 123, 3, True)
    assert parameters.precision == 0.1
    assert parameters.initial_step == 0.5
    assert parameters.max_evaluations == 123
    assert parameters.regions == 3
    assert parameters.keep_history
    assert parameters.function_precision == 1e-6
    assert parameters.stopping_criterion == "coordinates"


@pytest.mark.parametrize("value", ("unknown", None))
def test_stopping_criterion_rejects_unknown_values(value: object) -> None:
    with pytest.raises(ValueError, match="Критерий остановки"):
        AlgorithmParameters(stopping_criterion=value)


@pytest.mark.parametrize("criterion", ("function", "evaluations"))
def test_unused_coordinate_precision_does_not_constrain_initial_step(criterion: str) -> None:
    parameters = AlgorithmParameters(
        precision=0.1, initial_step=0.001, stopping_criterion=criterion
    )
    assert parameters.initial_step == 0.001


def test_coordinate_criterion_requires_initial_step_at_least_precision() -> None:
    with pytest.raises(ValueError, match="Начальный шаг не может быть меньше точности"):
        AlgorithmParameters(precision=0.1, initial_step=0.001)


@pytest.mark.parametrize("value", (0, float("inf")))
def test_initial_step_remains_finite_and_positive(value: float) -> None:
    with pytest.raises(ValueError, match="Точность и начальный шаг"):
        AlgorithmParameters(initial_step=value)
