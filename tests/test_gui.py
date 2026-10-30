import os
from collections.abc import Callable
from dataclasses import replace
from threading import Event
from time import monotonic, sleep

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core.auth import AuthService  # noqa: E402
from core.plugin_loader import create_method_registry  # noqa: E402
from core.storage import SQLiteStorage  # noqa: E402
from core.tasks import TaskLibrary  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402
from plugin_api import (  # noqa: E402
    AlgorithmParameters,
    MethodCapabilities,
    MethodMetadata,
    OptimizationMethod,
    OptimizationResult,
    OptimizationTask,
)


class WaitingMethod(OptimizationMethod):
    """A plugin that stays active until the GUI requests cancellation."""

    metadata = MethodMetadata(
        id="waiting",
        name="Ожидание отмены",
        description="Метод для проверки отмены фонового расчёта.",
        category="Тестовые методы",
    )
    capabilities = MethodCapabilities()
    supported_parameters = ("precision", "max_evaluations")

    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()

    def run(
        self,
        problem: OptimizationTask,
        parameters: AlgorithmParameters,
        cancelled: Callable[[], bool] | None = None,
    ) -> OptimizationResult:
        self.entered.set()
        while not self.release.wait(0.005):
            if cancelled is not None and cancelled():
                break
        return OptimizationResult(None, None, self.metadata.name, 0, 0, "Расчёт отменён", (), ())


def wait_until(app: QApplication, condition: Callable[[], bool], timeout: float = 15) -> None:
    deadline = monotonic() + timeout
    while not condition():
        app.processEvents()
        if monotonic() >= deadline:
            pytest.fail("Не завершилась фоновая операция интерфейса.")
        sleep(0.005)
    app.processEvents()


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    application.setQuitOnLastWindowClosed(False)
    yield application
    application.processEvents()


@pytest.fixture
def window(app):
    storage = SQLiteStorage(":memory:")
    auth = AuthService(storage.engine)
    auth.create_initial_admin("gui_admin", "TestPassword2026")
    session = auth.authenticate("gui_admin", "TestPassword2026")
    registry, library = create_method_registry(), TaskLibrary()
    storage.attach(library, registry)
    storage.bind_session(session)
    source = library.list_tasks()[0]
    library.add(
        replace(
            source,
            id="other-task",
            name="Другая задача",
            description="Задача для проверки переключения интерфейса.",
            problem=replace(source.problem, direction="minimize"),
            variable_names=("u", "v"),
            variable_units=("с", "с"),
            objective_unit="К",
            builtin=False,
        )
    )
    widget = MainWindow(registry, library, session=session)
    widget.show()
    wait_until(app, lambda: not widget.visualization_busy)
    try:
        yield widget
    finally:
        widget.close()
        wait_until(app, lambda: widget.worker is None and not widget.visualization_busy)
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        session.logout()
        storage.close()


def test_nelder_mead_is_default_with_supported_parameters(window):
    page = window.optimization_page
    assert [page.method.itemData(i) for i in range(page.method.count())] == [
        "nelder-mead",
        "variable",
        "fixed",
    ]
    assert page.method.currentData() == page.active_method_id == "nelder-mead"
    assert page.algorithm_parameters == AlgorithmParameters()
    assert page.precision.isEnabled()
    assert not page.precision.isHidden()
    assert page.calculation_settings.layout().labelForField(page.precision) is None
    assert page.algorithm_form.labelForField(page.precision).text() == "Точность по координатам δ:"
    assert not page.stopping_criterion.isHidden()
    assert page.stopping_criterion.currentData() == "coordinates"
    assert page.function_precision.isHidden()
    assert not page.initial_step.isHidden()
    assert not page.max_evaluations.isHidden()
    assert not page.keep_history.isHidden()
    assert page.regions.isHidden()
    assert page.reduction_factor.isHidden()
    assert page.calculate_button.isEnabled()
    assert page.prepare_calculation() == (
        "nelder-mead",
        page.current_problem(),
        AlgorithmParameters(),
    )


