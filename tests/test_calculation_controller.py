from collections.abc import Callable
from threading import Event
from time import monotonic
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from core.constraints import LinearConstraints2D
from core.problem import OptimizationProblem
from gui.calculation_controller import CalculationController
from plugin_api import AlgorithmParameters, OptimizationResult


@pytest.fixture(scope="module")
def application() -> QApplication:
    with patch.dict("os.environ", {"QT_QPA_PLATFORM": "offscreen"}):
        return QApplication.instance() or QApplication([])


@pytest.fixture
def problem() -> OptimizationProblem:
    return OptimizationProblem(lambda x, y: x + y, LinearConstraints2D(((0, 1), (0, 1))))


def wait_until(application: QApplication, predicate: Callable[[], bool]) -> None:
    deadline = monotonic() + 3
    while not predicate() and monotonic() < deadline:
        application.processEvents()
        QThread.msleep(1)
    assert predicate(), "Background calculation did not finish in time"


def result(reason: str = "done") -> OptimizationResult:
    return OptimizationResult((1, 1), 2, "test", 1, 1, reason, (), ())


class StubRegistry:
    def __init__(self, action: Callable[[Callable[[], bool]], OptimizationResult]) -> None:
        self.action = action
        self.calls = []

    def run(self, method_id, problem, parameters, cancelled):
        self.calls.append((method_id, problem, parameters, QThread.currentThread()))
        return self.action(cancelled)


def test_result_precedes_cleanup_and_worker_is_deleted(application, problem) -> None:
    parent = QObject()
    controller = CalculationController(parent)
    expected = result()
    registry = StubRegistry(lambda cancelled: expected)
    events = []
    controller.busy_changed.connect(lambda busy: events.append(("busy", busy)))
    controller.completed.connect(
        lambda value: events.append(("completed", value, controller.busy, QThread.currentThread()))
    )
    controller.finished.connect(lambda: events.append(("finished", controller.busy)))

    parameters = AlgorithmParameters()
    assert controller.start(problem, parameters, "test", registry)
    worker = controller.worker
    assert worker is not None and worker.parent() is controller
    assert controller.parent() is parent
    assert controller.busy
    assert not controller.start(problem, parameters, "test", registry)
    wait_until(application, lambda: not controller.busy)

    assert registry.calls == [("test", problem, parameters, worker)]
    assert events == [
        ("busy", True),
        ("completed", expected, True, application.thread()),
        ("busy", False),
        ("finished", False),
    ]
    assert controller.worker is None
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(worker)

    assert controller.start(problem, parameters, "test", registry)
    wait_until(application, lambda: not controller.busy)
    assert len(registry.calls) == 2


def test_cancel_waits_for_thread_before_signalling_finished(application, problem) -> None:
    entered = Event()
    interruption_seen = Event()
    release = Event()

    def calculate(cancelled):
        entered.set()
        deadline = monotonic() + 3
        while not cancelled() and monotonic() < deadline:
            QThread.msleep(1)
        if cancelled():
            interruption_seen.set()
        release.wait(3)
        return result("cancelled")

    controller = CalculationController()
    completed = []
    finished = []
    controller.completed.connect(completed.append)
    controller.finished.connect(lambda: finished.append(True))
    assert not controller.cancel()
    try:
        assert controller.start(problem, AlgorithmParameters(), "test", StubRegistry(calculate))
        wait_until(application, entered.is_set)
        worker = controller.worker
        assert controller.cancel()
        wait_until(application, interruption_seen.is_set)
        assert controller.busy and controller.worker is worker
        assert not finished
    finally:
        release.set()
        wait_until(application, lambda: not controller.busy)
    assert completed == [result("cancelled")]
    assert finished == [True]
    assert not controller.cancel()


def test_error_precedes_finished_and_controller_can_restart(application, problem) -> None:
    def fail(cancelled):
        raise ValueError("invalid calculation")

    controller = CalculationController()
    events = []
    controller.failed.connect(lambda message: events.append((message, controller.busy)))
    controller.finished.connect(lambda: events.append(("finished", controller.busy)))
    assert controller.start(problem, AlgorithmParameters(), "test", StubRegistry(fail))
    wait_until(application, lambda: not controller.busy)
    assert events == [("invalid calculation", True), ("finished", False)]
    assert controller.start(
        problem, AlgorithmParameters(), "test", StubRegistry(lambda cancelled: result())
    )
    wait_until(application, lambda: not controller.busy)


def test_cancellation_on_start_and_deferred_parent_destruction(application, problem) -> None:
    def calculate(cancelled):
        deadline = monotonic() + 1
        while not cancelled() and monotonic() < deadline:
            QThread.msleep(1)
        return result("cancelled" if cancelled() else "done")

    parent = QObject()
    controller = CalculationController(parent)
    completed = []
    controller.completed.connect(completed.append)
    controller.busy_changed.connect(lambda busy: controller.cancel() if busy else None)
    controller.finished.connect(parent.deleteLater)
    assert controller.start(problem, AlgorithmParameters(), "test", StubRegistry(calculate))
    worker = controller.worker
    wait_until(application, lambda: not controller.busy)
    assert completed == [result("cancelled")]
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(worker)
    assert not isValid(controller)
    assert not isValid(parent)
