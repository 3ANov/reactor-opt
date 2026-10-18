from dataclasses import replace
from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from core.auth import Session
from core.problem import OptimizationProblem
from core.registry import MethodRegistry
from core.tasks import TaskDefinition, TaskLibrary
from gui.widgets import collapsible, page_layout, scroll_panel, text_label
from plugin_api import AlgorithmParameters, OptimizationResult
from visualization.objective_plot import ObjectivePlot

if TYPE_CHECKING:
    from visualization.surface_plot import SurfacePlot


class OptimizationPage(QWidget):
    """Own the optimization form, its settings and displayed results."""

    calculation_requested = Signal()
    cancellation_requested = Signal()
    message = Signal(str)
    idle = Signal()

    def __init__(
        self, registry: MethodRegistry, task_library: TaskLibrary, session: Session
    ) -> None:
        super().__init__()
        self.registry = registry
        self.task_library = task_library
        self.session = session
        self._busy = False
        self.surface_plot: SurfacePlot | None = None
        self.last_result: OptimizationResult | None = None
        tasks = task_library.list_tasks()
        if not tasks:
            raise ValueError("В библиотеке нет задач.")
        self.problem = tasks[0].problem
        self.current_task: TaskDefinition | None = tasks[0]
        available = [
            method
            for method in registry.list_methods()
            if registry.compatibility(method.metadata.id, self.problem).compatible
        ]
        self.algorithm_parameters = (
            available[0].metadata.defaults if available else AlgorithmParameters()
        )
        self.method_parameters: dict[str, AlgorithmParameters] = {}
        self.active_method_id: str | None = None
        self.dark = False
        _build_page(self)
        self._connect_controls()
        self.refresh_methods()
        self.configure_task_display()

    @property
    def busy(self) -> bool:
        return self._busy

    def _connect_controls(self) -> None:
        self.method.currentIndexChanged.connect(self.update_method_settings)
        self.direction.idClicked.connect(self.refresh_methods)
        self.precision.valueChanged.connect(self.sync_parameters)
        self.initial_step.valueChanged.connect(self.sync_parameters)
        self.max_evaluations.valueChanged.connect(self.sync_parameters)
        self.regions.valueChanged.connect(self.sync_parameters)
        self.keep_history.toggled.connect(self.sync_parameters)

    def allow_admin(self) -> bool:
        try:
            self.session.require_admin()
            return True
        except ValueError as error:
            self.message.emit(str(error))
            return False

    def set_theme(self, dark: bool) -> None:
        self.dark = dark
        self.objective_plot.set_theme(dark)
        if self.surface_plot is not None:
            self.surface_plot.set_theme(dark)

    def prepare_calculation(self) -> tuple[str, OptimizationProblem, AlgorithmParameters]:
        method_id = self.method.currentData()
        if method_id is None:
            raise ValueError("Нет доступного метода для выбранной задачи и направления.")
        problem = self.current_problem()
        method = self.registry.require_available(method_id, problem)
        parameters = self.algorithm_parameters
        if "initial_step" not in method.supported_parameters:
            parameters = replace(parameters, initial_step=parameters.precision)
        return method_id, problem, parameters

    def begin_calculation(self) -> None:
        self.set_busy(True)
        self.last_result = None
        self.objective_plot.set_result(None)
        if self.surface_plot is not None:
            self.surface_plot.set_result(None)
        self.result_details.hide()
        self.result_warning.hide()
        self.result_message.setText("Выполняется расчёт…")
        self.result_message.show()
        self.message.emit("Выполняется расчёт…")

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.calculation_settings.setEnabled(not busy)
        self.algorithm_settings.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        if busy:
            self.save_defaults_button.setEnabled(False)
            self.calculate_button.setEnabled(False)
        else:
            self.refresh_methods()

    def clear_session(self) -> None:
        self.last_result = None
        self.method_parameters.clear()
        self.objective_plot.set_result(None)
        if self.surface_plot is not None:
            self.surface_plot.set_result(None)
        self.result_details.hide()
        for label in self.result_values.values():
            label.setText("—")

    @property
    def visualization_busy(self) -> bool:
        return self.objective_plot.busy or (
            self.surface_plot is not None and self.surface_plot.busy
        )

    def cancel_pending(self) -> None:
        self.objective_plot.cancel_pending()
        if self.surface_plot is not None:
            self.surface_plot.cancel_pending()

    def shutdown(self) -> None:
        if self.surface_plot is not None:
            self.surface_plot.shutdown()

    def switch_visualization(self, index: int) -> None:
        if index == 1 and self.surface_plot is None:
            from visualization.surface_plot import SurfacePlot

            self.surface_plot = SurfacePlot(self.problem)
            self.surface_plot.idle.connect(self.idle.emit)
            self.visualization_stack.addWidget(self.surface_plot)
            self.configure_task_display()
            self.surface_plot.set_theme(self.dark)
            self.surface_plot.set_result(self.last_result)
        self.visualization_stack.setCurrentIndex(index)

    def refresh_tasks(self, select_id: str | None = None) -> None:
        selected = select_id or (self.current_task.id if self.current_task else None)
        with QSignalBlocker(self.task):
            self.task.clear()
            for definition in self.task_library.list_tasks():
                self.task.addItem(definition.name, definition)
                if definition.id == selected:
                    self.task.setCurrentIndex(self.task.count() - 1)
        definition = self.task.currentData()
        if definition is not self.current_task:
            self.select_problem(self.task.currentIndex())

    def save_method_defaults(self) -> None:
        if not self.allow_admin():
            return
        method_id = self.method.currentData()
        if method_id is None:
            self.settings_message.setText("Нет выбранного метода.")
            return
        try:
            self.registry.update_metadata(method_id, defaults=self.algorithm_parameters)
            self.settings_message.setText("Значения по умолчанию сохранены.")
        except (ValueError, SQLAlchemyError) as error:
            self.settings_message.setText(f"Не удалось сохранить настройки: {error}")

    def current_problem(self) -> OptimizationProblem:
        return replace(
            self.problem, direction=("maximize" if self.direction.checkedId() == 1 else "minimize")
        )

    def refresh_methods(self, _direction: int | None = None) -> None:
        selected = self.method.currentData()
        excluded = list(self.registry.load_errors)
        with QSignalBlocker(self.method):
            self.method.clear()
            for method in self.registry.list_methods():
                metadata = method.metadata
                compatibility = self.registry.compatibility(metadata.id, self.current_problem())
                if compatibility.compatible:
                    self.method.addItem(metadata.name, metadata.id)
                else:
                    excluded.append(f"{metadata.name}: {compatibility.reason}")
            index = self.method.findData(selected)
            if index >= 0:
                self.method.setCurrentIndex(index)
        if not self.method.count():
            excluded.insert(0, "Для выбранной задачи и направления нет доступных методов.")
        self.method_message.setText("\n".join(excluded))
        self.method_message.setVisible(bool(excluded))
        self.update_method_settings(self.method.currentIndex())

    def select_problem(self, index: int) -> None:
        definition = self.task.itemData(index)
        problem = definition.problem if isinstance(definition, TaskDefinition) else definition
        if not isinstance(problem, OptimizationProblem):
            return
        self.task.setToolTip(self.task.currentText())
        self.current_task = definition if isinstance(definition, TaskDefinition) else None
        self.problem = problem
        self.direction.button(1 if problem.direction == "maximize" else 0).setChecked(True)
        self.last_result = None
        self.result_details.hide()
        self.result_warning.hide()
        self.result_message.setText("Расчёт ещё не выполнялся.")
        self.result_message.show()
        self.configure_task_display()
        self.objective_plot.set_problem(problem)
        if self.surface_plot is not None:
            self.surface_plot.set_problem(problem)
        self.refresh_methods()

    def configure_task_display(self) -> None:
        definition = self.current_task
        names = definition.variable_names if definition else ("x₁", "x₂")
        units = definition.variable_units if definition else ("", "")
        suffix = f" {units[0]}" if units[0] and units[0] == units[1] else ""
        self.precision.setSuffix(suffix)
        self.initial_step.setSuffix(suffix)
        axes = tuple(f"{name}, {unit}" if unit else name for name, unit in zip(names, units))
        self.task_heading.setText(definition.name if definition else self.task.currentText())
        self.task_description.setText(definition.description if definition else "")
        value_label = definition.objective_label if definition else "f"
        value_unit = definition.objective_unit if definition else ""
        self.objective_plot.set_labels(axes, value_label)
        function_title = f"{value_label}, {value_unit}" if value_unit else value_label
        if self.surface_plot is not None:
            self.surface_plot.set_labels(axes, function_title)
        for key, label in (("a1", axes[0]), ("a2", axes[1]), ("value", function_title)):
            self.result_form.labelForField(self.result_values[key]).setText(label + ":")
        for label in self.extra_metric_labels:
            self.result_form.removeRow(label)
        self.extra_metric_labels.clear()
        self.result_metric_values.clear()
        metrics = definition.derived_metrics if definition else self.problem.derived_metrics
        self.result_form.setRowVisible(self.result_values["output"], bool(metrics))
        for index, metric in enumerate(metrics):
            title = (f"{metric.symbol} — " if metric.symbol else "") + metric.name
            title += f", {metric.unit}" if metric.unit else ""
            if index == 0:
                label = self.result_values["output"]
                self.result_form.labelForField(label).setText(title + ":")
            else:
                label = text_label("")
                self.result_form.addRow(title + ":", label)
                self.extra_metric_labels.append(label)
            self.result_metric_values[metric.key] = label
        for row in range(self.result_form.rowCount()):
            item = self.result_form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            if item is not None and isinstance(item.widget(), QLabel):
                item.widget().setWordWrap(True)
                item.widget().setMaximumWidth(135)

    def update_method_settings(self, index: int) -> None:
        method = self.registry.get(self.method.itemData(index)) if index >= 0 else None
        method_id = method.metadata.id if method is not None else None
        if method_id != self.active_method_id:
            if self.active_method_id is not None:
                self.method_parameters[self.active_method_id] = self.algorithm_parameters
            self.active_method_id = method_id
            if method is not None:
                self.load_method_parameters(
                    self.method_parameters.get(method_id, method.metadata.defaults)
                )
        supported = method.supported_parameters if method is not None else ()
        for name, field in (
            ("initial_step", self.initial_step),
            ("regions", self.regions),
            ("max_evaluations", self.max_evaluations),
            ("keep_history", self.keep_history),
        ):
            self.algorithm_form.setRowVisible(field, name in supported)
        fixed = dict(method.metadata.fixed_parameters) if method is not None else {}
        self.algorithm_form.setRowVisible(self.reduction_factor, "step_reduction" in fixed)
        if "step_reduction" in fixed:
            self.reduction_factor.setText(f"{fixed['step_reduction']:g} (фиксирован)")
        self.algorithm_settings.setTitle("Параметры метода")
        self.precision.setEnabled("precision" in supported)
        self.calculation_settings.layout().labelForField(self.precision).setText(
            "Точность координат:"
        )
        units = self.current_task.variable_units if self.current_task else ("", "")
        suffix = f" {units[0]}" if units[0] and units[0] == units[1] else ""
        self.precision.setSuffix(suffix)
        self.calculate_button.setEnabled(method is not None and not self.busy)
        self.save_defaults_button.setEnabled(
            self.session.is_admin and method is not None and not self.busy
        )

    def load_method_parameters(self, parameters: AlgorithmParameters) -> None:
        widgets = (
            self.precision,
            self.initial_step,
            self.regions,
            self.max_evaluations,
            self.keep_history,
        )
        blockers = [QSignalBlocker(widget) for widget in widgets]
        self.precision.setValue(parameters.precision)
        self.initial_step.setRange(self.precision.value(), max(6.0, parameters.initial_step))
        self.initial_step.setValue(parameters.initial_step)
        self.regions.setValue(parameters.regions)
        self.max_evaluations.setValue(parameters.max_evaluations)
        self.keep_history.setChecked(parameters.keep_history)
        del blockers
        self.sync_parameters()

    def sync_parameters(self) -> None:
        self.initial_step.setMinimum(self.precision.value())
        self.algorithm_parameters = AlgorithmParameters(
            precision=self.precision.value(),
            initial_step=self.initial_step.value(),
            max_evaluations=self.max_evaluations.value(),
            regions=self.regions.value(),
            keep_history=self.keep_history.isChecked(),
        )

    def show_result(self, result: OptimizationResult) -> None:
        self.last_result = result
        self.objective_plot.set_result(result)
        if self.surface_plot is not None:
            self.surface_plot.set_result(result)
        values = {
            "a1": "—",
            "a2": "—",
            "value": "—",
            "output": "—",
            "method": result.method,
            "evaluations": str(result.evaluations),
            "stages": str(result.stages),
            "reason": result.reason,
        }
        if result.point is not None and result.value is not None:
            x, y = result.point
            values.update(a1=f"{x:.6g}", a2=f"{y:.6g}", value=f"{result.value:.10g}")
        for key, text in values.items():
            self.result_values[key].setText(text)
        for label in self.result_metric_values.values():
            label.setText("—")
        for metric in result.derived_metrics:
            if metric.key in self.result_metric_values:
                self.result_metric_values[metric.key].setText(f"{metric.value:.10g}")
        self.result_details.show()
        self.result_message.hide()
        warnings = []
        if result.reason in (
            "Достигнут лимит вычислений",
            "Расчёт отменён",
        ):
            warnings.append(
                "Поиск не завершён. "
                + (
                    "Показан промежуточный результат."
                    if result.point is not None
                    else "Допустимая точка ещё не найдена."
                )
            )
        if result.history_truncated:
            warnings.append("История сокращена до последних 2000 записей каждого типа.")
        warnings.append("Глобальная оптимальность результата не гарантируется.")
        self.result_warning.setText("\n".join(warnings))
        self.result_warning.show()
        self.message.emit(result.reason)

    def show_error(self, message: str) -> None:
        self.last_result = None
        self.objective_plot.set_result(None)
        if self.surface_plot is not None:
            self.surface_plot.set_result(None)
        self.result_details.hide()
        self.result_warning.hide()
        self.result_message.setText(f"Ошибка расчёта: {message}")
        self.result_message.show()
        self.message.emit("Ошибка расчёта")


