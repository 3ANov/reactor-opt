import os
from pathlib import Path
from time import monotonic, sleep

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QSize, Qt  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea, QToolButton, QWidget  # noqa: E402

from core.auth import AuthService  # noqa: E402
from core.plugin_loader import create_method_registry  # noqa: E402
from core.storage import SQLiteStorage  # noqa: E402
from core.tasks import TaskLibrary  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402


def settle(app, window):
    deadline = monotonic() + 15
    while window.visualization_busy or window.worker is not None:
        app.processEvents()
        if monotonic() >= deadline:
            pytest.fail("Не завершилась фоновая операция интерфейса.")
        sleep(0.005)
    for _ in range(5):
        app.processEvents()


@pytest.fixture(scope="module")
def responsive_app():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    original_font = app.font()
    # The offscreen Windows platform does not discover system fonts itself.
    font_path = Path("C:/Windows/Fonts/segoeui.ttf")
    if font_path.is_file():
        QFontDatabase.addApplicationFont(str(font_path))
        QFontDatabase.addApplicationFont(str(font_path.with_name("segoeuib.ttf")))
        app.setFont(QFont("Segoe UI", 9))
    yield app
    app.setFont(original_font)


@pytest.fixture
def responsive_window(responsive_app):
    storage = SQLiteStorage(":memory:")
    auth = AuthService(storage.engine)
    auth.create_initial_admin("responsive_admin", "TestPassword2026")
    session = auth.authenticate("responsive_admin", "TestPassword2026")
    registry, library = create_method_registry(), TaskLibrary()
    storage.attach(library, registry)
    storage.bind_session(session)
    window = MainWindow(registry, library, session=session)
    window.show()
    settle(responsive_app, window)
    try:
        yield window
    finally:
        window.close()
        settle(responsive_app, window)
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        responsive_app.processEvents()
        session.logout()
        storage.close()


@pytest.mark.parametrize(
    "size",
    [
        (1120, 640),
        (836, 540),
        (760, 540),
    ],
)
def test_pages_fit_small_screen_without_horizontal_scrolling(
    responsive_app, responsive_window, size
):
    window = responsive_window
    window.resize(*size)
    for index in range(window.pages.count()):
        window.navigation.button(index).click()
        settle(responsive_app, window)
        page = window.pages.currentWidget()
        assert window.pages.rect().contains(page.geometry())
        if isinstance(page, QScrollArea):
            assert page.horizontalScrollBar().maximum() == 0
        else:
            assert page.controls_scroll.horizontalScrollBar().maximum() == 0
            assert page.plot_scroll.horizontalScrollBar().maximum() == 0


def test_expanded_settings_and_calculation_result_remain_reachable(
    responsive_app, responsive_window
):
    window = responsive_window
    window.resize(760, 540)
    page = window.optimization_page
    page.advanced_parameters.findChild(QToolButton).setChecked(True)
    settle(responsive_app, window)
    assert page.controls_scroll.horizontalScrollBar().maximum() == 0

    for field in (
        page.max_evaluations,
        page.save_defaults_button,
        page.calculate_button,
    ):
        page.controls_scroll.ensureWidgetVisible(field)
        responsive_app.processEvents()
        assert not field.visibleRegion().isEmpty()
        assert field.visibleRegion().boundingRect().width() == field.width()

    page.max_evaluations.setValue(2)
    page.calculate_button.click()
    settle(responsive_app, window)
    assert page.last_result is not None
    reason = page.result_values["reason"]
    page.controls_scroll.ensureWidgetVisible(reason)
    responsive_app.processEvents()
    assert reason.isVisible()
    assert reason.visibleRegion().boundingRect().width() == reason.width()
    position = reason.mapTo(page.controls_scroll.viewport(), QPoint(0, 0))
    assert 0 <= position.x() < page.controls_scroll.viewport().width()


@pytest.mark.parametrize(
    "available",
    [QRect(0, 0, 1680, 1010), QRect(-1120, 0, 1120, 640)],
)
def test_initial_window_fits_available_screen(responsive_app, monkeypatch, available):
    from gui.widgets import fit_to_screen

    class Screen:
        def availableGeometry(self):
            return available

    monkeypatch.setattr(QWidget, "screen", lambda _: Screen())
    widget = QWidget()
    widget.setMinimumSize(760, 540)
    try:
        fit_to_screen(widget, QSize(1240, 800))
        assert widget.width() <= available.width()
        assert widget.height() <= available.height()
        assert widget.width() >= 760
        assert widget.height() >= 540
        if available.width() >= 1240 and available.height() >= 850:
            assert widget.size() == QSize(1240, 800)
    finally:
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_widening_on_another_tab_restores_controls_width(responsive_app, responsive_window):
    window = responsive_window
    window.resize(1240, 800)
    settle(responsive_app, window)
    splitter = window.optimization_page.splitter
    splitter.moveSplitter(370, 1)
    responsive_app.processEvents()
    original_width = splitter.sizes()[0]

    window.navigation.button(1).click()
    window.resize(760, 540)
    window.navigation.button(0).click()
    settle(responsive_app, window)
    assert splitter.orientation() == Qt.Orientation.Vertical
    window.navigation.button(1).click()
    window.resize(1240, 800)
    window.navigation.button(0).click()
    settle(responsive_app, window)
    assert splitter.orientation() == Qt.Orientation.Horizontal
    assert splitter.sizes()[0] == original_width
