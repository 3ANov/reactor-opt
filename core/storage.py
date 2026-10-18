import json
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import URL, create_engine, delete, select
from sqlalchemy.dialects.sqlite import insert

from core.auth import Session
from core.database import metadata, methods, tasks
from core.plugin_loader import plugin_directory
from core.registry import MethodRegistry
from core.task_serialization import json_data as json_data
from core.task_serialization import json_task_record
from core.task_serialization import json_text as json_text
from core.task_serialization import task_from_record as task_from_record
from core.task_serialization import task_to_record as task_to_record
from core.tasks import TaskDefinition, TaskLibrary
from plugin_api import AlgorithmParameters, MethodMetadata


class SQLiteStorage:
    def __init__(self, path: str | Path, *, tasks_directory: Path | None = None) -> None:
        self._session: Session | None = None
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            URL.create("sqlite", database=str(path)),
            hide_parameters=True,
        )
        try:
            metadata.create_all(self.engine)
            self._import_json_tasks(tasks_directory or plugin_directory() / "tasks")
        except Exception:
            self.engine.dispose()
            raise

    def bind_session(self, session: Session) -> None:
        if self._session is not None:
            raise ValueError("Контекст доступа уже установлен.")
        session.user
        self._session = session

    def _require_write(self) -> None:
        if self._session is not None:
            self._session.require_admin()

    def close(self) -> None:
        self.engine.dispose()

    def _import_json_tasks(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        with self.engine.begin() as connection:
            existing_ids = set(connection.scalars(select(tasks.c.id)))
            for path in sorted(directory.glob("*.json")):
                try:
                    definitions = json.loads(path.read_text(encoding="utf-8-sig"))
                    if isinstance(definitions, dict):
                        definitions = [definitions]
                    if not isinstance(definitions, list):
                        raise ValueError("Нужен объект задания или список заданий.")
                    for definition in definitions:
                        if not isinstance(definition, dict):
                            raise ValueError("Каждое задание должно быть JSON-объектом.")
                        if definition["id"] in existing_ids:
                            continue
                        record = self._json_task_record(definition)
                        task_from_record(record)
                        statement = insert(tasks).values(**record)
                        connection.execute(
                            statement.on_conflict_do_nothing(index_elements=[tasks.c.id])
                        )
                        existing_ids.add(definition["id"])
                except (ValueError, KeyError, TypeError, ArithmeticError) as error:
                    raise ValueError(
                        f"Не удалось загрузить задания из {path.name}: {error}"
                    ) from error

    _json_task_record = staticmethod(json_task_record)

    def load_tasks(self) -> tuple[TaskDefinition, ...]:
        with self.engine.connect() as connection:
            records = connection.execute(
                select(tasks).order_by(tasks.c.is_builtin.desc(), tasks.c.name, tasks.c.id)
            ).mappings()
            try:
                return tuple(task_from_record(record) for record in records)
            except (
                ValueError,
                KeyError,
                TypeError,
                ArithmeticError,
                AttributeError,
                IndexError,
                RecursionError,
            ) as error:
                raise ValueError(f"Не удалось загрузить задания из базы: {error}") from error

    def attach(self, library: TaskLibrary, registry: MethodRegistry) -> None:
        library.load(self.load_tasks())
        self._load_method_settings(registry)
        for method in registry.list_methods():
            self.save_method(method.metadata)
        library.set_persistence(self.save_task, self.delete_task)
        registry.set_persistence(self.save_method, self.delete_method)

    def _load_method_settings(self, registry: MethodRegistry) -> None:
        with self.engine.connect() as connection:
            records = connection.execute(select(methods)).mappings().all()
        available_ids = {method.metadata.id for method in registry.list_methods()}
        for record in records:
            if record["id"] not in available_ids:
                continue
            registry.update_metadata(
                record["id"],
                name=record["name"],
                description=record["description"],
                category=record["category"],
                enabled=bool(record["enabled"]),
                defaults=AlgorithmParameters(**json.loads(record["defaults_json"])),
            )

    def save_task(self, task: TaskDefinition) -> None:
        self._require_write()
        record = task_to_record(task)
        task_from_record(record)
        statement = insert(tasks).values(**record)
        with self.engine.begin() as connection:
            saved = connection.scalar(select(tasks.c.is_builtin).where(tasks.c.id == task.id))
            if bool(saved) != task.builtin:
                raise ValueError("Нельзя менять происхождение задания.")
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[tasks.c.id],
                    set_={
                        column.name: statement.excluded[column.name]
                        for column in tasks.columns
                        if not column.primary_key
                    },
                )
            )

    def delete_task(self, task_id: str) -> None:
        self._require_write()
        with self.engine.begin() as connection:
            builtin = connection.scalar(select(tasks.c.is_builtin).where(tasks.c.id == task_id))
            if builtin:
                raise ValueError("Встроенную задачу нельзя удалить.")
            connection.execute(delete(tasks).where(tasks.c.id == task_id))

    def save_method(self, metadata: MethodMetadata) -> None:
        self._require_write()
        record = {
            "id": metadata.id,
            "name": metadata.name,
            "description": metadata.description,
            "category": metadata.category,
            "enabled": int(metadata.enabled),
            "defaults_json": json.dumps(asdict(metadata.defaults), allow_nan=False),
        }
        statement = insert(methods).values(**record)
        with self.engine.begin() as connection:
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[methods.c.id],
                    set_={name: statement.excluded[name] for name in record if name != "id"},
                )
            )

    def delete_method(self, method_id: str) -> None:
        self._require_write()
        with self.engine.begin() as connection:
            connection.execute(delete(methods).where(methods.c.id == method_id))
