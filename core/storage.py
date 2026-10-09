import json
from collections.abc import Mapping
from dataclasses import asdict, replace
from pathlib import Path

from sqlalchemy import URL, create_engine, delete, select
from sqlalchemy.dialects.sqlite import insert

from core.auth import Session
from core.database import metadata, methods, tasks
from core.expressions import DerivedMetric, ExpressionModel
from core.plugin_loader import plugin_directory
from core.registry import MethodRegistry
from core.tasks import TaskDefinition, TaskLibrary, linear_constraints, model_task
from plugin_api import AlgorithmParameters, LinearConstraintSet, MethodMetadata


def json_text(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(text) > 65536:
        raise ValueError("Данные задания слишком велики.")
    return text


def json_data(text: str):
    if len(text) > 65536:
        raise ValueError("Данные задания слишком велики.")
    return json.loads(text)


def task_to_record(task: TaskDefinition) -> dict:
    if not isinstance(task.problem.constraints, LinearConstraintSet):
        raise ValueError("Хранилище пока поддерживает только линейные ограничения двух переменных.")
    model = task.model_parameters
    if not isinstance(model, ExpressionModel):
        raise ValueError("Для сохранения нужна модель с формулой.")
    if task.problem.objective != model.objective:
        raise ValueError("Целевая функция не соответствует параметрам сохраняемой модели.")
    symbols = model.variables
    variables = [
        {"name": symbol, "display_name": name, "unit": unit, "min": low, "max": high}
        for symbol, name, unit, (low, high) in zip(
            symbols,
            task.variable_names,
            task.variable_units,
            task.problem.constraints.bounds,
        )
    ]
    constraints = [
        {"coefficients": dict(zip(symbols, (a, b))), "operator": "<=", "value": c}
        for a, b, c in task.problem.constraints.inequalities
    ]
    metrics = [
        {
            "id": metric.key,
            "name": metric.name,
            "expression": metric.formula,
            "unit": metric.unit,
            "parameters": dict(metric.parameters),
            "symbol": metric.symbol,
        }
        for metric in task.derived_metrics
    ]
    parameters = dict(task.coefficients)
    return {
        "id": task.id,
        "name": task.name,
        "description": task.description,
        "objective": task.formula,
        "direction": "max" if task.problem.direction == "maximize" else "min",
        "variables_json": json_text(variables),
        "parameters_json": json_text(parameters),
        "constraints_json": json_text(constraints),
        "derived_metrics_json": json_text(metrics),
        "is_builtin": int(task.builtin),
        "objective_label": task.objective_label,
        "objective_unit": task.objective_unit,
    }


def task_from_record(record: Mapping) -> TaskDefinition:
    variables = json_data(record["variables_json"])
    if len(variables) != 2:
        raise ValueError("В задании должно быть ровно две переменные.")
    names = tuple(variable["name"] for variable in variables)
    parameters = json_data(record["parameters_json"])
    raw_metrics = json_data(record["derived_metrics_json"])
    if len(raw_metrics) > 16:
        raise ValueError("Допустимо до 16 дополнительных показателей.")
    metrics = tuple(
        DerivedMetric(
            row["id"],
            row["name"],
            row["expression"],
            row["unit"],
            tuple(row["parameters"].items()),
            row.get("symbol", ""),
        )
        for row in raw_metrics
    )
    model = ExpressionModel(record["objective"], names, tuple(parameters.items()))
    raw_constraints = json_data(record["constraints_json"])
    if len(raw_constraints) > 64:
        raise ValueError("Слишком много ограничений.")
    rows = []
    for row in raw_constraints:
        if set(row["coefficients"]) != set(names):
            raise ValueError("Переменные в ограничении не соответствуют заданию.")
        rows.append((*(row["coefficients"][name] for name in names), row["operator"], row["value"]))
    constraints = linear_constraints(tuple((v["min"], v["max"]) for v in variables), rows)
    if record["direction"] not in ("min", "max"):
        raise ValueError("Направление задания должно быть min или max.")
    task = model_task(
        record["id"],
        record["name"],
        record["description"],
        model,
        constraints,
        "maximize" if record["direction"] == "max" else "minimize",
        names,
        tuple(v["unit"] for v in variables),
        metrics,
        record["objective_label"],
        record["objective_unit"],
    )
    return replace(
        task,
        builtin=bool(record["is_builtin"]),
        variable_names=tuple(v.get("display_name", v["name"]) for v in variables),
    )


class SQLiteStorage:
    def __init__(self, path: str | Path, *, tasks_directory: Path | None = None) -> None:
        self._session: Session | None = None
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            URL.create("sqlite", database=str(path)),
            hide_parameters=True,
        )
        try:
            metadata.create_all(self.engine)
            self._import_json_tasks(tasks_directory or plugin_directory() / "tasks")
        except Exception:
            self.engine.dispose()
            raise

    def bind_session(self, session: Session) -> None:
        if self._session is not None:
            raise ValueError("Контекст доступа уже установлен.")
        session.user
        self._session = session

    def _require_write(self) -> None:
        if self._session is not None:
            self._session.require_admin()

    def close(self) -> None:
        self.engine.dispose()

    def _import_json_tasks(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        with self.engine.begin() as connection:
            existing_ids = set(connection.scalars(select(tasks.c.id)))
            for path in sorted(directory.glob("*.json")):
                try:
                    definitions = json.loads(path.read_text(encoding="utf-8-sig"))
                    if isinstance(definitions, dict):
                        definitions = [definitions]
                    if not isinstance(definitions, list):
                        raise ValueError("Нужен объект задания или список заданий.")
                    for definition in definitions:
                        if not isinstance(definition, dict):
                            raise ValueError("Каждое задание должно быть JSON-объектом.")
                        if definition["id"] in existing_ids:
                            continue
                        record = self._json_task_record(definition)
                        task_from_record(record)
                        statement = insert(tasks).values(**record)
                        connection.execute(
                            statement.on_conflict_do_nothing(index_elements=[tasks.c.id])
                        )
                        existing_ids.add(definition["id"])
                except (ValueError, KeyError, TypeError, ArithmeticError) as error:
                    raise ValueError(
                        f"Не удалось загрузить задания из {path.name}: {error}"
                    ) from error

    @staticmethod
    def _json_task_record(definition: Mapping) -> dict:
        return {
            "id": definition["id"],
            "name": definition["name"],
            "description": definition["description"],
            "objective": definition["objective"],
            "direction": definition["direction"],
            "variables_json": json_text(definition["variables"]),
            "parameters_json": json_text(definition.get("coefficients", {})),
            "constraints_json": json_text(definition.get("constraints", [])),
            "derived_metrics_json": json_text(definition.get("derived_metrics", [])),
            "is_builtin": int(definition.get("builtin", False)),
            "objective_label": definition.get("objective_label", "f"),
            "objective_unit": definition.get("objective_unit", ""),
        }

    def load_tasks(self) -> tuple[TaskDefinition, ...]:
        with self.engine.connect() as connection:
            records = connection.execute(
                select(tasks).order_by(tasks.c.is_builtin.desc(), tasks.c.name, tasks.c.id)
            ).mappings()
            try:
                return tuple(task_from_record(record) for record in records)
            except (
                ValueError,
                KeyError,
                TypeError,
                ArithmeticError,
                AttributeError,
                IndexError,
                RecursionError,
            ) as error:
                raise ValueError(f"Не удалось загрузить задания из базы: {error}") from error

    def attach(self, library: TaskLibrary, registry: MethodRegistry) -> None:
        library.load(self.load_tasks())
        self._load_method_settings(registry)
        for method in registry.list_methods():
            self.save_method(method.metadata)
        library.set_persistence(self.save_task, self.delete_task)
        registry.set_persistence(self.save_method, self.delete_method)

    def _load_method_settings(self, registry: MethodRegistry) -> None:
        with self.engine.connect() as connection:
            records = connection.execute(select(methods)).mappings().all()
        available_ids = {method.metadata.id for method in registry.list_methods()}
        for record in records:
            if record["id"] not in available_ids:
                continue
            registry.update_metadata(
                record["id"],
                name=record["name"],
                description=record["description"],
                category=record["category"],
                enabled=bool(record["enabled"]),
                defaults=AlgorithmParameters(**json.loads(record["defaults_json"])),
            )

    def save_task(self, task: TaskDefinition) -> None:
        self._require_write()
        record = task_to_record(task)
        task_from_record(record)
        statement = insert(tasks).values(**record)
        with self.engine.begin() as connection:
            saved = connection.scalar(select(tasks.c.is_builtin).where(tasks.c.id == task.id))
            if bool(saved) != task.builtin:
                raise ValueError("Нельзя менять происхождение задания.")
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[tasks.c.id],
                    set_={
                        column.name: statement.excluded[column.name]
                        for column in tasks.columns
                        if not column.primary_key
                    },
                )
            )

    def delete_task(self, task_id: str) -> None:
        self._require_write()
        with self.engine.begin() as connection:
            builtin = connection.scalar(select(tasks.c.is_builtin).where(tasks.c.id == task_id))
            if builtin:
                raise ValueError("Встроенную задачу нельзя удалить.")
            connection.execute(delete(tasks).where(tasks.c.id == task_id))

    def save_method(self, metadata: MethodMetadata) -> None:
        self._require_write()
        record = {
            "id": metadata.id,
            "name": metadata.name,
            "description": metadata.description,
            "category": metadata.category,
            "enabled": int(metadata.enabled),
            "defaults_json": json.dumps(asdict(metadata.defaults), allow_nan=False),
        }
        statement = insert(methods).values(**record)
        with self.engine.begin() as connection:
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[methods.c.id],
                    set_={name: statement.excluded[name] for name in record if name != "id"},
                )
            )

    def delete_method(self, method_id: str) -> None:
        self._require_write()
        with self.engine.begin() as connection:
            connection.execute(delete(methods).where(methods.c.id == method_id))
