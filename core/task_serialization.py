"""Convert task definitions to storage records without depending on a database."""

import json
from collections.abc import Mapping
from dataclasses import replace

from core.expressions import DerivedMetric, ExpressionModel
from core.tasks import TaskDefinition, linear_constraints, model_task
from plugin_api import LinearConstraintSet


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
    return {
        "id": task.id,
        "name": task.name,
        "description": task.description,
        "objective": task.formula,
        "direction": "max" if task.problem.direction == "maximize" else "min",
        "variables_json": json_text(variables),
        "parameters_json": json_text(dict(task.coefficients)),
        "constraints_json": json_text(constraints),
        "derived_metrics_json": json_text(metrics),
        "is_builtin": int(task.builtin),
        "objective_label": task.objective_label,
        "objective_unit": task.objective_unit,
    }


def _object(value: object, field: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise ValueError(f"Поле «{field}» должно быть JSON-объектом.")
    return value


def _array(value: object, field: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"Поле «{field}» должно быть списком.")
    return value


def _field(row: Mapping, key: str, field: str = ""):
    path = f"{field}.{key}" if field else key
    if key not in row:
        raise ValueError(f"Отсутствует обязательное поле «{path}».")
    return row[key]


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Поле «{field}» должно быть строкой.")
    return value


def _number(value: object, field: str) -> float:
    if not isinstance(value, (int, float)):
        raise ValueError(f"Поле «{field}» должно быть числом.")
    return value


def _parameters(value: object, field: str) -> tuple[tuple[str, float], ...]:
    parameters = _object(value, field)
    return tuple(
        (_text(name, field), _number(number, f"{field}.{name}"))
        for name, number in parameters.items()
    )


def _variables(value: object) -> list[Mapping]:
    variables = _array(value, "variables")
    if len(variables) != 2:
        raise ValueError("В задании должно быть ровно две переменные.")
    for index, value in enumerate(variables):
        field = f"variables[{index}]"
        row = _object(value, field)
        for key in ("name", "unit"):
            _text(_field(row, key, field), f"{field}.{key}")
        if "display_name" in row:
            _text(row["display_name"], f"{field}.display_name")
        for key in ("min", "max"):
            _number(_field(row, key, field), f"{field}.{key}")
    return variables


def _metrics(value: object) -> tuple[DerivedMetric, ...]:
    rows = _array(value, "derived_metrics")
    if len(rows) > 16:
        raise ValueError("Допустимо до 16 дополнительных показателей.")
    metrics = []
    for index, value in enumerate(rows):
        field = f"derived_metrics[{index}]"
        row = _object(value, field)
        metrics.append(
            DerivedMetric(
                *(
                    _text(_field(row, key, field), f"{field}.{key}")
                    for key in ("id", "name", "expression", "unit")
                ),
                _parameters(_field(row, "parameters", field), f"{field}.parameters"),
                _text(row.get("symbol", ""), f"{field}.symbol"),
            )
        )
    return tuple(metrics)


def _constraints(value: object, variables: list[Mapping]):
    raw_constraints = _array(value, "constraints")
    if len(raw_constraints) > 64:
        raise ValueError("Слишком много ограничений.")
    names = tuple(variable["name"] for variable in variables)
    rows = []
    for index, value in enumerate(raw_constraints):
        field = f"constraints[{index}]"
        row = _object(value, field)
        coefficients = _object(_field(row, "coefficients", field), f"{field}.coefficients")
        if set(coefficients) != set(names):
            raise ValueError("Переменные в ограничении не соответствуют заданию.")
        rows.append(
            (
                *(_number(coefficients[name], f"{field}.coefficients.{name}") for name in names),
                _text(_field(row, "operator", field), f"{field}.operator"),
                _number(_field(row, "value", field), f"{field}.value"),
            )
        )
    return linear_constraints(tuple((v["min"], v["max"]) for v in variables), rows)


def task_from_record(record: Mapping) -> TaskDefinition:
    variables = _variables(json_data(record["variables_json"]))
    names = tuple(variable["name"] for variable in variables)
    parameters = _parameters(json_data(record["parameters_json"]), "coefficients")
    metrics = _metrics(json_data(record["derived_metrics_json"]))
    model = ExpressionModel(_text(record["objective"], "objective"), names, parameters)
    constraints = _constraints(json_data(record["constraints_json"]), variables)
    if record["direction"] not in ("min", "max"):
        raise ValueError("Направление задания должно быть min или max.")
    task = model_task(
        _text(record["id"], "id"),
        _text(record["name"], "name"),
        _text(record["description"], "description"),
        model,
        constraints,
        "maximize" if record["direction"] == "max" else "minimize",
        names,
        tuple(v["unit"] for v in variables),
        metrics,
        _text(record["objective_label"], "objective_label"),
        _text(record["objective_unit"], "objective_unit"),
    )
    return replace(
        task,
        builtin=bool(record["is_builtin"]),
        variable_names=tuple(v.get("display_name", v["name"]) for v in variables),
    )


def json_task_record(definition: Mapping) -> dict:
    """Adapt a plugin JSON task to the unchanged SQLite record format."""
    return {
        "id": _field(definition, "id"),
        "name": _field(definition, "name"),
        "description": _field(definition, "description"),
        "objective": _field(definition, "objective"),
        "direction": _field(definition, "direction"),
        "variables_json": json_text(_field(definition, "variables")),
        "parameters_json": json_text(definition.get("coefficients", {})),
        "constraints_json": json_text(definition.get("constraints", [])),
        "derived_metrics_json": json_text(definition.get("derived_metrics", [])),
        "is_builtin": int(definition.get("builtin", False)),
        "objective_label": definition.get("objective_label", "f"),
        "objective_unit": definition.get("objective_unit", ""),
    }