def test_nelder_mead_criterion_switch_preserves_inactive_precision(window):
    page = window.optimization_page
    page.precision.setValue(0.5)
    page.initial_step.setValue(1.0)

    page.stopping_criterion.setCurrentIndex(page.stopping_criterion.findData("function"))
    page.function_precision.setValue(0.000123)
    page.initial_step.setValue(0.1)

    assert page.precision.isHidden()
    assert page.algorithm_form.labelForField(page.precision).isHidden()
    assert not page.function_precision.isHidden()
    assert page.precision.value() == 0.5
    assert page.prepare_calculation()[2] == replace(
        AlgorithmParameters(),
        precision=0.5,
        initial_step=0.1,
        function_precision=0.000123,
        stopping_criterion="function",
    )

    page.stopping_criterion.setCurrentIndex(page.stopping_criterion.findData("evaluations"))
    page.max_evaluations.setValue(123)
    expected = page.algorithm_parameters
    assert page.precision.isHidden()
    assert page.function_precision.isHidden()
    assert not page.max_evaluations.isHidden()

    page.method.setCurrentIndex(page.method.findData("variable"))
    page.method.setCurrentIndex(page.method.findData("nelder-mead"))
    assert page.prepare_calculation()[2] == expected
    assert page.precision.value() == 0.5
    assert page.initial_step.value() == pytest.approx(0.1)

    page.stopping_criterion.setCurrentIndex(page.stopping_criterion.findData("coordinates"))
    assert not page.precision.isHidden()
    assert page.function_precision.isHidden()
    assert page.precision.value() == page.algorithm_parameters.precision == 0.5
    assert page.initial_step.value() >= 0.5
    assert page.algorithm_parameters.function_precision == pytest.approx(0.000123)
    assert page.algorithm_parameters.max_evaluations == 123


@pytest.mark.parametrize(
    "method_id,label", (("fixed", "Шаг сетки:"), ("variable", "Конечный шаг сетки:"))
)
def test_grid_precision_remains_available_for_other_stopping_criteria(window, method_id, label):
    page = window.optimization_page
    page.method.setCurrentIndex(page.method.findData(method_id))
    page.precision.setValue(0.05)
    assert page.calculation_settings.layout().labelForField(page.precision) is None

    for criterion in ("function", "evaluations"):
        page.stopping_criterion.setCurrentIndex(page.stopping_criterion.findData(criterion))
        assert not page.precision.isHidden()
        assert page.algorithm_form.labelForField(page.precision).text() == label
        assert page.precision.value() == page.prepare_calculation()[2].precision == 0.05
        assert page.function_precision.isHidden() == (criterion != "function")

    page.stopping_criterion.setCurrentIndex(page.stopping_criterion.findData("coordinates"))
    assert not page.precision.isHidden()
    assert page.algorithm_form.labelForField(page.precision).text() == label


def test_method_switch_remembers_independent_parameters(window):
    page = window.optimization_page
    page.precision.setValue(0.03)
    page.stopping_criterion.setCurrentIndex(page.stopping_criterion.findData("function"))
    page.function_precision.setValue(0.000321)
    page.initial_step.setValue(1.5)
    page.max_evaluations.setValue(321)
    page.keep_history.setChecked(True)
    nelder_mead_parameters = page.algorithm_parameters

    page.method.setCurrentIndex(page.method.findData("variable"))
    page.precision.setValue(0.02)
    page.stopping_criterion.setCurrentIndex(page.stopping_criterion.findData("evaluations"))
    page.function_precision.setValue(0.000123)
    page.initial_step.setValue(2.0)
    page.regions.setValue(3)
    page.keep_history.setChecked(True)
    expected = page.algorithm_parameters

    page.method.setCurrentIndex(page.method.findData("fixed"))
    assert page.initial_step.isHidden()
    assert page.regions.isHidden()
    page.precision.setValue(0.05)
    page.function_precision.setValue(0.001)

    page.method.setCurrentIndex(page.method.findData("variable"))
    assert page.algorithm_parameters == expected
    assert page.stopping_criterion.currentData() == "evaluations"
    assert page.function_precision.isHidden()
    assert not page.initial_step.isHidden()
    assert not page.regions.isHidden()
    page.method.setCurrentIndex(page.method.findData("fixed"))
    assert page.precision.value() == 0.05
    assert page.function_precision.value() == pytest.approx(0.001)
    assert page.stopping_criterion.currentData() == "coordinates"
    assert page.function_precision.isHidden()

    page.method.setCurrentIndex(page.method.findData("nelder-mead"))
    assert page.algorithm_parameters == nelder_mead_parameters
    assert page.prepare_calculation()[2] == nelder_mead_parameters
    assert page.stopping_criterion.currentData() == "function"
    assert not page.function_precision.isHidden()
    assert not page.initial_step.isHidden()
    assert page.regions.isHidden()


