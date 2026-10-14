from typing import TYPE_CHECKING

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from gui.admin_pages import ROLE_NAMES
from gui.widgets import page_layout, text_label

if TYPE_CHECKING:
    from gui.main_window import MainWindow


SECTIONS = (
    "Оптимизация",
    "Библиотека задач",
    "Библиотека методов",
    "Настройки",
    "Пользователи",
    "Плагины",
)


def build_navigation_panel(window: "MainWindow") -> QWidget:
    window.sidebar = QWidget()
    window.sidebar.setObjectName("sidebar")
    window.sidebar.setFixedWidth(220)
    window.sidebar.setAccessibleName("Разделы приложения")
    layout = QVBoxLayout(window.sidebar)
    layout.setContentsMargins(10, 20, 10, 12)
    layout.setSpacing(6)
    title = QLabel("ReactorOpt")
    font = title.font()
    font.setPointSize(18)
    font.setBold(True)
    title.setFont(font)
    layout.addWidget(title)
    layout.addSpacing(18)
    window.navigation = QButtonGroup(window)
    window.navigation.setExclusive(True)
    icons = (
        QStyle.StandardPixmap.SP_ComputerIcon,
        QStyle.StandardPixmap.SP_DirIcon,
        QStyle.StandardPixmap.SP_FileDialogDetailedView,
        QStyle.StandardPixmap.SP_FileDialogContentsView,
        QStyle.StandardPixmap.SP_DirHomeIcon,
        QStyle.StandardPixmap.SP_DriveHDIcon,
    )
    sections = SECTIONS if window.session.is_admin else SECTIONS[:4]
    for index, (text, icon) in enumerate(zip(sections, icons)):
        button = QPushButton(text)
        button.setObjectName("navigationButton")
        button.setCheckable(True)
        button.setFixedHeight(44)
        button.setIcon(window.style().standardIcon(icon))
        button.setIconSize(QSize(16, 16))
        window.navigation.addButton(button, index)
        layout.addWidget(button)
    layout.addStretch()
    user = window.session.user
    window.account_label = text_label(f"{user.login}\n{ROLE_NAMES[user.role]}")
    layout.addWidget(window.account_label)
    window.logout_button = QPushButton("Выйти")
    window.logout_button.clicked.connect(window.logout)
    layout.addWidget(window.logout_button)
    return window.sidebar


def build_settings_page(window: "MainWindow") -> QWidget:
    page, layout = page_layout("Настройки")
    form = QFormLayout()
    window.theme = QComboBox()
    window.theme.addItems(["Светлая", "Тёмная"])
    window.theme.currentIndexChanged.connect(window.apply_theme)
    form.addRow("Тема интерфейса:", window.theme)
    layout.addLayout(form)
    layout.addWidget(text_label("Тема действует до закрытия приложения."))
    layout.addStretch()
    return page


def apply_theme(window: "MainWindow", index: int) -> None:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    dark = index == 1
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#24272d" if dark else "#f3f4f6",
        QPalette.ColorRole.WindowText: "#f0f1f3" if dark else "#20242b",
        QPalette.ColorRole.Base: "#1c1f24" if dark else "#ffffff",
        QPalette.ColorRole.AlternateBase: "#30343b" if dark else "#e9edf2",
        QPalette.ColorRole.Text: "#f0f1f3" if dark else "#20242b",
        QPalette.ColorRole.Button: "#343941" if dark else "#e5e9ef",
        QPalette.ColorRole.ButtonText: "#f0f1f3" if dark else "#20242b",
        QPalette.ColorRole.Highlight: "#3268ad" if dark else "#245da3",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.ToolTipBase: "#343941" if dark else "#ffffff",
        QPalette.ColorRole.ToolTipText: "#f0f1f3" if dark else "#20242b",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(
            QPalette.ColorGroup.Disabled, role, QColor("#9299a5" if dark else "#6c7480")
        )
    app.setPalette(palette)
    base = colors[QPalette.ColorRole.Base]
    text = colors[QPalette.ColorRole.Text]
    active = colors[QPalette.ColorRole.Highlight]
    hover = "#343e4c" if dark else "#e2eaf5"
    muted = "#adb6c4" if dark else "#596575"
    window.setStyleSheet(f"""
        QWidget#sidebar {{ background: {base}; }}
        QPushButton#navigationButton {{
            text-align: left; padding: 8px; border: none; border-radius: 5px;
            background: transparent; color: {text};
        }}
        QPushButton#navigationButton:hover {{ background: {hover}; }}
        QPushButton#navigationButton:checked {{ background: {active}; color: white; }}
        QPushButton#navigationButton:disabled {{ color: {muted}; }}
        QPushButton#directionButton {{ padding: 6px 4px; }}
        QPushButton#directionButton:checked {{ background: {active}; color: white; }}
        QPushButton#directionButton:hover:!checked {{ background: {hover}; }}
        QLabel#secondaryText {{ color: {muted}; }}
    """)
    window.objective_plot.set_theme(dark)
    if window.surface_plot is not None:
        window.surface_plot.set_theme(dark)
