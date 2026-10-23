from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from core.auth import Session
from core.problem import OptimizationProblem
from core.registry import MethodRegistry
from core.tasks import TaskDefinition, TaskLibrary
from gui.admin_pages import MethodEditor
from gui.task_editor import TaskEditor
from gui.widgets import collapsible, page_layout, text_label

PARAMETER_LABELS = {
    "precision": "Точность координат",
    "function_precision": "Точность по функции ε",
    "stopping_criterion": "Критерий остановки",
    "initial_step": "Начальный шаг поиска",
    "regions": "Перспективных областей",
    "max_evaluations": "Лимит вычислений",
    "keep_history": "Сохранение истории",
}


class TasksPage(QWidget):
    """Manage the task library without depending on the main window's widgets."""

    changed = Signal(object)
    message = Signal(str)

    def __init__(self, task_library: TaskLibrary, session: Session) -> None:
        super().__init__()
        self.task_library = task_library
        self.session = session
        self.busy = False
        self._build_layout()

    def _build_layout(self) -> None:
        _, layout = page_layout("Библиотека задач", page=self)
        self.task_list = QTreeWidget()
        self.task_list.setHeaderLabels(["Задание", "Тип", "Направление"])
        self.task_list.setRootIsDecorated(False)
        self.task_list.setMinimumHeight(180)
        self.task_list.itemDoubleClicked.connect(lambda *_: self.view_task())
        self.task_list.currentItemChanged.connect(self.update_task_actions)
        layout.addWidget(self.task_list)
        buttons = QHBoxLayout()
        self.task_actions = []
        for title, callback in (
            ("Создать", self.add_task),
            ("Изменить", self.edit_task),
            ("Дублировать", self.duplicate_task),
            ("Удалить", self.delete_task),
            ("Просмотреть", self.view_task),
        ):
            button = QPushButton(title)
            button.clicked.connect(callback)
            buttons.addWidget(button)
            self.task_actions.append(button)
        layout.addLayout(buttons)
        self.task_library_error = text_label("")
        layout.addWidget(self.task_library_error)
        layout.addStretch()
        self.fill_task_list()

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.update_task_actions()

    def allow_admin(self) -> bool:
        try:
            self.session.require_admin()
            return True
        except ValueError as error:
            self.message.emit(str(error))
            return False

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
                not self.busy
                and (index == 0 or task is not None and (index != 3 or not task.builtin))
            )

    def view_task(self) -> None:
        task = self.selected_library_task()
        if task is not None:
            TaskEditor(task, self.window(), view_only=True).exec()

    def save_edited_task(self, definition: TaskDefinition, *, existing: bool) -> None:
        self.session.require_admin()
        if existing:
            self.task_library.update(definition)
        else:
            self.task_library.add(definition)
        self.changed.emit(definition.id)
        self.task_library_error.clear()

    def add_task(self) -> None:
        if not self.allow_admin():
            return
        if self.busy:
            return
        dialog = TaskEditor(parent=self.window())
        if dialog.exec() == TaskEditor.DialogCode.Accepted:
            try:
                self.save_edited_task(dialog.saved_task, existing=False)
            except (ValueError, SQLAlchemyError) as error:
                self.task_library_error.setText(f"Не удалось сохранить задание: {error}")

    def edit_task(self) -> None:
        if not self.allow_admin():
            return
        task = self.selected_library_task()
        if task is None or self.busy:
            return
        dialog = TaskEditor(task, self.window())
        if dialog.exec() == TaskEditor.DialogCode.Accepted:
            try:
                self.save_edited_task(dialog.saved_task, existing=True)
            except (ValueError, SQLAlchemyError) as error:
                self.task_library_error.setText(f"Не удалось сохранить задание: {error}")

    def duplicate_task(self) -> None:
        if not self.allow_admin():
            return
        task = self.selected_library_task()
        if task is None or self.busy:
            return
        try:
            copied = self.task_library.duplicate(task.id)
            self.changed.emit(copied.id)
            self.task_library_error.clear()
        except (ValueError, SQLAlchemyError) as error:
            self.task_library_error.setText(str(error))

    def delete_task(self) -> None:
        if not self.allow_admin():
            return
        task = self.selected_library_task()
        if task is None or self.busy:
            return
        if task.builtin:
            self.task_library_error.setText("Встроенное задание нельзя удалить.")
            return
        confirm = QMessageBox(
            QMessageBox.Icon.Question,
            "Удаление задания",
            f"Удалить задание «{task.name}»?",
            parent=self.window(),
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
            self.changed.emit(None)
            self.task_library_error.clear()
        except (ValueError, SQLAlchemyError) as error:
            self.task_library_error.setText(str(error))


class MethodsPage(QWidget):
    """Render method metadata and handle library administration."""

    changed = Signal()
    message = Signal(str)

    def __init__(
        self,
        registry: MethodRegistry,
        session: Session,
        current_problem: Callable[[], OptimizationProblem],
    ) -> None:
        super().__init__()
        self.registry = registry
        self.session = session
        self.current_problem = current_problem
        self._build_layout()

    def _build_layout(self) -> None:
        _, layout = page_layout("Библиотека методов", page=self)
        self.method_library = QWidget()
        self.method_library_layout = QVBoxLayout(self.method_library)
        self.method_library_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.method_library)
        layout.addStretch()

    def refresh(self) -> None:
        while self.method_library_layout.count():
            item = self.method_library_layout.takeAt(0)
            widget = item.widget()
            widget.setParent(None)
            widget.deleteLater()
        direction_names = {"minimize": "минимизация", "maximize": "максимизация"}
        constraint_names = {
            "bounds": "границы переменных",
            "linear_inequality": "линейные неравенства",
        }
        for method in self.registry.list_methods():
            metadata = method.metadata
            group = QGroupBox(metadata.name)
            card_layout = QVBoxLayout(group)
            summary = text_label(metadata.category)
            card_layout.addWidget(summary)
            details = QWidget()
            form = QFormLayout(details)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            compatibility = self.registry.compatibility(metadata.id, self.current_problem())
            support = (
                "Переменных: "
                + ", ".join(map(str, method.capabilities.dimensions))
                + "; ограничения: "
                + ", ".join(
                    constraint_names.get(name, name)
                    for name in method.capabilities.constraint_types
                )
                + "; направления: "
                + ", ".join(direction_names[name] for name in method.capabilities.directions)
            )
            defaults = []
            for name in method.supported_parameters:
                value = getattr(metadata.defaults, name)
                if name == "stopping_criterion":
                    value_text = {
                        "coordinates": "По координатам δ",
                        "function": "По функции ε",
                        "evaluations": "По числу вычислений N",
                    }[value]
                else:
                    value_text = (
                        ("да" if value else "нет") if isinstance(value, bool) else str(value)
                    )
                defaults.append(f"{PARAMETER_LABELS[name]}: {value_text}")
            for name, value in metadata.fixed_parameters:
                title = "Коэффициент уменьшения шага" if name == "step_reduction" else name
                defaults.append(f"{title}: {value:g} (фиксирован)")
            for title, value in (
                ("Категория:", metadata.category),
                ("Принцип работы:", metadata.description),
                ("Доступность:", "Доступен" if compatibility.compatible else compatibility.reason),
                ("Типы задач:", support),
                ("Параметры по умолчанию:", "\n".join(defaults)),
                ("Происхождение:", "Встроенный" if metadata.builtin else "Пользовательский"),
                ("Реализация:", metadata.implementation),
            ):
                form.addRow(title, text_label(value))
            card_layout.addWidget(collapsible("Описание и параметры", details))
            enabled = QCheckBox("Разрешить новые расчёты этим методом")
            enabled.setChecked(metadata.enabled)
            enabled.toggled.connect(
                lambda checked, method_id=metadata.id: self.change_method_availability(
                    method_id, checked
                )
            )
            card_layout.addWidget(enabled)
            enabled.setVisible(self.session.is_admin)
            if self.session.is_admin:
                edit = QPushButton("Изменить метаданные")
                edit.clicked.connect(lambda _checked=False, key=metadata.id: self.edit_method(key))
                card_layout.addWidget(edit)
            self.method_library_layout.addWidget(group)
        if not self.registry.list_methods():
            self.method_library_layout.addWidget(text_label("Зарегистрированных методов нет."))

    def edit_method(self, method_id: str) -> None:
        try:
            self.session.require_admin()
        except ValueError as error:
            self.message.emit(str(error))
            return
        MethodEditor(self.registry, method_id, self.session, self.window()).exec()
        self.changed.emit()
        self.refresh()

    def change_method_availability(self, method_id: str, enabled: bool) -> None:
        try:
            self.session.require_admin()
            self.registry.set_enabled(method_id, enabled)
            self.message.emit("Доступность метода сохранена")
        except (ValueError, SQLAlchemyError) as error:
            self.message.emit(f"Не удалось сохранить доступность метода: {error}")
        self.changed.emit()
        self.refresh()
