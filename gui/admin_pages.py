from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from core.auth import Session, User
from core.registry import MethodRegistry

ROLE_NAMES = {"user": "Пользователь", "admin": "Администратор"}


# Учётные записи


class UserDialog(QDialog):
    def __init__(
        self,
        session: Session,
        user: User | None = None,
        *,
        reset: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.session, self.target, self.reset = session, user, reset
        self.setWindowTitle("Сброс пароля" if reset else "Учётная запись")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.login = QLineEdit(user.login if user else "")
        self.login.setMaxLength(64)
        self.login.setReadOnly(user is not None)
        form.addRow("Логин:", self.login)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setMaxLength(1024)
        self.confirm_password = QLineEdit()
        self.confirm_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_password.setMaxLength(1024)
        if user is None or reset:
            form.addRow("Новый пароль:", self.password)
            form.addRow("Повторите пароль:", self.confirm_password)
        self.role = QComboBox()
        for key, name in ROLE_NAMES.items():
            self.role.addItem(name, key)
        self.role.setCurrentIndex(self.role.findData(user.role if user else "user"))
        self.active = QCheckBox("Учётная запись активна")
        self.active.setChecked(user.active if user else True)
        if not reset:
            form.addRow("Роль:", self.role)
            if user:
                form.addRow(self.active)
        layout.addLayout(form)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        try:
            self.session.require_admin()
            auth = self.session.auth
            if self.target is None or self.reset:
                if self.password.text() != self.confirm_password.text():
                    raise ValueError("Пароли не совпадают.")
                if self.reset:
                    auth.reset_password(self.session, self.target.id, self.password.text())
                else:
                    auth.create_user(
                        self.session,
                        self.login.text(),
                        self.password.text(),
                        self.role.currentData(),
                    )
            else:
                auth.update_user(
                    self.session,
                    self.target.id,
                    role=self.role.currentData(),
                    active=self.active.isChecked(),
                )
        except (ValueError, SQLAlchemyError) as error:
            self.error.setText(str(error))
            return
        finally:
            self.password.clear()
            self.confirm_password.clear()
        super().accept()

    def reject(self) -> None:
        self.password.clear()
        self.confirm_password.clear()
        super().reject()


class UsersPage(QWidget):
    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Пользователи"))
        self.users = QTreeWidget()
        self.users.setHeaderLabels(["Логин", "Роль", "Состояние"])
        self.users.setRootIsDecorated(False)
        layout.addWidget(self.users)
        buttons = QHBoxLayout()
        for text, callback in (
            ("Создать", lambda: self.open_user(create=True)),
            ("Роль и блокировка", self.open_user),
            ("Сбросить пароль", lambda: self.open_user(reset=True)),
            ("Обновить", self.refresh),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.refresh()

    def refresh(self) -> None:
        try:
            users = self.session.auth.list_users(self.session)
            self.users.clear()
            for user in users:
                item = QTreeWidgetItem(
                    [
                        user.login,
                        ROLE_NAMES[user.role],
                        "Активна" if user.active else "Заблокирована",
                    ]
                )
                item.setData(0, Qt.ItemDataRole.UserRole, user)
                self.users.addTopLevelItem(item)
            self.error.clear()
        except (ValueError, SQLAlchemyError) as error:
            self.error.setText(str(error))

    def open_user(
        self, _checked: bool = False, *, create: bool = False, reset: bool = False
    ) -> None:
        try:
            self.session.require_admin()
            item = self.users.currentItem()
            user = item.data(0, Qt.ItemDataRole.UserRole) if item and not create else None
            if not create and user is None:
                return
            UserDialog(self.session, user, reset=reset, parent=self).exec()
            self.refresh()
        except (ValueError, SQLAlchemyError) as error:
            self.error.setText(str(error))


# Метаданные методов


class MethodEditor(QDialog):
    def __init__(
        self, registry: MethodRegistry, method_id: str, session: Session, parent: QWidget
    ) -> None:
        super().__init__(parent)
        self.registry, self.method_id, self.session = registry, method_id, session
        self.setWindowTitle("Метаданные метода")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        metadata = registry.get(method_id).metadata
        self.name = QLineEdit(metadata.name)
        self.category = QLineEdit(metadata.category)
        self.description = QPlainTextEdit(metadata.description)
        form.addRow("Название:", self.name)
        form.addRow("Категория:", self.category)
        form.addRow("Описание:", self.description)
        layout.addLayout(form)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        try:
            self.session.require_admin()
            self.registry.update_metadata(
                self.method_id,
                name=self.name.text(),
                category=self.category.text(),
                description=self.description.toPlainText(),
            )
        except (ValueError, SQLAlchemyError) as error:
            self.error.setText(str(error))
            return
        super().accept()


# Доверенные плагины


class PluginsPage(QWidget):
    def __init__(self, registry: MethodRegistry, session: Session, changed) -> None:
        super().__init__()
        self.registry, self.session, self.changed = registry, session, changed
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Доверенные плагины методов"))
        info = QLabel(
            "Показаны модули, зарегистрированные в Python-коде приложения. "
            "Загрузка стороннего кода через интерфейс не поддерживается."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        self.plugins = QTreeWidget()
        self.plugins.setHeaderLabels(["Метод", "Модуль", "Состояние"])
        self.plugins.setRootIsDecorated(False)
        layout.addWidget(self.plugins)
        buttons = QHBoxLayout()
        for title, callback in (
            ("Включить / отключить", self.toggle),
            ("Удалить из реестра", self.remove),
        ):
            button = QPushButton(title)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.refresh()

    def refresh(self) -> None:
        self.session.require_admin()
        self.plugins.clear()
        for method in self.registry.list_methods():
            meta = method.metadata
            item = QTreeWidgetItem(
                [meta.name, meta.implementation, "Включён" if meta.enabled else "Отключён"]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, meta.id)
            self.plugins.addTopLevelItem(item)

    def toggle(self) -> None:
        self.change(remove=False)

    def remove(self) -> None:
        self.change(remove=True)

    def change(self, *, remove: bool) -> None:
        try:
            self.session.require_admin()
            item = self.plugins.currentItem()
            if item is None:
                return
            method_id = item.data(0, Qt.ItemDataRole.UserRole)
            if remove:
                self.registry.unregister(method_id)
            else:
                method = self.registry.get(method_id)
                self.registry.set_enabled(method_id, not method.metadata.enabled)
            self.error.clear()
            self.refresh()
            self.changed()
        except (ValueError, SQLAlchemyError) as error:
            self.error.setText(str(error))
