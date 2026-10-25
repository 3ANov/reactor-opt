import json
from dataclasses import replace
from pathlib import Path

import pytest

from core.plugin_loader import plugin_directory
from core.storage import SQLiteStorage, task_from_record, task_to_record
from core.tasks import TaskDefinition


@pytest.fixture
def builtin_tasks() -> dict[str, TaskDefinition]:
    storage = SQLiteStorage(":memory:")
    try:
        return {task.id: task for task in storage.load_tasks()}
    finally:
        storage.close()


def test_builtin_tasks_offer_maximization_and_minimization(
    builtin_tasks: dict[str, TaskDefinition],
) -> None:
    assert list(builtin_tasks) == ["reactor-output", "mixing-cost"]
    assert all(task.builtin for task in builtin_tasks.values())
    assert builtin_tasks["reactor-output"].problem.direction == "maximize"
    assert builtin_tasks["mixing-cost"].problem.direction == "minimize"


def test_mixing_task_formula_and_constraints(builtin_tasks: dict[str, TaskDefinition]) -> None:
    task = builtin_tasks["mixing-cost"]
    assert task.problem.objective(2, 3) == 0
    assert task.problem.objective(3, 3) == 1
    assert task.problem.objective(2, 4) == 2
    assert task.problem.constraints.contains((2, 3))
    assert task.problem.constraints.contains((6, 2))
    assert not task.problem.constraints.contains((5, 5))
    assert not task.problem.constraints.contains((-1, 3))
    assert not task.problem.constraints.contains((2, 7))


def test_mixing_task_roundtrip_preserves_formula_and_display_metadata(
    builtin_tasks: dict[str, TaskDefinition],
) -> None:
    original = builtin_tasks["mixing-cost"]
    restored = task_from_record(task_to_record(original))
    assert restored.formula == original.formula
    assert dict(restored.coefficients) == {"a": 1, "b": 2, "x_target": 2, "y_target": 3}
    assert restored.variable_names == ("Q₁", "Q₂")
    assert restored.variable_units == ("м³/ч", "м³/ч")
    assert (restored.objective_label, restored.objective_unit) == ("J", "")
    assert restored.problem.objective(2, 3) == 0


def test_existing_database_imports_new_builtin_without_overwriting_edits(tmp_path: Path) -> None:
    definitions = json.loads(
        (plugin_directory() / "tasks" / "default_tasks.json").read_text(encoding="utf-8")
    )
    directory = tmp_path / "plugins"
    directory.mkdir()
    task_file = directory / "tasks.json"
    task_file.write_text(json.dumps(definitions[:1]), encoding="utf-8")
    database = tmp_path / "tasks.sqlite"
    storage = SQLiteStorage(database, tasks_directory=directory)
    try:
        reactor = storage.load_tasks()[0]
        storage.save_task(replace(reactor, name="Сохранённая задача реакторов"))
    finally:
        storage.close()

    task_file.write_text(json.dumps(definitions), encoding="utf-8")
    storage = SQLiteStorage(database, tasks_directory=directory)
    try:
        loaded = {task.id: task for task in storage.load_tasks()}
        assert len(loaded) == 2
        assert loaded["reactor-output"].name == "Сохранённая задача реакторов"
        assert loaded["mixing-cost"].problem.objective(2, 3) == 0
    finally:
        storage.close()