def _build_page(window: "OptimizationPage") -> QWidget:
    page, layout = page_layout("Оптимизация производительности реакторов", page=window)
    window.task_heading = layout.itemAt(0).widget()
    window.task_description = text_label(
        "Максимизация выхода целевого продукта в системе двух реакторов непрерывного действия"
    )
    layout.addWidget(window.task_description)
    layout.addWidget(_build_workspace(window), 1)
    return page


def _build_workspace(window: "OptimizationPage") -> QSplitter:
    window.splitter = QSplitter(Qt.Orientation.Horizontal)
    window.controls_scroll = scroll_panel(_build_controls(window))
    window.controls_scroll.setMinimumWidth(280)
    window.splitter.addWidget(window.controls_scroll)
    window.splitter.addWidget(_build_visualizations(window))
    window.splitter.setStretchFactor(0, 0)
    window.splitter.setStretchFactor(1, 1)
    window.splitter.setSizes([310, 680])
    window.splitter.setChildrenCollapsible(False)
    return window.splitter


def _build_controls(window: "OptimizationPage") -> QWidget:
    controls = QWidget()
    layout = QVBoxLayout(controls)
    layout.setContentsMargins(0, 0, 8, 0)
    layout.addWidget(_build_problem_settings(window))
    window.advanced_parameters = collapsible(
        "Дополнительные параметры", build_algorithm_options(window)
    )
    layout.addWidget(window.advanced_parameters)
    layout.addLayout(_build_calculation_buttons(window))
    layout.addWidget(_build_results(window))
    layout.addStretch()
    return controls


