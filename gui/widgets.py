from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QLabel,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def fit_to_screen(widget: QWidget, preferred_size: QSize) -> None:
    """Keep the initial client area and window frame inside the current screen."""
    parent = widget.parentWidget()
    screen = parent.screen() if parent is not None else widget.screen()
    if screen is None:
        widget.resize(preferred_size)
        return
    available = screen.availableGeometry()
    title_height = widget.style().pixelMetric(QStyle.PixelMetric.PM_TitleBarHeight)
    size_limit = QSize(
        max(1, available.width() - 24),
        max(1, available.height() - title_height - 24),
    )
    widget.setMinimumSize(widget.minimumSize().boundedTo(size_limit))
    size = preferred_size.boundedTo(size_limit).expandedTo(widget.minimumSize())
    widget.resize(size)
    if size != preferred_size:
        widget.move(
            available.x() + (available.width() - size.width()) // 2,
            available.y() + (available.height() - size.height() - title_height) // 2,
        )


def text_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def page_layout(title: str, *, page: QWidget | None = None) -> tuple[QWidget, QVBoxLayout]:
    if page is None:
        page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(12)
    heading = text_label(title)
    font = heading.font()
    font.setPointSize(18)
    font.setBold(True)
    heading.setFont(font)
    layout.addWidget(heading)
    return page, layout


def collapsible(title: str, content: QWidget) -> QWidget:
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    button = QToolButton()
    button.setText(title)
    button.setCheckable(True)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    button.setArrowType(Qt.ArrowType.RightArrow)
    button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    button.toggled.connect(content.setVisible)
    button.toggled.connect(
        lambda opened: button.setArrowType(
            Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow
        )
    )
    layout.addWidget(button)
    content.hide()
    layout.addWidget(content)
    return panel


def scroll_panel(widget: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidget(widget)
    return scroll
