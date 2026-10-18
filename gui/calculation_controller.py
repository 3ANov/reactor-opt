from PySide6.QtCore import QObject, Signal, Slot

from core.problem import OptimizationProblem
from core.registry import MethodRegistry
from gui.calculation_worker import CalculationWorker
from plugin_api import AlgorithmParameters


class CalculationController(QObject):
    """Own one background calculation and report its lifecycle to the interface.

    A closing window should cancel the calculation and wait for ``finished``
    before destroying the controller. Result and error signals arrive while the
    controller is still busy; ``finished`` arrives after the worker is released.
    """

    completed = Signal(object)
    failed = Signal(str)
    busy_changed = Signal(bool)
    finished = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._worker: CalculationWorker | None = None

    @property
    def worker(self) -> CalculationWorker | None:
        return self._worker

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def start(
        self,
        problem: OptimizationProblem,
        parameters: AlgorithmParameters,
        method_id: str,
        registry: MethodRegistry,
    ) -> bool:
        if self.busy:
            return False
        worker = CalculationWorker(problem, parameters, method_id, registry)
        worker.setParent(self)
        worker.completed.connect(self.completed)
        worker.failed.connect(self.failed)
        worker.finished.connect(self._finish)
        self._worker = worker
        worker.start()
        self.busy_changed.emit(True)
        return True

    def cancel(self) -> bool:
        if self._worker is None:
            return False
        self._worker.requestInterruption()
        return True

    @Slot()
    def _finish(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self.busy_changed.emit(False)
        self.finished.emit()