def _build_calculation_buttons(window: "OptimizationPage") -> QHBoxLayout:
    buttons = QHBoxLayout()
    window.calculate_button = QPushButton("Запустить расчёт")
    window.calculate_button.clicked.connect(window.calculation_requested.emit)
    buttons.addWidget(window.calculate_button)
    window.cancel_button = QPushButton("Отмена")
    window.cancel_button.setEnabled(False)
    window.cancel_button.clicked.connect(window.cancellation_requested.emit)
    buttons.addWidget(window.cancel_button)
    return buttons


def build_algorithm_options(window: "OptimizationPage") -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    window.algorithm_settings = QGroupBox("Сканирование с переменным шагом")
    window.algorithm_form = QFormLayout(window.algorithm_settings)
    window.algorithm_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    _add_scan_parameters(window)
    _add_search_limits(window)
    layout.addWidget(window.algorithm_settings)
    window.save_defaults_button = QPushButton("Сохранить параметры по умолчанию")
    window.save_defaults_button.clicked.connect(window.save_method_defaults)
    window.save_defaults_button.setVisible(window.session.is_admin)
    layout.addWidget(window.save_defaults_button)
    window.settings_message = text_label("")
    layout.addWidget(window.settings_message)
    return page


def _build_visualizations(window: "OptimizationPage") -> QWidget:
    window.objective_plot = ObjectivePlot(
        window.problem,
        axis_labels=("A₁, м³/ч", "A₂, м³/ч"),
        value_label="C",
    )
    window.objective_plot.idle.connect(window.idle.emit)
    visualizations = QWidget()
    visualization_layout = QVBoxLayout(visualizations)
    visualization_layout.setContentsMargins(0, 0, 0, 0)
    switches = QHBoxLayout()
    window.visualization_mode = QButtonGroup(window)
    for index, title in enumerate(("2D", "3D")):
        button = QPushButton(title)
        button.setCheckable(True)
        window.visualization_mode.addButton(button, index)
        switches.addWidget(button)
    window.visualization_mode.button(0).setChecked(True)
    window.visualization_mode.idClicked.connect(window.switch_visualization)
    visualization_layout.addLayout(switches)
    window.visualization_stack = QStackedWidget()
    window.visualization_stack.addWidget(window.objective_plot)
    visualization_layout.addWidget(window.visualization_stack, 1)
    window.plot_scroll = scroll_panel(visualizations)
    window.plot_scroll.setMinimumWidth(300)
    return window.plot_scroll


