from dataclasses import replace
from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QStackedWidget,
    QTreeWidgetItem,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from core.auth import Session
from core.plugin_loader import create_method_registry
from core.problem import OptimizationProblem
from core.registry import MethodRegistry
from core.tasks import TaskDefinition, TaskLibrary
from gui.admin_pages import MethodEditor, PluginsPage, UsersPage
from gui.appearance import SECTIONS as SECTIONS
from gui.appearance import apply_theme, build_navigation_panel, build_settings_page
from gui.calculation_worker import CalculationWorker
from gui.library_pages import build_methods_page, build_tasks_page, render_method_library
from gui.optimization_page import build_optimization_page
from gui.task_editor import TaskEditor
from gui.widgets import text_label
from plugin_api import AlgorithmParameters, OptimizationResult

if TYPE_CHECKING:
    from visualization.surface_plot import SurfacePlot


class MainWindow(QMainWindow):
    closed = Signal()

    def __init__(
        self,
        registry: MethodRegistry | None = None,
        task_library: TaskLibrary | None = None,
        *,
        session: Session,
    ) -> None:
        super().__init__()
        self.session = session
        self.session.user
        self.logout_requested = False
        self.surface_plot: SurfacePlot | None = None
        self.worker: CalculationWorker | None = None
        self.last_result: OptimizationResult | None = None
        self.close_pending = False
        self.registry = registry if registry is not None else create_method_registry()
        if task_library is None:
            raise ValueError("Нужна библиотека заданий, загруженная из базы данных.")
        self.task_library = task_library
        self.registry.bind_session(session)
        self.task_library.bind_session(session)
        tasks = self.task_library.list_tasks()
        if not tasks:
            raise ValueError("В библиотеке нет задач.")
        self.problem = tasks[0].problem
        self.current_task: TaskDefinition | None = tasks[0]
        available = [
            method
            for method in self.registry.list_methods()
            if self.registry.compatibility(method.metadata.id, self.problem).compatible
        ]
        self.algorithm_parameters = (
            available[0].metadata.defaults if available else (AlgorithmParameters())
        )
        self.method_parameters: dict[str, AlgorithmParameters] = {}
        self.active_method_id: str | None = None
        self.setWindowTitle("ReactorOpt")
        self.resize(1240, 800)
        self.setMinimumSize(760, 540)
        self._build_layout()
        self._connect_controls()
        self.refresh_methods()
        self.refresh_method_library()
        self.configure_task_display()
        self.apply_theme(0)
        self.statusBar().showMessage("Готово к расчёту")
        self.session_timer = QTimer(self)
        self.session_timer.setInterval(2000)
        self.session_timer.timeout.connect(self.check_session)
        self.session_timer.start()

    # Создание окна и подключение сигналов

    def _build_layout(self) -> None:
        container = QWidget()
        self.setCentralWidget(container)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(build_navigation_panel(self))
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.pages.addWidget(build_optimization_page(self))
        self.add_page(build_tasks_page(self))
        self.add_page(build_methods_page(self))
        self.add_page(build_settings_page(self))
        if self.session.is_admin:
            self.users_page = UsersPage(self.session)
            self.plugins_page = PluginsPage(self.registry, self.session, self.plugins_changed)
            self.add_page(self.users_page)
            self.add_page(self.plugins_page)

    def _connect_controls(self) -> None:
        self.navigation.idClicked.connect(self.pages.setCurrentIndex)
        self.navigation.idClicked.connect(self.section_changed)
        self.navigation.button(0).setChecked(True)
        self.pages.setCurrentIndex(0)
        self.method.currentIndexChanged.connect(self.update_method_settings)
        self.direction.idClicked.connect(self.refresh_methods)
        self.precision.valueChanged.connect(self.sync_parameters)
        self.initial_step.valueChanged.connect(self.sync_parameters)
        self.max_evaluations.valueChanged.connect(self.sync_parameters)
        self.regions.valueChanged.connect(self.sync_parameters)
        self.keep_history.toggled.connect(self.sync_parameters)

    def add_page(self, page: QWidget) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(page)
        self.pages.addWidget(scroll)

    def switch_visualization(self, index: int) -> None:
        if index == 1 and self.surface_plot is None:
            from visualization.surface_plot import SurfacePlot

            self.surface_plot = SurfacePlot(self.problem)
            self.surface_plot.idle.connect(self.finish_close_if_ready)
            self.visualization_stack.addWidget(self.surface_plot)
            self.configure_task_display()
            self.surface_plot.set_theme(self.theme.currentIndex() == 1)
            self.surface_plot.set_result(self.last_result)
        self.visualization_stack.setCurrentIndex(index)

    def apply_theme(self, index: int) -> None:
        apply_theme(self, index)

    # Сеанс и права доступа

    def check_session(self) -> None:
        try:
            self.session.user
        except ValueError:
            self.logout()

    def allow_admin(self) -> bool:
        try:
            self.session.require_admin()
            return True
        except ValueError as error:
            self.statusBar().showMessage(str(error))
            return False

    def edit_method(self, method_id: str) -> None:
        if not self.allow_admin():
            return
        MethodEditor(self.registry, method_id, self.session, self).exec()
        self.refresh_methods()
        self.refresh_method_library()

    def plugins_changed(self) -> None:
        self.session.require_admin()
        self.refresh_methods()
        self.refresh_method_library()

    def logout(self) -> None:
        self.session_timer.stop()
        self.logout_requested = True
        self.last_result = None
        self.method_parameters.clear()
        self.objective_plot.set_result(None)
        if self.surface_plot is not None:
            self.surface_plot.set_result(None)
        self.result_details.hide()
        for label in self.result_values.values():
            label.setText("—")
        self.session.logout()
        self.close()

    # Библиотеки заданий и методов

    def fill_task_list(self, select_id: str | None = None) -> None:
        item = self.task_list.currentItem()
        selected = select_id or (item.data(0, Qt.ItemDataRole.UserRole) if item else None)
        self.task_list.clear()
        for task in self.task_library.list_tasks():
            origin = "Встроенная задача" if task.builtin else "Пользовательская задача"
            direction = "Максимизация" if task.problem.direction == "maximize" else "Минимизация"
            item = QTreeWidgetItem([task.name, origin, direction])
            item.setData(0, Qt.ItemDataRole.UserRole, task.id)
            item.setToolTip(0, task.description)
            self.task_list.addTopLevelItem(item)
        self.task_list.resizeColumnToContents(0)
        if self.task_list.topLevelItemCount():
            self.task_list.setCurrentItem(self.task_list.topLevelItem(0))
        for i in range(self.task_list.topLevelItemCount()):
            item = self.task_list.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == selected:
                self.task_list.setCurrentItem(item)

    def selected_library_task(self) -> TaskDefinition | None:
        item = self.task_list.currentItem()
        return self.task_library.get(item.data(0, Qt.ItemDataRole.UserRole)) if item else None

    def update_task_actions(self) -> None:
        task = self.selected_library_task()
        for index, button in enumerate(self.task_actions):
            button.setVisible(index == 4 or self.session.is_admin)
            button.setEnabled(
                self.worker is None
                and (index == 0 or task is not None and (index != 3 or not task.builtin))
            )

    def view_task(self) -> None:
        task = self.selected_library_task()
        if task is not None:
            TaskEditor(task, self, view_only=True).exec()

    def refresh_tasks(self, select_id: str | None = None) -> None:
        selected = select_id or (self.current_task.id if self.current_task else None)
        self.fill_task_list(selected)
        with QSignalBlocker(self.task):
            self.task.clear()
            for definition in self.task_library.list_tasks():
                self.task.addItem(definition.name, definition)
                if definition.id == selected:
                    self.task.setCurrentIndex(self.task.count() - 1)
        definition = self.task.currentData()
        if definition is not self.current_task:
            self.select_problem(self.task.currentIndex())

    def save_edited_task(self, definition: TaskDefinition, *, existing: bool) -> None:
        self.session.require_admin()
        if existing:
            self.task_library.update(definition)
        else:
            self.task_library.add(definition)
        self.refresh_tasks(definition.id)
        self.task_library_error.clear()

    def add_task(self) -> None:
        if not self.allow_admin():
            return
        if self.worker is not None:
            return
        dialog = TaskEditor(parent=self)
        if dialog.exec() == TaskEditor.DialogCode.Accepted:
            try:
                self.save_edited_task(dialog.saved_task, existing=False)
            except (ValueError, SQLAlchemyError) as error:
                self.task_library_error.setText(f"Не удалось сохранить задание: {error}")

    def edit_task(self) -> None:
        if not self.allow_admin():
            return
        task = self.selected_library_task()
        if task is None or self.worker is not None:
            return
        dialog = TaskEditor(task, self)
        if dialog.exec() == TaskEditor.DialogCode.Accepted:
            try:
                self.save_edited_task(dialog.saved_task, existing=True)
            except (ValueError, SQLAlchemyError) as error:
                self.task_library_error.setText(f"Не удалось сохранить задание: {error}")

    def duplicate_task(self) -> None:
        if not self.allow_admin():
            return
        task = self.selected_library_task()
        if task is None or self.worker is not None:
            return
        try:
            copied = self.task_library.duplicate(task.id)
            self.refresh_tasks(copied.id)
            self.task_library_error.clear()
        except (ValueError, SQLAlchemyError) as error:
            self.task_library_error.setText(str(error))

    def delete_task(self) -> None:
        if not self.allow_admin():
            return
        task = self.selected_library_task()
        if task is None or self.worker is not None:
            return
        if task.builtin:
            self.task_library_error.setText("Встроенное задание нельзя удалить.")
            return
        confirm = QMessageBox(
            QMessageBox.Icon.Question,
            "Удаление задания",
            f"Удалить задание «{task.name}»?",
            parent=self,
        )
        confirm.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        confirm.setDefaultButton(QMessageBox.StandardButton.No)
        confirm.button(QMessageBox.StandardButton.Yes).setText("Удалить")
        confirm.button(QMessageBox.StandardButton.No).setText("Отмена")
        answer = confirm.exec()
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.task_library.remove(task.id)
            self.refresh_tasks()
            self.task_library_error.clear()
        except (ValueError, SQLAlchemyError) as error:
            self.task_library_error.setText(str(error))

    def refresh_method_library(self) -> None:
        render_method_library(self)

    def change_method_availability(self, method_id: str, enabled: bool) -> None:
        try:
            self.session.require_admin()
            self.registry.set_enabled(method_id, enabled)
            self.statusBar().showMessage("Доступность метода сохранена")
        except (ValueError, SQLAlchemyError) as error:
            self.statusBar().showMessage(f"Не удалось сохранить доступность метода: {error}")
        self.refresh_methods()
        self.refresh_method_library()

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

    def section_changed(self, index: int) -> None:
        if index >= 4:
            if not self.allow_admin():
                self.pages.setCurrentIndex(0)
                return
            if index == 4:
                self.users_page.refresh()
            elif index == 5:
                self.plugins_page.refresh()
        if index in (0, 2):
            self.refresh_methods()
        if index == 2:
            self.refresh_method_library()
        if index == 1:
            self.fill_task_list()

    # Настройки текущего расчёта

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
        self.calculate_button.setEnabled(method is not None and self.worker is None)
        self.save_defaults_button.setEnabled(
            self.session.is_admin and method is not None and self.worker is None
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

    # Фоновый расчёт и результаты

    def start_calculation(self) -> None:
        if self.worker is not None:
            return
        try:
            self.session.user
            method_id = self.method.currentData()
            if method_id is None:
                raise ValueError("Нет доступного метода для выбранной задачи и направления.")
            problem = self.current_problem()
            method = self.registry.require_available(method_id, problem)
            parameters = self.algorithm_parameters
            if "initial_step" not in method.supported_parameters:
                parameters = replace(parameters, initial_step=parameters.precision)
        except ValueError as error:
            self.show_error(str(error))
            return
        self.worker = CalculationWorker(problem, parameters, method_id, self.registry)
        self.worker.completed.connect(self.show_result)
        self.worker.failed.connect(self.show_error)
        self.worker.finished.connect(self.calculation_finished)
        self.calculation_settings.setEnabled(False)
        self.algorithm_settings.setEnabled(False)
        self.save_defaults_button.setEnabled(False)
        for button in self.task_actions:
            button.setEnabled(False)
        self.calculate_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.last_result = None
        self.objective_plot.set_result(None)
        if self.surface_plot is not None:
            self.surface_plot.set_result(None)
        self.result_details.hide()
        self.result_warning.hide()
        self.result_message.setText("Выполняется расчёт…")
        self.result_message.show()
        self.statusBar().showMessage("Выполняется расчёт…")
        self.worker.start()

    def cancel_calculation(self) -> None:
        if self.worker is not None:
            self.worker.requestInterruption()
            self.cancel_button.setEnabled(False)
            self.statusBar().showMessage("Отмена расчёта…")

    def show_result(self, result: OptimizationResult) -> None:
        if self.logout_requested:
            return
        try:
            self.session.user
        except ValueError:
            self.logout()
            return
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
        self.statusBar().showMessage(result.reason)

    def show_error(self, message: str) -> None:
        self.last_result = None
        self.objective_plot.set_result(None)
        if self.surface_plot is not None:
            self.surface_plot.set_result(None)
        self.result_details.hide()
        self.result_warning.hide()
        self.result_message.setText(f"Ошибка расчёта: {message}")
        self.result_message.show()
        self.statusBar().showMessage("Ошибка расчёта")

    def calculation_finished(self) -> None:
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()
        self.calculation_settings.setEnabled(True)
        self.algorithm_settings.setEnabled(True)
        self.update_task_actions()
        self.refresh_methods()
        self.cancel_button.setEnabled(False)
        self.finish_close_if_ready()

    @property
    def visualization_busy(self) -> bool:
        return self.objective_plot.busy or (
            self.surface_plot is not None and self.surface_plot.busy
        )

    # Завершение работы

    def finish_close_if_ready(self) -> None:
        if self.close_pending and self.worker is None and not self.visualization_busy:
            self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker is not None or self.visualization_busy:
            self.close_pending = True
            self.cancel_calculation()
            self.objective_plot.cancel_pending()
            if self.surface_plot is not None:
                self.surface_plot.cancel_pending()
            event.ignore()
        else:
            self.session_timer.stop()
            if self.surface_plot is not None:
                self.surface_plot.shutdown()
            super().closeEvent(event)
            self.closed.emit()
