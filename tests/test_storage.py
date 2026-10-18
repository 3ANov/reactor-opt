import json
from dataclasses import replace
from pathlib import Path

import pytest

from core.expressions import DerivedMetric, ExpressionModel
from core.storage import SQLiteStorage, json_data, json_text, task_from_record, task_to_record
from core.tasks import TaskDefinition, linear_constraints, model_task


@pytest.fixture
def definition() -> dict:
    return {
        "id": "custom-task",
        "name": "Задание для сохранения",
        "description": "Проверка сохранения формулы, ограничений и обозначений",
        "objective": "gain*x + y",
        "direction": "min",
        "variables": [
            {"name": "x", "display_name": "Расход", "unit": "м³/ч", "min": 0, "max": 3},
            {"name": "y", "display_name": "Температура", "unit": "°C", "min": 1, "max": 4},
        ],
        "coefficients": {"gain": 2.5},
        "constraints": [
            {"coefficients": {"x": 1, "y": 1}, "operator": "<=", "value": 5},
            {"coefficients": {"x": 1, "y": -1}, "operator": ">=", "value": -2},
        ],
        "derived_metrics": [
            {
                "id": "total",
                "name": "За смену",
                "expression": "C*hours",
                "parameters": {"hours": 8},
                "unit": "кг",
                "symbol": "Q",
            }
        ],
        "objective_label": "C",
        "objective_unit": "кг/ч",
    }


@pytest.fixture
def task() -> TaskDefinition:
    model = ExpressionModel("gain*x + y", ("x", "y"), (("gain", 2.5),))
    task = model_task(
        "custom-task",
        "Задание для сохранения",
        "Проверка сохранения формулы, ограничений и обозначений",
        model,
        linear_constraints(((0, 3), (1, 4)), ((1, 1, "<=", 5), (1, -1, ">=", -2))),
        "minimize",
        ("x", "y"),
        ("м³/ч", "°C"),
        (DerivedMetric("total", "За смену", "C*hours", "кг", (("hours", 8),), "Q"),),
        "C",
        "кг/ч",
    )
    return replace(task, variable_names=("Расход", "Температура"))


def assert_task_preserved(loaded: TaskDefinition, original: TaskDefinition) -> None:
    assert loaded.id == original.id
    assert loaded.name == original.name
    assert loaded.description == original.description
    assert loaded.formula == original.formula
    assert loaded.coefficients == original.coefficients
    assert loaded.variable_names == ("Расход", "Температура")
    assert loaded.variable_units == ("м³/ч", "°C")
    assert loaded.builtin == original.builtin
    assert (loaded.objective_label, loaded.objective_unit) == ("C", "кг/ч")
    assert loaded.problem.direction == "minimize"
    assert loaded.problem.objective(1, 2) == 4.5
    assert loaded.problem.constraints.contains((1, 2))
    assert not loaded.problem.constraints.contains((3, 4))
    assert not loaded.problem.constraints.contains((0, 3))
    assert loaded.problem.constraints.bounds == original.problem.constraints.bounds
    assert set(loaded.problem.constraints.vertices()) == set(
        original.problem.constraints.vertices()
    )
    assert loaded.derived_metrics[0].calculate(4.5).value == 36
    assert loaded.derived_metrics[0].symbol == "Q"


def test_record_roundtrip_preserves_calculation_and_display_metadata(task: TaskDefinition) -> None:
    restored = task_from_record(task_to_record(task))
    assert_task_preserved(restored, task)


def test_sqlite_save_update_reopen_and_delete(tmp_path: Path, task: TaskDefinition) -> None:
    database = tmp_path / "tasks.sqlite"
    directory = tmp_path / "empty-plugins"
    storage = SQLiteStorage(database, tasks_directory=directory)
    try:
        storage.save_task(task)
        edited = replace(task, name="Исправленное название")
        storage.save_task(edited)
        assert len(storage.load_tasks()) == 1
    finally:
        storage.close()

    storage = SQLiteStorage(database, tasks_directory=directory)
    try:
        assert_task_preserved(storage.load_tasks()[0], edited)
        storage.delete_task(task.id)
        assert storage.load_tasks() == ()
    finally:
        storage.close()

    storage = SQLiteStorage(database, tasks_directory=directory)
    try:
        assert storage.load_tasks() == ()
    finally:
        storage.close()