def test_saved_function_precision_is_loaded_as_method_default(window):
    page = window.optimization_page
    page.stopping_criterion.setCurrentIndex(page.stopping_criterion.findData("function"))
    page.function_precision.setValue(0.000123)
    expected = page.algorithm_parameters
    page.save_defaults_button.click()

    assert window.registry.get("nelder-mead").metadata.defaults == expected
    assert page.settings_message.text() == "Значения по умолчанию сохранены."
    page.method.setCurrentIndex(page.method.findData("fixed"))
    page.method_parameters.pop("nelder-mead")
    page.method.setCurrentIndex(page.method.findData("nelder-mead"))

    assert page.function_precision.value() == pytest.approx(0.000123)
    assert page.stopping_criterion.currentData() == "function"
    assert not page.function_precision.isHidden()
    assert page.prepare_calculation()[2] == expected


def test_loading_defaults_preserves_small_values_and_large_grid_steps(window):
    page = window.optimization_page
    small = AlgorithmParameters(
        precision=1e-8,
        function_precision=1e-12,
        initial_step=1e-4,
        stopping_criterion="function",
    )
    page.method.setCurrentIndex(page.method.findData("fixed"))
    window.registry.update_metadata("nelder-mead", defaults=small)
    page.method_parameters.pop("nelder-mead", None)
    page.method.setCurrentIndex(page.method.findData("nelder-mead"))

    assert page.prepare_calculation()[2] == small
    page.initial_step.setValue(0.125)
    assert page.prepare_calculation()[2].initial_step == 0.125

    large = AlgorithmParameters(precision=500, initial_step=500)
    window.registry.update_metadata("fixed", defaults=large)
    page.method_parameters.pop("fixed", None)
    page.method.setCurrentIndex(page.method.findData("fixed"))
    assert page.prepare_calculation()[2] == large

    page.method.setCurrentIndex(page.method.findData("nelder-mead"))
    assert page.prepare_calculation()[2] == replace(small, initial_step=0.125)


def test_calculation_displays_result_and_restores_controls(app, window):
    page = window.optimization_page
    page.precision.setValue(0.1)
    page.keep_history.setChecked(True)
    page.calculate_button.click()

    assert window.worker is not None
    assert not page.calculate_button.isEnabled()
    assert not page.calculation_settings.isEnabled()
    assert page.cancel_button.isEnabled()
    wait_until(app, lambda: window.worker is None)

    result = page.last_result
    assert result is not None
    assert result.point == pytest.approx((1, 7), abs=0.1)
    assert result.value == pytest.approx(1858, abs=0.1)
    assert result.best_history and result.stage_history
    assert page.result_values["value"].text() == "1858"
    assert page.result_values["method"].text() == result.method
    assert page.result_details.isVisible()
    assert not page.result_message.isVisible()
    assert page.calculate_button.isEnabled()
    assert page.calculation_settings.isEnabled()
    assert not page.cancel_button.isEnabled()


def test_small_evaluation_limit_displays_result_without_warning(app, window):
    page = window.optimization_page
    page.max_evaluations.setValue(2)
    page.keep_history.setChecked(True)
    page.calculate_button.click()
    wait_until(app, lambda: window.worker is None)

    result = page.last_result
    assert result is not None
    assert result.evaluations == 2
    assert result.reason == "Достигнут лимит вычислений"
    assert result.point is not None
    assert page.current_problem().constraints.contains(result.point)
    assert page.result_values["evaluations"].text() == "2"
    assert page.result_values["reason"].text() == "Выполнено заданное число вычислений"
    assert window.statusBar().currentMessage() == "Выполнено заданное число вычислений"
    assert "Поиск не завершён" not in page.result_warning.text()
    assert "Показан промежуточный результат" not in page.result_warning.text()
    assert page.result_details.isVisible()
    assert page.calculate_button.isEnabled()
    assert not page.cancel_button.isEnabled()


