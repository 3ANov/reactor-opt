from collections.abc import Callable
from dataclasses import dataclass, fields, replace

from core.auth import Session
from plugin_api import (
    API_VERSION,
    AlgorithmParameters,
    ConstraintSet,
    MethodCapabilities,
    MethodMetadata,
    OptimizationMethod,
    OptimizationResult,
    OptimizationTask,
)


@dataclass(frozen=True)
class RegisteredMethod:
    metadata: MethodMetadata
    capabilities: MethodCapabilities
    supported_parameters: tuple[str, ...]
    implementation: OptimizationMethod


@dataclass(frozen=True)
class Compatibility:
    compatible: bool
    reason: str = ""


class MethodRegistry:
    def __init__(self) -> None:
        self._session: Session | None = None
        self._methods: dict[str, RegisteredMethod] = {}
        self.load_errors: list[str] = []
        self._save: Callable[[MethodMetadata], None] | None = None
        self._delete: Callable[[str], None] | None = None

    def bind_session(self, session: Session) -> None:
        if self._session is not None:
            raise ValueError("Контекст доступа уже установлен.")
        session.user
        self._session = session

    def _require_write(self) -> None:
        if self._session is not None:
            self._session.require_admin()

    def set_persistence(
        self, save: Callable[[MethodMetadata], None], delete: Callable[[str], None]
    ) -> None:
        self._save, self._delete = save, delete

    def register(self, method: OptimizationMethod, *, builtin: bool = False) -> None:
        self._require_write()
        if not isinstance(method, OptimizationMethod) or not callable(method.run):
            raise ValueError("Плагин не соответствует интерфейсу метода оптимизации.")
        if not isinstance(method.metadata, MethodMetadata) or not isinstance(
            method.capabilities, MethodCapabilities
        ):
            raise ValueError("Плагин должен предоставить метаданные и возможности метода.")
        if method.metadata.api_version != API_VERSION:
            raise ValueError("Версия API плагина не поддерживается.")
        method_id = method.metadata.id
        if method_id in self._methods:
            raise ValueError(f"Метод «{method_id}» уже зарегистрирован.")
        supported = tuple(method.supported_parameters)
        known = {field.name for field in fields(AlgorithmParameters)}
        if (
            len(set(supported)) != len(supported)
            or not set(supported) <= known
            or not {"precision", "max_evaluations"} <= set(supported)
        ):
            raise ValueError("Метод должен объявить известные параметры, включая точность и лимит.")
        source = type(method).__module__ + "." + type(method).__qualname__
        metadata = replace(method.metadata, builtin=builtin, implementation=source)
        if self._save is not None:
            self._save(metadata)
        self._methods[method_id] = RegisteredMethod(
            metadata, method.capabilities, supported, method
        )

    def get(self, method_id: str) -> RegisteredMethod:
        try:
            return self._methods[method_id]
        except KeyError:
            raise ValueError(f"Метод «{method_id}» не зарегистрирован.") from None

    def list_methods(self) -> tuple[RegisteredMethod, ...]:
        return tuple(self._methods.values())

    def update_metadata(
        self,
        method_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        category: str | None = None,
        defaults: AlgorithmParameters | None = None,
        enabled: bool | None = None,
    ) -> MethodMetadata:
        self._require_write()
        registered = self.get(method_id)
        updates = {
            key: value
            for key, value in (
                ("name", name),
                ("description", description),
                ("category", category),
                ("defaults", defaults),
                ("enabled", enabled),
            )
            if value is not None
        }
        metadata = replace(registered.metadata, **updates)
        if self._save is not None:
            self._save(metadata)
        self._methods[method_id] = replace(registered, metadata=metadata)
        return metadata

    def set_enabled(self, method_id: str, enabled: bool) -> None:
        self._require_write()
        self.update_metadata(method_id, enabled=enabled)

    def unregister(self, method_id: str) -> None:
        self._require_write()
        if self.get(method_id).metadata.builtin:
            raise ValueError("Встроенный метод нельзя удалить.")
        if self._delete is not None:
            self._delete(method_id)
        del self._methods[method_id]

    def compatibility(self, method_id: str, problem: OptimizationTask) -> Compatibility:
        method = self.get(method_id)
        if not method.metadata.enabled:
            return Compatibility(False, "Метод отключён.")
        if not isinstance(problem, OptimizationTask) or not isinstance(
            problem.constraints, ConstraintSet
        ):
            return Compatibility(False, "Тип задачи или ограничений не поддерживается.")
        if len(problem.constraints.bounds) not in method.capabilities.dimensions:
            return Compatibility(False, "Количество переменных не поддерживается методом.")
        required = {"bounds", *problem.constraints.constraint_types}
        if not required <= set(method.capabilities.constraint_types):
            return Compatibility(False, "Метод не поддерживает ограничения выбранной задачи.")
        if problem.direction not in method.capabilities.directions:
            return Compatibility(False, "Метод не поддерживает выбранное направление поиска.")
        return Compatibility(True)

    def require_available(self, method_id: str, problem: OptimizationTask) -> RegisteredMethod:
        method = self.get(method_id)
        compatibility = self.compatibility(method_id, problem)
        if not compatibility.compatible:
            raise ValueError(compatibility.reason)
        return method

    def run(
        self,
        method_id: str,
        problem: OptimizationTask,
        parameters: AlgorithmParameters | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> OptimizationResult:
        method = self.require_available(method_id, problem)
        parameters = parameters if parameters is not None else method.metadata.defaults
        if not isinstance(parameters, AlgorithmParameters):
            raise ValueError("Конфигурация метода должна иметь тип AlgorithmParameters.")
        result = method.implementation.run(problem, parameters, cancelled)
        if not isinstance(result, OptimizationResult):
            raise ValueError("Метод вернул результат неизвестного типа.")
        metrics = getattr(problem, "derived_metrics", ())
        values = (
            tuple(metric.calculate(result.value) for metric in metrics)
            if result.value is not None
            else ()
        )
        return replace(result, method=method.metadata.name, derived_metrics=values)
