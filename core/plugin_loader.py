import hashlib
import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType

from core.registry import MethodRegistry
from plugin_api import MethodMetadata, OptimizationMethod


def plugin_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "plugins"
    return Path(__file__).resolve().parent.parent / "plugins"


def load_module(path: Path, directory: Path) -> ModuleType:
    namespace = "_reactoropt_plugins_" + hashlib.sha256(str(directory).encode()).hexdigest()[:12]
    relative = path.relative_to(directory)
    parts = relative.with_suffix("").parts
    for depth in range(len(parts)):
        name = ".".join((namespace, *parts[:depth]))
        if name not in sys.modules:
            package = ModuleType(name)
            package.__path__ = [str(directory.joinpath(*parts[:depth]))]
            sys.modules[name] = package
    name = ".".join((namespace, *parts))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("Не удалось загрузить Python-модуль.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def create_method_registry(directory: Path | None = None) -> MethodRegistry:
    registry = MethodRegistry()
    directory = (directory or plugin_directory()).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    loaded = []
    for path in sorted(directory.rglob("*.py")):
        if any(part.startswith(("_", ".")) for part in path.relative_to(directory).parts):
            continue
        try:
            module = load_module(path, directory)
            classes = [
                cls
                for cls in vars(module).values()
                if inspect.isclass(cls)
                and cls.__module__ == module.__name__
                and issubclass(cls, OptimizationMethod)
                and not inspect.isabstract(cls)
            ]
            if not classes:
                raise ValueError("Плагин должен предоставить класс-наследник OptimizationMethod.")
            for cls in classes:
                try:
                    method = cls()
                    if not isinstance(method.metadata, MethodMetadata):
                        raise ValueError("Плагин должен предоставить MethodMetadata из plugin_api.")
                    loaded.append((method.metadata.priority, path, method))
                except Exception as error:
                    registry.load_errors.append(
                        f"Плагин {path.relative_to(directory)}, класс {cls.__name__}: {error}"
                    )
        except Exception as error:
            registry.load_errors.append(f"Плагин {path.relative_to(directory)}: {error}")
    ordered = sorted(
        loaded,
        key=lambda item: (not item[2].metadata.builtin, -item[0], str(item[1])),
    )
    for _priority, path, method in ordered:
        try:
            registry.register(method, builtin=method.metadata.builtin)
        except Exception as error:
            registry.load_errors.append(f"Плагин {path.relative_to(directory)}: {error}")
    return registry