def _build_problem_settings(window: "OptimizationPage") -> QWidget:
    settings = QGroupBox("Параметры задачи")
    window.calculation_settings = settings
    form = QFormLayout(settings)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
    _add_task_and_method_selection(window, form)
    form.addRow("Направление поиска:", _build_direction_buttons(window))
    form.addRow("Точность координат:", _build_precision(window))
    return settings


def _build_results(window: "OptimizationPage") -> QWidget:
    results = QGroupBox("Результаты")
    results_layout = QVBoxLayout(results)
    window.result_message = text_label("Расчёт ещё не выполнялся.")
    results_layout.addWidget(window.result_message)
    _build_result_fields(window)
    window.result_details.hide()
    results_layout.addWidget(window.result_details)
    window.result_warning = text_label("")
    window.result_warning.setObjectName("secondaryText")
    window.result_warning.hide()
    results_layout.addWidget(window.result_warning)
    return results


def _add_task_and_method_selection(window: "OptimizationPage", form: QFormLayout) -> None:
    window.task = QComboBox()
    window.task.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )
    window.task.setMinimumContentsLength(16)
    for task in window.task_library.list_tasks():
        window.task.addItem(task.name, task)
    window.task.setToolTip(window.task.currentText())
    window.task.currentIndexChanged.connect(window.select_problem)
    form.addRow("Задача:", window.task)
    window.method = QComboBox()
    window.method.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )
    window.method.setMinimumContentsLength(14)
    form.addRow("Метод:", window.method)
    window.method_message = text_label("")
    window.method_message.setObjectName("secondaryText")
    form.addRow(window.method_message)


