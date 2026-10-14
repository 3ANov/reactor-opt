import json
from dataclasses import replace
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.expressions import DerivedMetric, ExpressionModel
from core.tasks import TaskDefinition, linear_constraints, model_task
from plugin_api import LinearConstraintSet


def number(text: str) -> float:
    try:
        return float(text.strip().replace(",", "."))
    except ValueError as error:
        raise ValueError(f"Неверное число: «{text}».") from error


class TaskEditor(QDialog):
    def __init__(
        self,
        task: TaskDefinition | None = None,
        parent: QWidget | None = None,
        *,
        view_only: bool = False,
    ) -> None:
        super().__init__(parent)
        self.source = task
        unsupported = task is not None and not isinstance(
            task.problem.constraints, LinearConstraintSet
        )
        self.read_only = view_only or unsupported
        self.saved_task: TaskDefinition | None = None
        self.setWindowTitle("Просмотр задания" if self.read_only else "Редактор задания")
        self.resize(780, 780)
        layout = QVBoxLayout(self)
        content = QWidget()
        rows = QVBoxLayout(content)
        self._add_description_fields(rows, task)
        self._add_variable_fields(rows, task)
        self._add_coefficient_fields(rows)
        self._add_constraint_fields(rows)
        self._add_metric_fields(rows)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        close_text = "Закрыть" if self.read_only else "Отмена"
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(close_text)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._load_task(task)
        if unsupported:
            self.error.setText("Редактор пока поддерживает только линейные ограничения.")
        if self.read_only:
            content.setEnabled(False)
            self.buttons.button(QDialogButtonBox.StandardButton.Save).hide()

    def _add_description_fields(self, rows: QVBoxLayout, task: TaskDefinition | None) -> None:
        form = QFormLayout()
        self.name = QLineEdit(task.name if task else "Новое задание")
        self.name.setMaxLength(200)
        self.description = QPlainTextEdit(task.description if task else "Пользовательская задача")
        self.description.setMaximumHeight(70)
        self.direction = QComboBox()
        self.direction.addItem("Минимизация", "minimize")
        self.direction.addItem("Максимизация", "maximize")
        self.direction.setCurrentIndex(1 if task and task.problem.direction == "maximize" else 0)
        form.addRow("Название:", self.name)
        form.addRow("Описание:", self.description)
        form.addRow("Направление по умолчанию:", self.direction)
        rows.addLayout(form)
        self.formula = QPlainTextEdit()
        self.formula.setMaximumHeight(70)
        self.formula.setPlaceholderText("Например: a*(x-2)**2 + (y-3)**2")
        rows.addWidget(QLabel("Целевая функция (степень: **):"))
        rows.addWidget(self.formula)
        help_label = QLabel(
            "Функции: sin, cos, tan, sqrt, exp, log, log10, abs, min, max. "
            "Константы: pi, e. Имена в формуле — латинские идентификаторы."
        )
        help_label.setWordWrap(True)
        rows.addWidget(help_label)

    def _add_variable_fields(self, rows: QVBoxLayout, task: TaskDefinition | None) -> None:
        self.variables = QTableWidget(2, 4)
        self.variables.setHorizontalHeaderLabels(
            ["Переменная", "Единица", "Нижняя граница", "Верхняя граница"]
        )
        self.variables.setMaximumHeight(110)
        self.variables.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        names = (
            task.model_parameters.variables
            if task and isinstance(task.model_parameters, ExpressionModel)
            else ("x", "y")
        )
        units = task.variable_units if task else ("", "")
        bounds = task.problem.constraints.bounds if task else ((0, 5), (0, 5))
        for i, (name, unit, bound) in enumerate(zip(names, units, bounds)):
            for j, value in enumerate((name, unit, *bound)):
                self.variables.setItem(i, j, QTableWidgetItem(str(value)))
        rows.addWidget(QLabel("Переменные и границы:"))
        rows.addWidget(self.variables)

    def _add_coefficient_fields(self, rows: QVBoxLayout) -> None:
        self.coefficients = QTableWidget(0, 2)
        self.coefficients.setHorizontalHeaderLabels(["Коэффициент / параметр", "Значение"])
        self.coefficients.setMaximumHeight(180)
        self.coefficients.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        rows.addWidget(QLabel("Коэффициенты модели:"))
        rows.addWidget(self.coefficients)
        coefficient_buttons = QHBoxLayout()
        self.add_coefficient = QPushButton("Добавить коэффициент")
        self.remove_coefficient = QPushButton("Удалить коэффициент")
        self.add_coefficient.clicked.connect(lambda: self.add_coefficient_row("a", 1))
        self.remove_coefficient.clicked.connect(
            lambda: self.coefficients.removeRow(self.coefficients.currentRow())
        )
        coefficient_buttons.addWidget(self.add_coefficient)
        coefficient_buttons.addWidget(self.remove_coefficient)
        rows.addLayout(coefficient_buttons)

    def _add_constraint_fields(self, rows: QVBoxLayout) -> None:
        self.constraints = QTableWidget(0, 4)
        self.constraints.setHorizontalHeaderLabels(
            ["Коэффициент 1", "Коэффициент 2", "Сравнение", "Правая часть"]
        )
        self.constraints.setMaximumHeight(160)
        self.constraints.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        rows.addWidget(QLabel("Только линейные ограничения: a·x₁ + b·x₂ ≤ / ≥ / = c"))
        rows.addWidget(self.constraints)
        constraint_buttons = QHBoxLayout()
        add_constraint = QPushButton("Добавить ограничение")
        remove_constraint = QPushButton("Удалить ограничение")
        add_constraint.clicked.connect(lambda: self.add_constraint_row(1, 1, "<=", 8))
        remove_constraint.clicked.connect(
            lambda: self.constraints.removeRow(self.constraints.currentRow())
        )
        constraint_buttons.addWidget(add_constraint)
        constraint_buttons.addWidget(remove_constraint)
        rows.addLayout(constraint_buttons)

    def _add_metric_fields(self, rows: QVBoxLayout) -> None:
        self.metrics = QTableWidget(0, 5)
        self.metrics.setHorizontalHeaderLabels(
            ["Обозначение", "Название", "Формула", "Единица", "Параметры (JSON)"]
        )
        self.metrics.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.metrics.setMaximumHeight(160)
        rows.addWidget(QLabel("Дополнительные показатели: C — значение целевой функции"))
        rows.addWidget(self.metrics)
        metric_buttons = QHBoxLayout()
        add_metric = QPushButton("Добавить показатель")
        remove_metric = QPushButton("Удалить показатель")
        add_metric.clicked.connect(
            lambda: self.add_metric_row(
                DerivedMetric("metric-" + uuid4().hex, "Новый показатель", "C")
            )
        )
        remove_metric.clicked.connect(lambda: self.metrics.removeRow(self.metrics.currentRow()))
        metric_buttons.addWidget(add_metric)
        metric_buttons.addWidget(remove_metric)
        rows.addLayout(metric_buttons)

    def _load_task(self, task: TaskDefinition | None) -> None:
        model = task.model_parameters if task else None
        self.formula.setPlainText(
            model.formula if isinstance(model, ExpressionModel) else "(x-2)**2 + (y-2)**2"
        )
        if isinstance(model, ExpressionModel):
            for name, value in model.coefficients:
                self.add_coefficient_row(name, value)
        if task:
            self.metrics.setRowCount(0)
            for metric in task.derived_metrics:
                self.add_metric_row(metric)
            for a, b, c in getattr(task.problem.constraints, "inequalities", ()):
                self.add_constraint_row(a, b, "<=", c)

    def add_metric_row(self, metric: DerivedMetric) -> None:
        if self.metrics.rowCount() >= 16:
            self.error.setText("Допустимо до 16 дополнительных показателей.")
            return
        row = self.metrics.rowCount()
        self.metrics.insertRow(row)
        for column, value in enumerate(
            (
                metric.symbol,
                metric.name,
                metric.formula,
                metric.unit,
                json.dumps(dict(metric.parameters), ensure_ascii=False),
            )
        ):
            self.metrics.setItem(row, column, QTableWidgetItem(value))
        self.metrics.item(row, 0).setData(Qt.ItemDataRole.UserRole, metric.key)

    def read_metrics(self) -> tuple[DerivedMetric, ...]:
        metrics = []
        for row in range(self.metrics.rowCount()):
            try:
                parameter_text = self.cell(self.metrics, row, 4)
                if len(parameter_text) > 2048:
                    raise ValueError("Список параметров слишком велик.")
                try:
                    parameters = json.loads(parameter_text or "{}")
                except json.JSONDecodeError as error:
                    raise ValueError('Неверный JSON параметров. Пример: {"a": 2}.') from error
                if not isinstance(parameters, dict):
                    raise ValueError("Параметры должны быть JSON-объектом с числами.")
                item = self.metrics.item(row, 0)
                key = item.data(Qt.ItemDataRole.UserRole) or "metric-" + uuid4().hex
                metrics.append(
                    DerivedMetric(
                        key,
                        self.cell(self.metrics, row, 1),
                        self.cell(self.metrics, row, 2),
                        self.cell(self.metrics, row, 3),
                        tuple(parameters.items()),
                        self.cell(self.metrics, row, 0),
                    )
                )
            except (ValueError, TypeError) as error:
                raise ValueError(f"Показатель, строка {row + 1}: {error}") from error
        return tuple(metrics)

    def add_coefficient_row(self, name: str, value: float) -> None:
        if self.coefficients.rowCount() >= 32:
            self.error.setText("Допустимо до 32 коэффициентов.")
            return
        row = self.coefficients.rowCount()
        self.coefficients.insertRow(row)
        self.coefficients.setItem(row, 0, QTableWidgetItem(name))
        self.coefficients.setItem(row, 1, QTableWidgetItem(str(value)))

    def add_constraint_row(self, a: float, b: float, comparison: str, c: float) -> None:
        if self.constraints.rowCount() >= 64:
            self.error.setText("Допустимо до 64 ограничений.")
            return
        row = self.constraints.rowCount()
        self.constraints.insertRow(row)
        for column, value in ((0, a), (1, b), (3, c)):
            self.constraints.setItem(row, column, QTableWidgetItem(str(value)))
        combo = QComboBox()
        combo.addItems(["<=", ">=", "="])
        combo.setCurrentText(comparison)
        self.constraints.setCellWidget(row, 2, combo)

    def cell(self, table: QTableWidget, row: int, column: int) -> str:
        item = table.item(row, column)
        return item.text().strip() if item else ""

    def numeric_cell(self, table: QTableWidget, row: int, column: int, field: str) -> float:
        try:
            return number(self.cell(table, row, column))
        except ValueError as error:
            raise ValueError(f"{field}, строка {row + 1}, столбец {column + 1}: {error}") from error

    def build_task(self) -> TaskDefinition:
        if self.source is not None and not isinstance(
            self.source.problem.constraints, LinearConstraintSet
        ):
            raise ValueError("Редактор пока поддерживает только линейные ограничения.")
        metrics = self.read_metrics()
        names = tuple(self.cell(self.variables, row, 0) for row in range(2))
        units = tuple(self.cell(self.variables, row, 1) for row in range(2))
        bounds = tuple(
            tuple(self.numeric_cell(self.variables, row, col, "Границы") for col in (2, 3))
            for row in range(2)
        )
        constraints = linear_constraints(
            bounds,
            [
                (
                    self.numeric_cell(self.constraints, row, 0, "Ограничения"),
                    self.numeric_cell(self.constraints, row, 1, "Ограничения"),
                    self.constraints.cellWidget(row, 2).currentText(),
                    self.numeric_cell(self.constraints, row, 3, "Ограничения"),
                )
                for row in range(self.constraints.rowCount())
            ],
        )
        coefficients = tuple(
            (
                self.cell(self.coefficients, row, 0),
                self.numeric_cell(self.coefficients, row, 1, "Параметры модели"),
            )
            for row in range(self.coefficients.rowCount())
        )
        if len({name for name, _ in coefficients}) != len(coefficients):
            raise ValueError("Имена коэффициентов не должны повторяться.")
        try:
            model = ExpressionModel(self.formula.toPlainText(), names, coefficients)
        except ValueError as error:
            raise ValueError(f"Целевая функция: {error}") from error
        description = self.description.toPlainText().strip()
        if len(description) > 4000:
            raise ValueError("Описание не должно превышать 4000 символов.")
        task = model_task(
            self.source.id if self.source else "task-" + uuid4().hex,
            self.name.text().strip(),
            description,
            model,
            constraints,
            self.direction.currentData(),
            names,
            units,
            metrics,
            self.source.objective_label if self.source else "f",
            self.source.objective_unit if self.source else "",
        )

        if self.source is not None:
            display_names = (
                self.source.variable_names
                if isinstance(self.source.model_parameters, ExpressionModel)
                and names == self.source.model_parameters.variables
                else names
            )
            return replace(task, builtin=self.source.builtin, variable_names=display_names)
        return task

    def accept(self) -> None:
        if self.read_only:
            self.error.setText("Задание открыто только для просмотра.")
            return
        try:
            self.saved_task = self.build_task()
        except (ValueError, ArithmeticError) as error:
            self.error.setText(str(error))
            return
        super().accept()
