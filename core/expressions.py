import ast
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from re import fullmatch

import numpy as np
import sympy as sp
from sympy.printing.numpy import NumPyPrinter

from plugin_api import MetricValue

FUNCTIONS = {
    "sin": (sp.sin, 1),
    "cos": (sp.cos, 1),
    "tan": (sp.tan, 1),
    "sqrt": (sp.sqrt, 1),
    "exp": (sp.exp, 1),
    "log": (sp.log, 1),
    "log10": (sp.log, 1),
    "abs": (sp.Abs, 1),
    "min": (sp.Min, 2),
    "max": (sp.Max, 2),
}
OPERATORS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
CONSTANTS = {"pi": sp.pi, "e": sp.E}


def valid_name(name: str) -> bool:
    return bool(fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,31}", name)) and name not in (
        *FUNCTIONS,
        *CONSTANTS,
        "True",
        "False",
        "None",
    )


def validate_node(node: ast.AST, names: set[str], depth: int = 0) -> None:
    if depth > 24:
        raise ValueError("Слишком большая глубина выражения: допустимо до 24 уровней.")
    if isinstance(node, ast.Constant):
        if type(node.value) not in (int, float):
            raise ValueError("Разрешены только конечные числовые константы.")
        if abs(node.value) > 1e100 or not math.isfinite(node.value):
            raise ValueError("Слишком большая числовая константа.")
        return
    if isinstance(node, ast.Name) and node.id in names:
        return
    if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
        if isinstance(node.op, ast.Pow) and any(
            isinstance(part, ast.BinOp) and isinstance(part.op, ast.Pow)
            for part in ast.walk(node.right)
        ):
            raise ValueError("Вложенные степени в показателе запрещены.")
        children = (node.left, node.right)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        children = (node.operand,)
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in FUNCTIONS
        and not node.keywords
    ):
        if len(node.args) != FUNCTIONS[node.func.id][1]:
            raise ValueError(f"Неверное число аргументов функции {node.func.id}.")
        children = node.args
    else:
        raise ValueError("Недопустимое имя или операция в формуле.")
    for child in children:
        validate_node(child, names, depth + 1)


def symbolic_node(node: ast.AST, symbols: dict[str, sp.Symbol]) -> sp.Expr:
    if isinstance(node, ast.Constant):
        return sp.Integer(node.value) if type(node.value) is int else sp.Float(node.value)
    if isinstance(node, ast.Name):
        return CONSTANTS[node.id] if node.id in CONSTANTS else symbols[node.id]
    if isinstance(node, ast.UnaryOp):
        value = symbolic_node(node.operand, symbols)
        return sp.Mul(-1, value, evaluate=False) if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        left, right = symbolic_node(node.left, symbols), symbolic_node(node.right, symbols)
        if isinstance(node.op, ast.Add):
            return sp.Add(left, right, evaluate=False)
        if isinstance(node.op, ast.Sub):
            return sp.Add(left, sp.Mul(-1, right, evaluate=False), evaluate=False)
        if isinstance(node.op, ast.Mult):
            return sp.Mul(left, right, evaluate=False)
        if isinstance(node.op, ast.Div):
            return sp.Mul(left, sp.Pow(right, -1, evaluate=False), evaluate=False)
        return sp.Pow(left, right, evaluate=False)
    args = [symbolic_node(arg, symbols) for arg in node.args]
    if node.func.id == "log10":
        return sp.Mul(
            sp.log(args[0], evaluate=False),
            sp.Pow(sp.log(10, evaluate=False), -1, evaluate=False),
            evaluate=False,
        )
    return FUNCTIONS[node.func.id][0](*args, evaluate=False)


def safe_power(base: object, exponent: object) -> np.ndarray:
    base, exponent = np.asarray(base, dtype=float), np.asarray(exponent, dtype=float)
    allowed = np.isfinite(exponent) & (np.abs(exponent) <= 100)
    with np.errstate(all="ignore"):
        return np.where(allowed, np.power(base, np.where(allowed, exponent, 0)), np.nan)


class ExpressionPrinter(NumPyPrinter):
    def _print_Add(self, expression: sp.Expr) -> str:
        return "(" + " + ".join(self._print(arg) for arg in expression.args) + ")"

    def _print_Mul(self, expression: sp.Expr) -> str:
        return "(" + " * ".join(self._print(arg) for arg in expression.args) + ")"

    def _print_Pow(self, expression: sp.Expr) -> str:
        return f"safe_power({self._print(expression.base)}, {self._print(expression.exp)})"

    def _print_Min(self, expression: sp.Expr) -> str:
        result = self._print(expression.args[0])
        for arg in expression.args[1:]:
            result = f"numpy.minimum({result}, {self._print(arg)})"
        return result

    def _print_Max(self, expression: sp.Expr) -> str:
        result = self._print(expression.args[0])
        for arg in expression.args[1:]:
            result = f"numpy.maximum({result}, {self._print(arg)})"
        return result