def test_task_selection_clears_previous_result_and_changes_labels(app, window):
    page = window.optimization_page
    page.precision.setValue(0.1)
    page.calculate_button.click()
    wait_until(app, lambda: window.worker is None)
    assert page.last_result is not None

    other_index = next(
        i for i in range(page.task.count()) if page.task.itemData(i).id == "other-task"
    )
    page.task.setCurrentIndex(other_index)

    assert page.current_task.id == "other-task"
    assert page.current_problem().direction == "minimize"
    assert page.task_heading.text() == "Другая задача"
    assert page.precision.suffix() == " с"
    assert page.function_precision.suffix() == " К"
    assert page.last_result is None
    assert not page.result_details.isVisible()
    assert page.result_message.text() == "Расчёт ещё не выполнялся."
    wait_until(app, lambda: not window.visualization_busy)


def test_second_builtin_task_can_be_selected_and_calculated(app, window):
    page = window.optimization_page
    index = next(i for i in range(page.task.count()) if page.task.itemData(i).id == "mixing-cost")
    page.task.setCurrentIndex(index)

    assert page.current_problem().direction == "minimize"
    assert page.task_heading.text() == "Подбор режима смешения"
    assert page.function_precision.suffix() == ""
    assert page.method.currentData() == "nelder-mead"
    page.calculate_button.click()
    wait_until(app, lambda: window.worker is None)

    assert page.last_result.point == pytest.approx((2, 3), abs=0.01)
    assert page.last_result.value == pytest.approx(0, abs=0.001)
    assert page.result_details.isVisible()


def test_saving_task_updates_both_pages_and_selected_problem(app, window):
    page = window.optimization_page
    tasks = window.tasks_page
    initial_count = page.task.count()
    created = replace(
        page.current_task,
        id="created-through-editor",
        name="Новое задание",
        description="Сохранено через редактор заданий.",
        builtin=False,
    )
    tasks.save_edited_task(created, existing=False)

    assert page.task.count() == tasks.task_list.topLevelItemCount() == initial_count + 1
    assert tasks.selected_library_task() is created
    assert page.task.currentData() is created
    assert page.current_task is created
    assert page.task_heading.text() == created.name

    updated = replace(
        created,
        name="Обновлённое задание",
        problem=replace(created.problem, direction="minimize"),
        variable_units=("с", "с"),
    )
    tasks.save_edited_task(updated, existing=True)

    assert page.task.count() == tasks.task_list.topLevelItemCount() == initial_count + 1
    assert tasks.selected_library_task() is updated
    assert tasks.task_list.currentItem().text(0) == updated.name
    assert tasks.task_list.currentItem().text(2) == "Минимизация"
    assert page.current_task is updated
    assert page.task.currentText() == page.task_heading.text() == updated.name
    assert page.current_problem().direction == "minimize"
    assert page.precision.suffix() == " с"
    wait_until(app, lambda: not window.visualization_busy)


def test_duplicate_and_delete_keep_task_selections_consistent(app, window, monkeypatch):
    page = window.optimization_page
    tasks = window.tasks_page
    original = page.current_task
    initial_count = page.task.count()
    window.navigation.button(1).click()
    tasks.task_actions[2].click()

    copied = tasks.selected_library_task()
    assert copied.id != original.id
    assert copied.name == original.name + " (копия)"
    assert not copied.builtin
    assert page.current_task is copied
    assert page.task.currentData() is copied
    assert page.task.count() == tasks.task_list.topLevelItemCount() == initial_count + 1

    monkeypatch.setattr(QMessageBox, "exec", lambda _: QMessageBox.StandardButton.Yes)
    tasks.task_actions[3].click()

    ids = {task.id for task in window.task_library.list_tasks()}
    listed_ids = {
        tasks.task_list.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
        for i in range(tasks.task_list.topLevelItemCount())
    }
    assert copied.id not in ids
    assert listed_ids == {page.task.itemData(i).id for i in range(page.task.count())} == ids
    assert page.task.count() == tasks.task_list.topLevelItemCount() == initial_count
    assert tasks.selected_library_task() is page.current_task
    assert page.task.currentData() is original
    wait_until(app, lambda: not window.visualization_busy)