def _build_direction_buttons(window: "OptimizationPage") -> QWidget:
    direction_panel = QWidget()
    direction_layout = QHBoxLayout(direction_panel)
    direction_layout.setContentsMargins(0, 0, 0, 0)
    direction_layout.setSpacing(4)
    window.direction = QButtonGroup(window)
    window.direction.setExclusive(True)
    for index, title in enumerate(("Минимизация", "Максимизация")):
        button = QPushButton(title)
        button.setObjectName("directionButton")
        button.setCheckable(True)
        window.direction.addButton(button, index)
        direction_layout.addWidget(button)
    window.direction.button(1 if window.problem.direction == "maximize" else 0).setChecked(True)
    return direction_panel


def _build_precision(window: "OptimizationPage") -> QDoubleSpinBox:
    window.precision = QDoubleSpinBox()
    window.precision.setDecimals(6)
    window.precision.setRange(0.000001, 10.0)
    window.precision.setSingleStep(0.01)
    window.precision.setValue(window.algorithm_parameters.precision)
    window.precision.setSuffix(" м³/ч")
    return window.precision


def _build_result_fields(window: "OptimizationPage") -> None:
    window.result_details = QWidget()
    result_form = QFormLayout(window.result_details)
    window.result_form = result_form
    result_form.setContentsMargins(0, 0, 0, 0)
    result_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    window.result_values: dict[str, QLabel] = {}
    window.result_metric_values: dict[str, QLabel] = {}
    window.extra_metric_labels: list[QLabel] = []
    for key, title in (
        ("a1", "A₁, м³/ч:"),
        ("a2", "A₂, м³/ч:"),
        ("value", "f:"),
        ("output", "Дополнительный показатель:"),
        ("method", "Метод:"),
        ("evaluations", "Вычислений:"),
        ("stages", "Этапов поиска:"),
        ("reason", "Завершение:"),
    ):
        label = text_label("")
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        window.result_values[key] = label
        result_form.addRow(title, label)


