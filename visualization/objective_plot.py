from collections import OrderedDict
from collections.abc import Sequence

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QObject, QPointF, QThread, Signal
from PySide6.QtGui import QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGraphicsPolygonItem,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.problem import OptimizationProblem
from plugin_api import LinearConstraintSet, OptimizationResult, Point
from visualization.plot_data import (
    PlotData,
    build_contours,
    compute_grid,
    constraint_segments,
    feasible_polygon,
    sampled_points,
    view_bounds,
)


class GridWorker(QThread):
    ready = Signal(int, object)
    failed = Signal(int, str)

    def __init__(
        self, request: int, problem: OptimizationProblem, resolution: int, parent: QWidget
    ) -> None:
        super().__init__(parent)
        self.request = request
        self.problem = problem
        self.resolution = resolution

    def run(self) -> None:
        try:
            grid = compute_grid(self.problem, self.resolution, self.isInterruptionRequested)
            contours = build_contours(grid, cancelled=self.isInterruptionRequested)
        except InterruptedError:
            return
        except Exception as error:
            self.failed.emit(self.request, str(error))
        else:
            self.ready.emit(self.request, PlotData(grid, contours))


class ObjectivePlot(QWidget):
    idle = Signal()

    def __init__(
        self,
        problem: OptimizationProblem,
        *,
        resolution: int = 151,
        axis_labels: tuple[str, str] = ("x₁", "x₂"),
        value_label: str = "f",
        marker_limit: int = 500,
    ) -> None:
        super().__init__()
        if marker_limit < 1:
            raise ValueError("Лимит маркеров должен быть положительным.")
        self.resolution = resolution
        self.marker_limit = marker_limit
        self.value_label = value_label
        self.coordinate_labels = tuple(label.split(",")[0] for label in axis_labels)
        self.problem = problem
        self.data: PlotData | None = None
        self.result: OptimizationResult | None = None
        self.checked_points: Sequence[Point] = ()
        self.workers: list[GridWorker] = []
        self.request = 0
        self.cache: OrderedDict[tuple[int, int], tuple[OptimizationProblem, PlotData]] = (
            OrderedDict()
        )
        self.contour_items: list[pg.PlotCurveItem] = []
        self.contour_labels: list[pg.TextItem] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        heading = QLabel("Целевая функция и допустимая область")
        heading.setWordWrap(True)
        header.addWidget(heading, 1)
        self.reset_button = QPushButton("Исходный масштаб")
        self.reset_button.clicked.connect(self.reset_view)
        header.addWidget(self.reset_button)
        layout.addLayout(header)
        self.plot = pg.PlotWidget()
        self.plot.setMinimumHeight(240)
        self.plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self.plot.setLabel("bottom", axis_labels[0])
        self.plot.setLabel("left", axis_labels[1])
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.getViewBox().disableAutoRange()
        self.plot.getViewBox().setMouseMode(pg.ViewBox.PanMode)
        layout.addWidget(self.plot, 1)
        self.fill = QGraphicsPolygonItem()
        self.fill.setZValue(-10)
        self.plot.addItem(self.fill)
        self.boundaries = pg.PlotCurveItem(connect="pairs", antialias=True)
        self.plot.addItem(self.boundaries)
        self.best = pg.ScatterPlotItem(size=8, symbol="o")
        self.checked = pg.ScatterPlotItem(size=4, symbol="o")
        self.extremum = pg.ScatterPlotItem(size=15, symbol="star")
        for item, z in ((self.checked, 1), (self.best, 2), (self.extremum, 3)):
            item.setZValue(z)
            self.plot.addItem(item)
        self.annotation = pg.TextItem(anchor=(0, 1))
        self.annotation.setZValue(4)
        self.plot.addItem(self.annotation, ignoreBounds=True)
        self.layers: dict[str, QCheckBox] = {}
        self.display_group = QGroupBox("Отображение графика")
        display_layout = QVBoxLayout(self.display_group)
        display_layout.setSpacing(4)
        switches = QGridLayout()
        switches.setVerticalSpacing(2)
        names = [
            ("contours", "Изолинии"),
            ("boundaries", "Границы"),
            ("feasible", "Допустимая область"),
            ("extremum", "Экстремум"),
            ("best", "Лучшие точки этапов"),
            ("checked", "Проверенные точки"),
        ]
        for i, (key, title) in enumerate(names):
            checkbox = QCheckBox(title)
            checkbox.setChecked(key != "checked")
            checkbox.toggled.connect(self.update_layers)
            switches.addWidget(checkbox, i // 2, i % 2)
            self.layers[key] = checkbox
        display_layout.addLayout(switches)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        display_layout.addWidget(divider)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.hide()
        display_layout.addWidget(self.status)
        self.history_status = QLabel()
        self.history_status.setWordWrap(True)
        display_layout.addWidget(self.history_status)
        self.cursor_label = QLabel("Курсор: —")
        self.cursor_label.setWordWrap(True)
        display_layout.addWidget(self.cursor_label)
        self.point_status = QLabel("Статус точки: —")
        display_layout.addWidget(self.point_status)
        for label in (self.status, self.history_status, self.cursor_label, self.point_status):
            label.setObjectName("secondaryText")
        layout.addWidget(self.display_group)
        self.cursor_inside = False
        self.plot.viewport().installEventFilter(self)
        self.mouse_proxy = pg.SignalProxy(
            self.plot.scene().sigMouseMoved, rateLimit=20, slot=self.mouse_moved
        )
        self.set_theme(False)
        self.set_problem(problem)

    @property
    def busy(self) -> bool:
        return bool(self.workers)

    def set_labels(self, axis_labels: tuple[str, str], value_label: str) -> None:
        self.value_label = value_label
        self.coordinate_labels = tuple(label.split(",")[0] for label in axis_labels)
        for axis, label in zip(("bottom", "left"), axis_labels):
            self.plot.setLabel(axis, label, color=self.colors["text"])
        self.clear_cursor()

    def cancel_pending(self) -> None:
        self.request += 1
        for worker in self.workers:
            worker.requestInterruption()

    def set_problem(self, problem: OptimizationProblem) -> None:
        changed = problem is not self.problem or self.request == 0
        self.cancel_pending()
        self.problem = problem
        self.clear_cursor()
        self.data = None
        self.set_result(None)
        self.clear_contours()
        if not isinstance(problem.constraints, LinearConstraintSet):
            self.fill.setPolygon(QPolygonF())
            self.boundaries.setData([], [])
            if changed:
                self.reset_view()
            self.status.setText("Построение области этого типа ограничений пока не поддерживается.")
            self.status.show()
            return
        polygon = feasible_polygon(problem.constraints)
        self.fill.setPolygon(QPolygonF([QPointF(*point) for point in polygon]))
        bounds = view_bounds(problem.constraints)
        segments = constraint_segments(problem.constraints, bounds)
        self.boundaries.setData(segments[:, 0], segments[:, 1])
        if changed:
            self.reset_view()
        key = (id(problem), self.resolution)
        if key in self.cache:
            self.cache.move_to_end(key)
            self.apply_data(self.request, self.cache[key][1])
            return
        self.status.setText("Подготовка сетки и изолиний…")
        self.status.show()
        worker = GridWorker(self.request, problem, self.resolution, self)
        worker.ready.connect(self.apply_data)
        worker.failed.connect(self.show_error)
        worker.finished.connect(self.worker_finished)
        self.workers.append(worker)
        worker.start()

    def reset_view(self) -> None:
        x_range, y_range = view_bounds(self.problem.constraints)
        self.plot.setRange(xRange=x_range, yRange=y_range, padding=0, disableAutoRange=True)

    def clear_contours(self) -> None:
        for item in (*self.contour_items, *self.contour_labels):
            self.plot.removeItem(item)
        self.contour_items.clear()
        self.contour_labels.clear()

    def apply_data(self, request: int, data: PlotData) -> None:
        if request != self.request:
            return
        self.data = data
        key = (id(self.problem), self.resolution)
        self.cache[key] = (self.problem, data)
        while len(self.cache) > 3:
            self.cache.popitem(last=False)
        self.clear_contours()
        for contour in data.contours:
            curve = pg.PlotCurveItem(
                contour.points[:, 0],
                contour.points[:, 1],
                connect="pairs",
                pen=pg.mkPen(self.colors["contours"]),
                antialias=True,
            )
            self.plot.addItem(curve)
            self.contour_items.append(curve)
            label = pg.TextItem(f"{contour.level:.3g}", color=self.colors["contours"])
            label.setPos(*contour.points[len(contour.points) // 2])
            self.plot.addItem(label, ignoreBounds=True)
            self.contour_labels.append(label)
        finite = np.isfinite(data.grid.values)
        if not finite.any():
            self.status.setText("Нет конечных значений функции для построения изолиний.")
        elif not len(feasible_polygon(self.problem.constraints)):
            self.status.setText("Допустимая область пуста.")
        else:
            self.status.clear()
        self.status.setVisible(bool(self.status.text()))
        self.update_layers()

    def show_error(self, request: int, message: str) -> None:
        if request == self.request:
            self.status.setText(f"Ошибка построения графика: {message}")
            self.status.show()

    def worker_finished(self) -> None:
        worker = self.sender()
        if worker in self.workers:
            self.workers.remove(worker)
            worker.deleteLater()
        if not self.busy:
            self.idle.emit()

    def set_result(
        self, result: OptimizationResult | None, checked_points: Sequence[Point] = ()
    ) -> None:
        self.result = result
        self.checked_points = checked_points
        stages = (
            []
            if result is None
            else [
                stage.best_point for stage in result.stage_history if stage.best_point is not None
            ]
        )
        best = sampled_points(stages, self.marker_limit)
        checked = sampled_points(checked_points, self.marker_limit)
        self.best.setData(best[:, 0], best[:, 1])
        self.checked.setData(checked[:, 0], checked[:, 1])
        self.extremum.clear()
        self.annotation.setText("")
        has_extremum = result is not None and result.point is not None and result.value is not None
        if has_extremum:
            x, y = result.point
            self.extremum.setData([x], [y])
            self.annotation.setText(f"({x:.6g}; {y:.6g})\n{self.value_label} = {result.value:.8g}")
            x_range, y_range = view_bounds(self.problem.constraints)
            self.annotation.setAnchor(
                (0 if x < sum(x_range) / 2 else 1, 1 if y < sum(y_range) / 2 else 0)
            )
            self.annotation.setPos(x, y)
        self.layers["extremum"].setEnabled(has_extremum)
        self.layers["best"].setEnabled(bool(stages))
        self.layers["checked"].setEnabled(bool(len(checked_points)))
        self.layers["best"].setToolTip("История этапов не сохранена" if not stages else "")
        self.layers["checked"].setToolTip(
            "Проверенные точки не сохранены" if not len(checked_points) else ""
        )
        self.history_status.setText(
            f"Лучшие точки этапов: {len(best)} / {len(stages)}; "
            f"проверенные точки: {len(checked)} / {len(checked_points)}"
        )
        self.update_layers()

    def update_layers(self) -> None:
        visible = {key: cb.isChecked() and cb.isEnabled() for key, cb in self.layers.items()}
        for item in (*self.contour_items, *self.contour_labels):
            item.setVisible(visible["contours"])
        for key, item in (
            ("feasible", self.fill),
            ("boundaries", self.boundaries),
            ("best", self.best),
            ("checked", self.checked),
            ("extremum", self.extremum),
            ("extremum", self.annotation),
        ):
            item.setVisible(visible[key])

    def mouse_moved(self, event: tuple[QPointF]) -> None:
        position = event[0]
        view_rect = self.plot.getViewBox().sceneBoundingRect()
        if not self.cursor_inside or not view_rect.contains(position):
            self.clear_cursor()
            return
        point = self.plot.getViewBox().mapSceneToView(position)
        x, y = point.x(), point.y()
        value = None if self.data is None else self.data.grid.interpolate(x, y)
        first, second = self.coordinate_labels
        text = f"Курсор: {first} = {x:.4g}; {second} = {y:.4g}; {self.value_label} "
        text += "недоступно" if value is None else f"≈ {value:.6g}"
        self.cursor_label.setText(text)
        self.point_status.setText(
            "Статус точки: "
            + ("допустимая" if self.problem.constraints.contains((x, y)) else "недопустимая")
        )
        self.plot.viewport().setToolTip(text)

    def clear_cursor(self) -> None:
        self.cursor_label.setText("Курсор: —")
        self.point_status.setText("Статус точки: —")
        self.plot.viewport().setToolTip("")

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Enter:
            self.cursor_inside = True
        if event.type() in (QEvent.Type.Leave, QEvent.Type.Hide):
            self.cursor_inside = False
            self.clear_cursor()
        return super().eventFilter(watched, event)

    def set_theme(self, dark: bool) -> None:
        self.colors = {
            "background": "#1c1f24" if dark else "#ffffff",
            "text": "#f0f1f3" if dark else "#20242b",
            "contours": "#8ab4f8" if dark else "#3466a3",
            "feasible": (68, 194, 151, 45) if dark else (15, 137, 98, 35),
            "boundaries": "#63dfb2" if dark else "#00684a",
            "extremum": "#ff9393" if dark else "#b01e32",
            "best": "#d3a0ff" if dark else "#7436a3",
            "checked": "#adb6c4" if dark else "#697484",
        }
        self.plot.setBackground(self.colors["background"])
        muted = "#adb6c4" if dark else "#596575"
        for label in (self.status, self.history_status, self.cursor_label, self.point_status):
            label.setStyleSheet(f"color: {muted};")
        for name in ("bottom", "left"):
            axis = self.plot.getAxis(name)
            axis.setPen(self.colors["text"])
            axis.setTextPen(self.colors["text"])
            axis.setLabel(axis.labelText, color=self.colors["text"])
        self.fill.setPen(pg.mkPen(None))
        self.fill.setBrush(pg.mkBrush(self.colors["feasible"]))
        self.boundaries.setPen(pg.mkPen(self.colors["boundaries"], width=2))
        for key, item in (
            ("extremum", self.extremum),
            ("best", self.best),
            ("checked", self.checked),
        ):
            item.setPen(pg.mkPen(self.colors["background"]))
            item.setBrush(pg.mkBrush(self.colors[key]))
        self.annotation.setColor(self.colors["extremum"])
        for curve in self.contour_items:
            curve.setPen(pg.mkPen(self.colors["contours"]))
        for label in self.contour_labels:
            label.setColor(self.colors["contours"])