@dataclass(frozen=True)
class ExpressionModel:
    formula: str
    variables: tuple[str, ...] = ("x", "y")
    coefficients: tuple[tuple[str, float], ...] = ()
    symbolic: sp.Expr = field(init=False, repr=False, compare=False)
    _function: Callable = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.formula, str)
            or not self.formula.strip()
            or len(self.formula) > 2048
        ):
            raise ValueError("Формула должна содержать от 1 до 2048 символов.")
        names = (*self.variables, *(name for name, _ in self.coefficients))
        if not 1 <= len(self.variables) <= 3 or len(self.coefficients) > 32:
            raise ValueError("Допустимо от 1 до 3 аргументов и до 32 коэффициентов.")
        if len(set(names)) != len(names) or not all(valid_name(name) for name in names):
            raise ValueError("Имена должны быть уникальными латинскими идентификаторами.")
        if any(not math.isfinite(value) or abs(value) > 1e12 for _, value in self.coefficients):
            raise ValueError("Коэффициенты должны быть конечными числами по модулю до 10¹².")
        try:
            tree = ast.parse(self.formula, mode="eval").body
        except (SyntaxError, RecursionError) as error:
            raise ValueError("Неверная запись формулы. Для степени используйте **.") from error
        if sum(1 for _ in ast.walk(tree)) > 128:
            raise ValueError("Выражение слишком сложно: допустимо до 128 узлов.")
        validate_node(tree, set(names) | set(CONSTANTS))
        symbols = {name: sp.Symbol(name, real=True) for name in names}
        expression = symbolic_node(tree, symbols)
        numeric_symbols = {
            name: sp.Symbol(f"argument_{i}", real=True) for i, name in enumerate(names)
        }
        numeric_expression = symbolic_node(tree, numeric_symbols)
        function = sp.lambdify(
            list(numeric_symbols.values()),
            numeric_expression,
            modules=[{"safe_power": safe_power}, "numpy"],
            printer=ExpressionPrinter(),
            dummify=False,
            cse=False,
            docstring_limit=0,
        )
        object.__setattr__(self, "symbolic", expression)
        object.__setattr__(self, "_function", function)

    def objective(self, *coordinates: float) -> float:
        if len(coordinates) != len(self.variables):
            raise ValueError("Число координат не соответствует аргументам формулы.")
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError("Координаты должны быть конечными числами.")
        try:
            values = self.array_objective(*coordinates)
            value = float(values)
            if not math.isfinite(value):
                raise ValueError("Недопустимая область функции, степень или величина результата.")
            return value
        except (ArithmeticError, ValueError) as error:
            raise ValueError(f"Формула не определена в точке {coordinates}: {error}") from error

    def array_objective(self, *coordinates: object) -> np.ndarray:
        arrays = np.broadcast_arrays(*(np.asarray(value, dtype=float) for value in coordinates))
        with np.errstate(all="ignore"):
            result = np.asarray(
                self._function(*arrays, *(v for _, v in self.coefficients)), dtype=float
            )
            result = np.broadcast_to(result, arrays[0].shape)
            return np.where(np.isfinite(result) & (np.abs(result) <= 1e100), result, np.nan)


@dataclass(frozen=True)
class DerivedMetric:
    key: str
    name: str
    formula: str
    unit: str = ""
    parameters: tuple[tuple[str, float], ...] = ()
    symbol: str = ""
    expression: ExpressionModel = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not fullmatch(r"[a-zA-Z][a-zA-Z0-9_.-]{0,63}", self.key):
            raise ValueError("Показателю нужен постоянный идентификатор.")
        if not self.name.strip() or len(self.name) > 200 or len(self.unit) > 32:
            raise ValueError("Укажите название показателя до 200 символов и единицу до 32.")
        if len(self.symbol) > 32:
            raise ValueError("Обозначение показателя не должно превышать 32 символа.")
        compiled = ExpressionModel(self.formula, ("C",), self.parameters)
        object.__setattr__(self, "expression", compiled)

    def calculate(self, value: float) -> MetricValue:
        try:
            result = self.expression.objective(value)
        except ValueError as error:
            raise ValueError(f"Показатель «{self.name}»: {error}") from error
        return MetricValue(self.key, self.name, result, self.unit, self.symbol)
