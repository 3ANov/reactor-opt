from decimal import Decimal

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class ParameterSpinBox(QDoubleSpinBox):
    """Keep saved precision while showing ordinary values without extra zeroes."""

    def __init__(self, display_decimals: int, *, width_reference: float) -> None:
        super().__init__()
        self._display_decimals = display_decimals
        self._width_reference = width_reference
        self.setDecimals(max(6, display_decimals))

    def textFromValue(self, value: float) -> str:
        text = super().textFromValue(value)
        separator = self.locale().decimalPoint()
        if separator in text:
            whole, fraction = text.rsplit(separator, 1)
            minimum = getattr(self, "_display_decimals", 2)
            fraction = fraction.rstrip("0").ljust(minimum, "0")
            return whole + separator + fraction
        return text

    def set_parameter_value(self, value: float) -> None:
        places = max(0, -Decimal(str(value)).as_tuple().exponent)
        self.setDecimals(max(self.decimals(), min(323, places)))
        self.setRange(min(self.minimum(), value), max(self.maximum(), value))
        self.setValue(value)
        if self.value() != value:
            raise ValueError("Значение слишком мало для числового поля.")

    def sizeHint(self) -> QSize:
        size = super().sizeHint()
        padding = max(0, size.width() - super().minimumSizeHint().width())
        size.setWidth(min(size.width(), self._reference_width() + padding, 260))
        return size

    def minimumSizeHint(self) -> QSize:
        size = super().minimumSizeHint()
        # A larger allowed range should not move ordinary fields onto another row.
        size.setWidth(min(size.width(), self._reference_width(), 260))
        return size

    def _reference_width(self) -> int:
        text = self.prefix() + self.textFromValue(self._width_reference) + self.suffix()
        return self.fontMetrics().horizontalAdvance(text)


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