def test_method_availability_updates_calculation_choices(window):
    page = window.optimization_page
    window.methods_page.change_method_availability("fixed", False)
    assert [page.method.itemData(i) for i in range(page.method.count())] == [
        "nelder-mead",
        "variable",
    ]
    assert page.method.currentData() == "nelder-mead"
    assert page.calculate_button.isEnabled()

    window.methods_page.change_method_availability("nelder-mead", False)
    assert [page.method.itemData(i) for i in range(page.method.count())] == ["variable"]
    assert page.method.currentData() == "variable"
    assert page.calculate_button.isEnabled()

    window.methods_page.change_method_availability("nelder-mead", True)
    assert page.method.currentData() == "variable"

    window.methods_page.change_method_availability("variable", False)
    assert page.method.currentData() == "nelder-mead"
    assert page.calculate_button.isEnabled()

    window.methods_page.change_method_availability("nelder-mead", False)
    assert page.method.count() == 0
    assert not page.calculate_button.isEnabled()
    assert not page.save_defaults_button.isEnabled()
    assert "нет доступных методов" in page.method_message.text()

    window.methods_page.change_method_availability("fixed", True)
    assert page.method.currentData() == "fixed"
    assert page.calculate_button.isEnabled()
    assert page.save_defaults_button.isEnabled()

    window.methods_page.change_method_availability("nelder-mead", True)
    assert page.method.currentData() == "fixed"


def start_waiting_method(app, window):
    plugin = WaitingMethod()
    window.registry.register(plugin)
    page = window.optimization_page
    page.refresh_methods()
    page.method.setCurrentIndex(page.method.findData(plugin.metadata.id))
    assert page.stopping_criterion.isHidden()
    assert page.function_precision.isHidden()
    page.calculate_button.click()
    wait_until(app, plugin.entered.is_set)
    return plugin


def test_cancel_calculation_returns_to_ready_state(app, window):
    page = window.optimization_page
    start_waiting_method(app, window)
    page.cancel_button.click()
    assert not page.cancel_button.isEnabled()
    wait_until(app, lambda: window.worker is None)

    assert page.last_result.reason == "Расчёт отменён"
    assert "Допустимая точка ещё не найдена." in page.result_warning.text()
    assert page.calculate_button.isEnabled()
    assert page.calculation_settings.isEnabled()


def test_logout_cancels_running_calculation_and_discards_late_result(app, window):
    page = window.optimization_page
    start_waiting_method(app, window)
    closed = []
    window.closed.connect(lambda: closed.append(True))

    window.logout_button.click()
    wait_until(app, lambda: bool(closed))

    assert window.logout_requested
    assert window.worker is None
    assert not window.isVisible()
    assert not window.session_timer.isActive()
    assert page.last_result is None
    assert not page.method_parameters
    with pytest.raises(ValueError, match="Сеанс завершён"):
        window.session.user


def test_expired_session_closes_window(app, window):
    window.session.logout()
    window.check_session()
    wait_until(app, lambda: not window.isVisible())
    assert window.logout_requested
    assert not window.session_timer.isActive()


def test_regular_user_sees_read_only_library_and_no_admin_pages(app):
    storage = SQLiteStorage(":memory:")
    auth = AuthService(storage.engine)
    auth.create_initial_admin("admin", "TestPassword2026")
    admin = auth.authenticate("admin", "TestPassword2026")
    auth.create_user(admin, "reader", "TestPassword2026")
    session = auth.authenticate("reader", "TestPassword2026")
    registry, library = create_method_registry(), TaskLibrary()
    storage.attach(library, registry)
    storage.bind_session(session)
    widget = MainWindow(registry, library, session=session)
    widget.show()
    try:
        wait_until(app, lambda: not widget.visualization_busy)
        assert widget.pages.count() == 4
        assert len(widget.navigation.buttons()) == 4
        widget.navigation.button(1).click()
        assert all(not button.isVisible() for button in widget.tasks_page.task_actions[:4])
        assert widget.tasks_page.task_actions[4].isVisible()
        widget.navigation.button(0).click()
        assert not widget.optimization_page.save_defaults_button.isEnabled()
        assert widget.optimization_page.calculate_button.isEnabled()
    finally:
        widget.close()
        wait_until(app, lambda: not widget.visualization_busy)
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        session.logout()
        admin.logout()
        storage.close()
