from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QScrollArea, QStackedWidget, QWidget

from core.auth import Session
from core.plugin_loader import create_method_registry
from core.registry import MethodRegistry
from core.tasks import TaskLibrary
from gui.admin_pages import PluginsPage, UsersPage
from gui.appearance import apply_theme, build_navigation_panel, build_settings_page
from gui.calculation_controller import CalculationController
from gui.calculation_worker import CalculationWorker
from gui.library_pages import MethodsPage, TasksPage
from gui.optimization_page import OptimizationPage
from plugin_api import OptimizationResult


class MainWindow(QMainWindow):
    """Coordinate navigation, the user session and background work."""

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
        self.close_pending = False
        self.registry = registry if registry is not None else create_method_registry()
        if task_library is None:
            raise ValueError("Нужна библиотека заданий, загруженная из базы данных.")
        self.task_library = task_library
        self.registry.bind_session(session)
        self.task_library.bind_session(session)
        self.calculation = CalculationController(self)
        self.calculation.completed.connect(self.show_result)
        self.calculation.failed.connect(self.show_error)
        self.calculation.finished.connect(self.calculation_finished)
        self.setWindowTitle("ReactorOpt")
        self.resize(1240, 800)
        self.setMinimumSize(760, 540)
        self._build_layout()
        self._connect_controls()
        self.methods_page.refresh()
        self.apply_theme(0)
        self.statusBar().showMessage("Готово к расчёту")
        self.session_timer = QTimer(self)
        self.session_timer.setInterval(2000)
        self.session_timer.timeout.connect(self.check_session)
        self.session_timer.start()

    @property
    def worker(self) -> CalculationWorker | None:
        return self.calculation.worker

    def _build_layout(self) -> None:
        container = QWidget()
        self.setCentralWidget(container)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(build_navigation_panel(self))
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.optimization_page = OptimizationPage(self.registry, self.task_library, self.session)
        self.tasks_page = TasksPage(self.task_library, self.session)
        self.methods_page = MethodsPage(
            self.registry, self.session, self.optimization_page.current_problem
        )
        self.pages.addWidget(self.optimization_page)
        self.add_page(self.tasks_page)
        self.add_page(self.methods_page)
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
        self.optimization_page.calculation_requested.connect(self.start_calculation)
        self.optimization_page.cancellation_requested.connect(self.cancel_calculation)
        self.optimization_page.idle.connect(self.finish_close_if_ready)
        self.tasks_page.changed.connect(self.refresh_tasks)
        self.methods_page.changed.connect(self.optimization_page.refresh_methods)
        for page in (self.optimization_page, self.tasks_page, self.methods_page):
            page.message.connect(self.statusBar().showMessage)

    def add_page(self, page: QWidget) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(page)
        self.pages.addWidget(scroll)

    def apply_theme(self, index: int) -> None:
        apply_theme(self, index)

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

    def plugins_changed(self) -> None:
        self.session.require_admin()
        self.optimization_page.refresh_methods()
        self.methods_page.refresh()

    def logout(self) -> None:
        self.session_timer.stop()
        self.logout_requested = True
        self.optimization_page.clear_session()
        self.session.logout()
        self.close()

    def refresh_tasks(self, select_id: str | None = None) -> None:
        current = self.optimization_page.current_task
        selected = select_id or (current.id if current else None)
        self.tasks_page.fill_task_list(selected)
        self.optimization_page.refresh_tasks(selected)

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
            self.optimization_page.refresh_methods()
        if index == 2:
            self.methods_page.refresh()
        if index == 1:
            self.tasks_page.fill_task_list()

    def start_calculation(self) -> None:
        if self.calculation.busy:
            return
        try:
            self.session.user
            method_id, problem, parameters = self.optimization_page.prepare_calculation()
        except ValueError as error:
            self.show_error(str(error))
            return
        self.optimization_page.begin_calculation()
        self.tasks_page.set_busy(True)
        self.calculation.start(problem, parameters, method_id, self.registry)

    def cancel_calculation(self) -> None:
        if self.calculation.cancel():
            self.optimization_page.cancel_button.setEnabled(False)
            self.statusBar().showMessage("Отмена расчёта…")

    def show_result(self, result: OptimizationResult) -> None:
        if self.logout_requested:
            return
        try:
            self.session.user
        except ValueError:
            self.logout()
            return
        self.optimization_page.show_result(result)

    def show_error(self, message: str) -> None:
        self.optimization_page.show_error(message)

    def calculation_finished(self) -> None:
        self.tasks_page.set_busy(False)
        self.optimization_page.set_busy(False)
        self.finish_close_if_ready()

    @property
    def visualization_busy(self) -> bool:
        return self.optimization_page.visualization_busy

    def finish_close_if_ready(self) -> None:
        if self.close_pending and not self.calculation.busy and not self.visualization_busy:
            self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.calculation.busy or self.visualization_busy:
            self.close_pending = True
            self.cancel_calculation()
            self.optimization_page.cancel_pending()
            event.ignore()
        else:
            self.session_timer.stop()
            self.optimization_page.shutdown()
            super().closeEvent(event)
            self.closed.emit()
