from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget


def text_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def page_layout(title: str) -> tuple[QWidget, QVBoxLayout]:
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
