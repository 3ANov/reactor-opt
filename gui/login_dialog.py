from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)
from sqlalchemy.exc import SQLAlchemyError

from core.auth import AuthService, Session


class LoginDialog(QDialog):
    def __init__(self, auth: AuthService) -> None:
        super().__init__()
        self.auth = auth
        self.session: Session | None = None
        self.setWindowTitle("Вход в ReactorOpt")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.login = QLineEdit()
        self.login.setMaxLength(64)
        self.password = QLineEdit()
        self.password.setMaxLength(1024)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Логин:", self.login)
        form.addRow("Пароль:", self.password)
        layout.addLayout(form)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        if not auth.has_users():
            self.error.setText(
                "Сначала создайте администратора командой "
                "uv run python main.py create-admin <логин>."
            )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Войти")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Закрыть")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        password = self.password.text()
        self.password.clear()
        try:
            self.session = self.auth.authenticate(self.login.text(), password)
        except (ValueError, SQLAlchemyError) as error:
            self.error.setText(str(error))
            self.password.setFocus()
            return
        finally:
            del password
        super().accept()

    def reject(self) -> None:
        self.password.clear()
        super().reject()
