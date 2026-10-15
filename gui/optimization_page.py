from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
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

from gui.widgets import collapsible, page_layout, scroll_panel, text_label
from visualization.objective_plot import ObjectivePlot

if TYPE_CHECKING:
    from gui.main_window import MainWindow


def build_optimization_page(window: "MainWindow") -> QWidget:
    page, layout = page_layout("Оптимизация производительности реакторов")
    window.task_heading = layout.itemAt(0).widget()
    window.task_description = text_label(
        "Максимизация выхода целевого продукта в системе двух реакторов непрерывного действия"
    )
    layout.addWidget(window.task_description)
    layout.addWidget(_build_workspace(window), 1)
    return page


def _build_workspace(window: "MainWindow") -> QSplitter:
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


def _build_controls(window: "MainWindow") -> QWidget:
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


def _build_calculation_buttons(window: "MainWindow") -> QHBoxLayout:
    buttons = QHBoxLayout()
    window.calculate_button = QPushButton("Запустить расчёт")
    window.calculate_button.clicked.connect(window.start_calculation)
    buttons.addWidget(window.calculate_button)
    window.cancel_button = QPushButton("Отмена")
    window.cancel_button.setEnabled(False)
    window.cancel_button.clicked.connect(window.cancel_calculation)
    buttons.addWidget(window.cancel_button)
    return buttons


def build_algorithm_options(window: "MainWindow") -> QWidget:
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


def _build_visualizations(window: "MainWindow") -> QWidget:
    window.objective_plot = ObjectivePlot(
        window.problem,
        axis_labels=("A₁, м³/ч", "A₂, м³/ч"),
        value_label="C",
    )
    window.objective_plot.idle.connect(window.finish_close_if_ready)
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


def _build_problem_settings(window: "MainWindow") -> QWidget:
    settings = QGroupBox("Параметры задачи")
    window.calculation_settings = settings
    form = QFormLayout(settings)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
    _add_task_and_method_selection(window, form)
    form.addRow("Направление поиска:", _build_direction_buttons(window))
    form.addRow("Точность координат:", _build_precision(window))
    return settings


def _build_results(window: "MainWindow") -> QWidget:
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


def _add_task_and_method_selection(window: "MainWindow", form: QFormLayout) -> None:
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


def _build_direction_buttons(window: "MainWindow") -> QWidget:
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


def _build_precision(window: "MainWindow") -> QDoubleSpinBox:
    window.precision = QDoubleSpinBox()
    window.precision.setDecimals(6)
    window.precision.setRange(0.000001, 10.0)
    window.precision.setSingleStep(0.01)
    window.precision.setValue(window.algorithm_parameters.precision)
    window.precision.setSuffix(" м³/ч")
    return window.precision


def _build_result_fields(window: "MainWindow") -> None:
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


def _add_scan_parameters(window: "MainWindow") -> None:
    window.initial_step = QDoubleSpinBox()
    window.initial_step.setRange(window.algorithm_parameters.precision, 6.0)
    window.initial_step.setSingleStep(0.1)
    window.initial_step.setValue(window.algorithm_parameters.initial_step)
    window.initial_step.setSuffix(" м³/ч")
    window.algorithm_form.addRow("Начальный шаг поиска:", window.initial_step)
    window.reduction_factor = QLabel("0,5 (фиксирован)")
    window.reduction_factor.setToolTip("Текущий алгоритм уменьшает шаг вдвое.")
    window.algorithm_form.addRow("Коэффициент уменьшения шага:", window.reduction_factor)


def _add_search_limits(window: "MainWindow") -> None:
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
