from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from gui.widgets import collapsible, page_layout, text_label

if TYPE_CHECKING:
    from gui.main_window import MainWindow


PARAMETER_LABELS = {
    "precision": "Точность координат",
    "initial_step": "Начальный шаг поиска",
    "regions": "Перспективных областей",
    "max_evaluations": "Лимит вычислений",
    "keep_history": "Сохранение истории",
}


def build_tasks_page(window: "MainWindow") -> QWidget:
    page, layout = page_layout("Библиотека задач")
    window.task_list = QTreeWidget()
    window.task_list.setHeaderLabels(["Задание", "Тип", "Направление"])
    window.task_list.setRootIsDecorated(False)
    window.task_list.setMinimumHeight(180)
    window.task_list.itemDoubleClicked.connect(lambda *_: window.view_task())
    window.task_list.currentItemChanged.connect(window.update_task_actions)
    layout.addWidget(window.task_list)
    buttons = QHBoxLayout()
    window.task_actions = []
    for title, callback in (
        ("Создать", window.add_task),
        ("Изменить", window.edit_task),
        ("Дублировать", window.duplicate_task),
        ("Удалить", window.delete_task),
        ("Просмотреть", window.view_task),
    ):
        button = QPushButton(title)
        button.clicked.connect(callback)
        buttons.addWidget(button)
        window.task_actions.append(button)
    layout.addLayout(buttons)
    window.task_library_error = text_label("")
    layout.addWidget(window.task_library_error)
    layout.addStretch()
    window.fill_task_list()
    return page


def build_methods_page(window: "MainWindow") -> QWidget:
    page, layout = page_layout("Библиотека методов")
    window.method_library = QWidget()
    window.method_library_layout = QVBoxLayout(window.method_library)
    window.method_library_layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(window.method_library)
    layout.addStretch()
    return page


def render_method_library(window: "MainWindow") -> None:
    while window.method_library_layout.count():
        item = window.method_library_layout.takeAt(0)
        widget = item.widget()
        widget.setParent(None)
        widget.deleteLater()
    direction_names = {"minimize": "минимизация", "maximize": "максимизация"}
    constraint_names = {
        "bounds": "границы переменных",
        "linear_inequality": "линейные неравенства",
    }
    for method in window.registry.list_methods():
        metadata = method.metadata
        group = QGroupBox(metadata.name)
        card_layout = QVBoxLayout(group)
        summary = text_label(metadata.category)
        card_layout.addWidget(summary)
        details = QWidget()
        form = QFormLayout(details)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        compatibility = window.registry.compatibility(metadata.id, window.current_problem())
        support = (
            "Переменных: "
            + ", ".join(map(str, method.capabilities.dimensions))
            + "; ограничения: "
            + ", ".join(
                constraint_names.get(name, name) for name in method.capabilities.constraint_types
            )
            + "; направления: "
            + ", ".join(direction_names[name] for name in method.capabilities.directions)
        )
        defaults = []
        for name in method.supported_parameters:
            value = getattr(metadata.defaults, name)
            value_text = ("да" if value else "нет") if isinstance(value, bool) else str(value)
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
            lambda checked, method_id=metadata.id: window.change_method_availability(
                method_id, checked
            )
        )
        card_layout.addWidget(enabled)
        enabled.setVisible(window.session.is_admin)
        if window.session.is_admin:
            edit = QPushButton("Изменить метаданные")
            edit.clicked.connect(lambda _checked=False, key=metadata.id: window.edit_method(key))
            card_layout.addWidget(edit)
        window.method_library_layout.addWidget(group)
    if not window.registry.list_methods():
        window.method_library_layout.addWidget(text_label("Зарегистрированных методов нет."))
