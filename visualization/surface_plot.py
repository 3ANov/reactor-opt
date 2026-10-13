import os
from collections import OrderedDict

import numpy as np
import pyvista as pv
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from pyvistaqt import QtInteractor

from core.problem import OptimizationProblem
from plugin_api import OptimizationResult
from visualization.surface_data import MAX_RESOLUTION, SurfaceData, compute_surface


class SurfaceWorker(QThread):
    ready = Signal(int, object)
    failed = Signal(int, str)

    def __init__(
        self, request: int, problem: OptimizationProblem, resolution: int, parent: QWidget
    ) -> None:
        super().__init__(parent)
        self.request, self.problem, self.resolution = request, problem, resolution

    def run(self) -> None:
        try:
            data = compute_surface(self.problem, self.resolution, self.isInterruptionRequested)
        except InterruptedError:
            return
        except Exception as error:
            self.failed.emit(self.request, str(error))
        else:
            self.ready.emit(self.request, data)


class SurfacePlot(QWidget):
    idle = Signal()

    def __init__(self, problem: OptimizationProblem, *, resolution: int = 81) -> None:
        super().__init__()
        self.problem = problem
        self.data: SurfaceData | None = None
        self.result: OptimizationResult | None = None
        self.workers: list[SurfaceWorker] = []
        self.request = 0
        self.camera_pending = True
        self.dark = False
        self.axis_labels = ("x", "y")
        self.value_label = "f"
        self.cache: OrderedDict[tuple[int, int], tuple[OptimizationProblem, SurfaceData]] = (
            OrderedDict()
        )
        self.mesh = None
        self.marker_mesh = None
        self.marker_actor = None
        self.boundary_actor = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(["Полная поверхность", "Допустимая поверхность"])
        self.mode.currentIndexChanged.connect(self.draw_surface)
        controls.addWidget(self.mode)
        controls.addWidget(QLabel("Сетка:"))
        self.detail = QSpinBox()
        self.detail.setRange(3, MAX_RESOLUTION)
        self.detail.setValue(resolution)
        self.detail.setKeyboardTracking(False)
        self.detail.setToolTip("Число узлов по каждой оси")
        self.detail.valueChanged.connect(self.load_surface)
        controls.addWidget(self.detail)
        self.scale_bar = QCheckBox("Шкала")
        self.scale_bar.setChecked(True)
        self.scale_bar.toggled.connect(self.update_scale_bar)
        controls.addWidget(self.scale_bar)
        layout.addLayout(controls)
        reset = QPushButton("Исходное положение камеры")
        reset.clicked.connect(self.reset_camera)
        layout.addWidget(reset)
        self.scene = QtInteractor(
            self,
            off_screen=os.environ.get("QT_QPA_PLATFORM") == "offscreen",
            auto_update=False,
            multi_samples=0,
        )
        self.scene.setMinimumHeight(300)
        self.scene.setAcceptDrops(False)
        if self.scene.iren is not None:
            self.scene.enable_trackball_style()
            self.scene.enable_surface_point_picking(
                callback=self.pick_point, show_message=False, show_point=False
            )
        layout.addWidget(self.scene, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.extremum_label = QLabel("Экстремум: —")
        self.extremum_label.setWordWrap(True)
        layout.addWidget(self.extremum_label)
        self.point_label = QLabel(
            "Выбор точки: правая кнопка мыши. "
            "Вращение: левая кнопка; перемещение: Shift + левая кнопка."
        )
        self.point_label.setWordWrap(True)
        layout.addWidget(self.point_label)
        self.set_theme(False)
        self.load_surface()

    @property
    def busy(self) -> bool:
        return bool(self.workers)

    def cancel_pending(self) -> None:
        self.request += 1
        for worker in self.workers:
            worker.requestInterruption()

    def shutdown(self) -> None:
        self.cancel_pending()
        self.scene.close()

    def set_problem(self, problem: OptimizationProblem) -> None:
        if problem is self.problem:
            return
        self.problem = problem
        self.data = None
        self.camera_pending = True
        self.scene.clear()
        self.mesh = self.marker_mesh = self.marker_actor = self.boundary_actor = None
        self.set_result(None)
        self.point_label.setText("Выбор точки: правая кнопка мыши.")
        self.load_surface()

    def set_labels(self, axes: tuple[str, str], value_label: str) -> None:
        self.axis_labels, self.value_label = axes, value_label
        if self.data is not None:
            self.draw_surface()

    def load_surface(self, _value: int = 0) -> None:
        self.cancel_pending()
        key = (id(self.problem), self.detail.value())
        if key in self.cache:
            self.cache.move_to_end(key)
            self.apply_data(self.request, self.cache[key][1])
            return
        self.status.setText("Подготовка 3D-поверхности…")
        worker = SurfaceWorker(self.request, self.problem, self.detail.value(), self)
        worker.ready.connect(self.apply_data)
        worker.failed.connect(self.show_error)
        worker.finished.connect(self.worker_finished)
        self.workers.append(worker)
        worker.start()

    def worker_finished(self) -> None:
        worker = self.sender()
        if worker in self.workers:
            self.workers.remove(worker)
            worker.deleteLater()
        if not self.busy:
            self.idle.emit()

    def show_error(self, request: int, error: str) -> None:
        if request == self.request:
            self.status.setText(f"Не удалось построить поверхность: {error}")

    def apply_data(self, request: int, data: SurfaceData) -> None:
        if request != self.request:
            return
        self.data = data
        self.cache[(id(self.problem), self.detail.value())] = (self.problem, data)
        while len(self.cache) > 3:
            self.cache.popitem(last=False)
        self.draw_surface()

    def draw_surface(self, _index: int = 0) -> None:
        if self.data is None:
            return
        surface = self.data.feasible if self.mode.currentIndex() else self.data.full
        self.scene.remove_actor("surface", reset_camera=False, render=False)
        self.scene.remove_actor("boundary", reset_camera=False, render=False)
        self.mesh = None
        self.boundary_actor = None
        self.scene.remove_bounds_axes()
        if len(surface.triangles):
            faces = np.column_stack((np.full(len(surface.triangles), 3), surface.triangles))
            self.mesh = pv.PolyData(surface.points.copy(), faces.ravel())
            self.mesh["objective"] = surface.points[:, 2]
            self.scene.add_mesh(
                self.mesh,
                scalars="objective",
                cmap="viridis",
                name="surface",
                show_scalar_bar=False,
                reset_camera=False,
                render=False,
            )
            ranges = self.mesh.bounds
            span = max(ranges[1] - ranges[0], ranges[3] - ranges[2], 1e-12)
            height = ranges[5] - ranges[4]
            if self.camera_pending:
                self.scene.set_scale(
                    zscale=span / height if height > 0 else 1, reset_camera=False, render=False
                )
            self.scene.show_bounds(
                xtitle=self.axis_labels[0],
                ytitle=self.axis_labels[1],
                ztitle=self.value_label,
                color=self.text_color,
                font_size=10,
                bold=False,
                axes_ranges=ranges,
            )
            self.status.setText("Поверхность — линейная аппроксимация по сетке.")
        else:
            self.status.setText(
                "Нет конечных значений или допустимой поверхности ненулевой площади."
            )
        if len(self.data.boundary_lines):
            valid = np.unique(self.data.boundary_lines)
            boundary = pv.PolyData(self.data.boundary[valid].copy())
            lookup = np.full(len(self.data.boundary), -1, dtype=int)
            lookup[valid] = np.arange(len(valid))
            boundary.lines = np.column_stack(
                (np.full(len(self.data.boundary_lines), 2), lookup[self.data.boundary_lines])
            ).ravel()
            self.boundary_actor = self.scene.add_mesh(
                boundary,
                color=self.boundary_color,
                line_width=4,
                name="boundary",
                reset_camera=False,
                render=False,
                pickable=False,
            )
        self.update_scale_bar()
        self.set_result(self.result)
        if self.camera_pending:
            self.reset_camera()
            self.camera_pending = False
        self.scene.render()

    @property
    def text_color(self) -> str:
        return "#edf1f7" if self.dark else "#202938"

    @property
    def boundary_color(self) -> str:
        return "#ffb36b" if self.dark else "#a13e00"

    def update_scale_bar(self, _checked: bool = False) -> None:
        for title in list(self.scene.scalar_bars.keys()):
            self.scene.remove_scalar_bar(title, render=False)
        if self.mesh is not None and self.scale_bar.isChecked():
            actor = self.scene.renderer.actors.get("surface")
            self.scene.add_scalar_bar(
                title=self.value_label,
                mapper=actor.mapper,
                color=self.text_color,
                title_font_size=12,
                label_font_size=10,
                render=False,
            )
        self.scene.render()

    def reset_camera(self) -> None:
        self.scene.view_isometric(render=False)
        self.scene.reset_camera(render=False)
        self.scene.render()

    def set_theme(self, dark: bool) -> None:
        self.dark = dark
        self.scene.set_background("#202630" if dark else "#ffffff")
        if self.data is not None:
            self.draw_surface()

    def set_result(self, result: OptimizationResult | None) -> None:
        self.result = result
        self.extremum_label.setText("Экстремум: —")
        if self.marker_actor is not None:
            self.marker_actor.visibility = False
        if result is not None and result.point is not None:
            try:
                value = float(self.problem.objective(*result.point))
                if not np.isfinite(value) or not self.problem.constraints.contains(result.point):
                    raise ValueError
            except (ValueError, ArithmeticError):
                return
            xyz = np.array([[*result.point, value]])
            if self.marker_mesh is None:
                self.marker_mesh = pv.PolyData(xyz)
                self.marker_actor = self.scene.add_mesh(
                    self.marker_mesh,
                    color="#ee2070",
                    point_size=18,
                    render_points_as_spheres=True,
                    name="extremum",
                    lighting=False,
                    reset_camera=False,
                    render=False,
                    pickable=False,
                )
            else:
                self.marker_mesh.points = xyz
            self.marker_actor.visibility = True
            x, y = result.point
            self.extremum_label.setText(
                f"Экстремум: {self.axis_labels[0]} = {x:.8g}; "
                f"{self.axis_labels[1]} = {y:.8g}; "
                f"{self.value_label} = {value:.10g}"
            )
        self.scene.render()

    def pick_point(self, point: np.ndarray) -> None:
        x, y = float(point[0]), float(point[1])
        try:
            value = float(self.problem.objective(x, y))
            if not np.isfinite(value):
                raise ValueError
            self.point_label.setText(
                f"{self.axis_labels[0]} = {x:.6g}; "
                f"{self.axis_labels[1]} = {y:.6g}; "
                f"{self.value_label} = {value:.8g}"
            )
        except (ValueError, ArithmeticError):
            self.point_label.setText("Функция не определена в выбранной точке.")
