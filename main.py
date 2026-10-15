import argparse
import getpass
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from core.auth import AuthService
from core.plugin_loader import create_method_registry
from core.storage import SQLiteStorage
from core.tasks import TaskLibrary
from gui.login_dialog import LoginDialog
from gui.main_window import MainWindow

DATABASE = Path(__file__).parent / "data" / "reactoropt.sqlite3"


def create_admin(database: Path, login: str) -> int:
    storage = SQLiteStorage(database)
    try:
        password = getpass.getpass("Пароль администратора: ")
        confirmation = getpass.getpass("Повторите пароль: ")
        try:
            if password != confirmation:
                raise ValueError("Пароли не совпадают.")
            AuthService(storage.engine).create_initial_admin(login, password)
        finally:
            del password, confirmation
        print("Первый администратор создан.")
        return 0
    finally:
        storage.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="ReactorOpt")
    commands = parser.add_subparsers(dest="command")
    command = commands.add_parser("create-admin", help="Создать первого администратора")
    command.add_argument("login", help="Логин администратора")
    args = parser.parse_args()
    if args.command == "create-admin":
        try:
            return create_admin(DATABASE, args.login)
        except (ValueError, SQLAlchemyError, OSError, EOFError, KeyboardInterrupt) as error:
            print(f"Не удалось создать администратора: {error}", file=sys.stderr)
            return 1
    app = QApplication(sys.argv)
    app.setApplicationName("ReactorOpt")
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    storage = None
    try:
        while True:
            storage = SQLiteStorage(DATABASE)
            auth = AuthService(storage.engine)
            dialog = LoginDialog(auth)
            if dialog.exec() != LoginDialog.DialogCode.Accepted:
                return 0
            session = dialog.session
            dialog.deleteLater()
            registry, library = create_method_registry(), TaskLibrary()
            storage.attach(library, registry)
            storage.bind_session(session)
            window = MainWindow(registry, library, session=session)
            window.closed.connect(app.quit)
            window.show()
            app.exec()
            session.logout()
            again = window.logout_requested
            window.deleteLater()
            app.processEvents()
            storage.close()
            storage = None
            if not again:
                return 0
    except (SQLAlchemyError, OSError, ValueError) as error:
        QMessageBox.critical(None, "Ошибка запуска", f"Не удалось открыть библиотеку: {error}")
        return 1
    finally:
        if storage is not None:
            storage.close()


if __name__ == "__main__":
    sys.exit(main())
