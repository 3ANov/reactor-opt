import json
from pathlib import Path

from sqlalchemy import select, update

from core.database import methods
from core.plugin_loader import create_method_registry
from core.registry import MethodRegistry
from core.storage import SQLiteStorage
from core.tasks import TaskLibrary
from plugin_api import AlgorithmParameters


def test_older_database_adds_nelder_mead_and_preserves_saved_settings(tmp_path: Path) -> None:
    database = tmp_path / "methods.sqlite"
    directory = tmp_path / "empty-tasks"
    variable_defaults = AlgorithmParameters(precision=0.05, initial_step=2, regions=3)
    loaded = create_method_registry()
    legacy = MethodRegistry()
    for method_id in ("variable", "fixed"):
        legacy.register(loaded.get(method_id).implementation, builtin=True)

    storage = SQLiteStorage(database, tasks_directory=directory)
    try:
        storage.attach(TaskLibrary(), legacy)
        legacy.update_metadata("variable", name="Сохранённое название", defaults=variable_defaults)
        legacy.set_enabled("fixed", False)
        with storage.engine.begin() as connection:
            assert set(connection.scalars(select(methods.c.id))) == {"variable", "fixed"}
            records = connection.execute(select(methods.c.id, methods.c.defaults_json)).all()
            for method_id, defaults_json in records:
                defaults = json.loads(defaults_json)
                del defaults["function_precision"]
                del defaults["stopping_criterion"]
                connection.execute(
                    update(methods)
                    .where(methods.c.id == method_id)
                    .values(defaults_json=json.dumps(defaults))
                )
    finally:
        storage.close()

    storage = SQLiteStorage(database, tasks_directory=directory)
    try:
        registry = create_method_registry()
        storage.attach(TaskLibrary(), registry)
        assert [method.metadata.id for method in registry.list_methods()] == [
            "nelder-mead",
            "variable",
            "fixed",
        ]
        assert registry.list_methods()[0].metadata.enabled
        assert registry.get("nelder-mead").metadata.defaults == AlgorithmParameters()
        assert registry.get("variable").metadata.name == "Сохранённое название"
        assert registry.get("variable").metadata.defaults == variable_defaults
        assert registry.get("variable").metadata.defaults.function_precision == 1e-6
        assert registry.get("variable").metadata.defaults.stopping_criterion == "coordinates"
        assert not registry.get("fixed").metadata.enabled
        with storage.engine.connect() as connection:
            assert set(connection.scalars(select(methods.c.id))) == {
                "nelder-mead",
                "variable",
                "fixed",
            }

        registry.set_enabled("nelder-mead", False)
    finally:
        storage.close()

    storage = SQLiteStorage(database, tasks_directory=directory)
    try:
        registry = create_method_registry()
        storage.attach(TaskLibrary(), registry)
        assert not registry.get("nelder-mead").metadata.enabled
        assert not registry.get("fixed").metadata.enabled
        assert registry.get("variable").metadata.defaults == variable_defaults
        assert [
            method.metadata.id for method in registry.list_methods() if method.metadata.enabled
        ] == ["variable"]
    finally:
        storage.close()


def test_function_criterion_and_precision_survive_saving_and_reopening(tmp_path: Path) -> None:
    database = tmp_path / "methods.sqlite"
    directory = tmp_path / "empty-tasks"
    defaults = AlgorithmParameters(
        precision=0.02,
        initial_step=2.5,
        max_evaluations=1234,
        regions=3,
        keep_history=True,
        function_precision=0.00025,
        stopping_criterion="function",
    )
    storage = SQLiteStorage(database, tasks_directory=directory)
    try:
        registry = create_method_registry()
        storage.attach(TaskLibrary(), registry)
        registry.update_metadata("nelder-mead", defaults=defaults)
        with storage.engine.connect() as connection:
            saved = connection.scalar(
                select(methods.c.defaults_json).where(methods.c.id == "nelder-mead")
            )
        saved_parameters = json.loads(saved)
        assert saved_parameters["function_precision"] == defaults.function_precision
        assert saved_parameters["stopping_criterion"] == "function"
    finally:
        storage.close()

    storage = SQLiteStorage(database, tasks_directory=directory)
    try:
        registry = create_method_registry()
        storage.attach(TaskLibrary(), registry)
        assert registry.get("nelder-mead").metadata.defaults == defaults
    finally:
        storage.close()