def write_definitions(directory: Path, value: object) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "tasks.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8-sig")
    return path


def test_import_keeps_existing_task_changes(tmp_path: Path, definition: dict) -> None:
    directory = tmp_path / "plugins"
    write_definitions(directory, definition)
    database = tmp_path / "tasks.sqlite"
    storage = SQLiteStorage(database, tasks_directory=directory)
    try:
        task = storage.load_tasks()[0]
        assert task.problem.objective(1, 2) == 4.5
        storage.save_task(replace(task, name="Сохранённое изменение"))
    finally:
        storage.close()
    storage = SQLiteStorage(database, tasks_directory=directory)
    try:
        assert storage.load_tasks()[0].name == "Сохранённое изменение"
    finally:
        storage.close()


def test_builtin_task_cannot_be_deleted_or_changed_to_custom(
    tmp_path: Path, definition: dict
) -> None:
    definition["builtin"] = True
    directory = tmp_path / "plugins"
    write_definitions(directory, definition)
    storage = SQLiteStorage(":memory:", tasks_directory=directory)
    try:
        task = storage.load_tasks()[0]
        with pytest.raises(ValueError, match="нельзя удалить"):
            storage.delete_task(task.id)
        with pytest.raises(ValueError, match="происхождение"):
            storage.save_task(replace(task, builtin=False))
        assert storage.load_tasks()[0].builtin
    finally:
        storage.close()


@pytest.mark.parametrize(
    ("path", "invalid", "field"),
    [
        (("variables",), None, "variables"),
        (("variables", 0), None, "variables[0]"),
        (("variables", 0, "min"), "zero", "variables[0].min"),
        (("coefficients",), [], "coefficients"),
        (("constraints",), {}, "constraints"),
        (("constraints", 0, "coefficients"), None, "constraints[0].coefficients"),
        (("derived_metrics",), {}, "derived_metrics"),
        (("derived_metrics", 0, "parameters"), None, "derived_metrics[0].parameters"),
    ],
)
def test_invalid_json_reports_file_and_field(
    tmp_path: Path, definition: dict, path: tuple, invalid: object, field: str
) -> None:
    target = definition
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = invalid
    directory = tmp_path / "plugins"
    write_definitions(directory, definition)
    with pytest.raises(ValueError) as failure:
        SQLiteStorage(":memory:", tasks_directory=directory)
    assert "tasks.json" in str(failure.value)
    assert field in str(failure.value)


def test_invalid_json_syntax_reports_file(tmp_path: Path) -> None:
    directory = tmp_path / "plugins"
    directory.mkdir()
    (directory / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="broken.json"):
        SQLiteStorage(":memory:", tasks_directory=directory)


def test_invalid_import_rolls_back_other_tasks(tmp_path: Path, definition: dict) -> None:
    invalid = {**definition, "id": "invalid-task", "variables": None}
    directory = tmp_path / "plugins"
    write_definitions(directory, [definition, invalid])
    database = tmp_path / "tasks.sqlite"
    with pytest.raises(ValueError, match="variables"):
        SQLiteStorage(database, tasks_directory=directory)

    storage = SQLiteStorage(database, tasks_directory=tmp_path / "empty-plugins")
    try:
        assert storage.load_tasks() == ()
    finally:
        storage.close()


def test_json_helpers_preserve_unicode_and_reject_oversized_or_nonfinite_data() -> None:
    assert json_data(json_text({"единица": "м³/ч"})) == {"единица": "м³/ч"}
    with pytest.raises(ValueError):
        json_text(float("nan"))
    with pytest.raises(ValueError, match="слишком велики"):
        json_text("x" * 65537)
    with pytest.raises(ValueError, match="слишком велики"):
        json_data(" " * 65537)