def _add_scan_parameters(window: "OptimizationPage") -> None:
    window.initial_step = QDoubleSpinBox()
    window.initial_step.setRange(window.algorithm_parameters.precision, 6.0)
    window.initial_step.setSingleStep(0.1)
    window.initial_step.setValue(window.algorithm_parameters.initial_step)
    window.initial_step.setSuffix(" м³/ч")
    window.algorithm_form.addRow("Начальный шаг поиска:", window.initial_step)
    window.reduction_factor = QLabel("0,5 (фиксирован)")
    window.reduction_factor.setToolTip("Текущий алгоритм уменьшает шаг вдвое.")
    window.algorithm_form.addRow("Коэффициент уменьшения шага:", window.reduction_factor)


def _add_search_limits(window: "OptimizationPage") -> None:
    window.regions = QSpinBox()
    window.regions.setRange(1, 20)
    window.regions.setValue(window.algorithm_parameters.regions)
    window.algorithm_form.addRow("Перспективных областей:", window.regions)
    window.max_evaluations = QSpinBox()
    window.max_evaluations.setRange(1, 10_000_000)
    window.max_evaluations.setValue(window.algorithm_parameters.max_evaluations)
    window.algorithm_form.addRow("Лимит вычислений:", window.max_evaluations)
    window.keep_history = QCheckBox("Сохранять историю поиска")
    window.keep_history.setChecked(window.algorithm_parameters.keep_history)
    window.algorithm_form.addRow(window.keep_history)
