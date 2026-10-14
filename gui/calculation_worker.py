from PySide6.QtCore import QThread, Signal

from core.problem import OptimizationProblem
from core.registry import MethodRegistry
from plugin_api import AlgorithmParameters


class CalculationWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        problem: OptimizationProblem,
        parameters: AlgorithmParameters,
        method: str,
        registry: MethodRegistry,
    ) -> None:
        super().__init__()
        self.problem = problem
        self.parameters = parameters
        self.method = method
        self.registry = registry

    def run(self) -> None:
        try:
            result = self.registry.run(
                self.method, self.problem, self.parameters, self.isInterruptionRequested
            )
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.completed.emit(result)
