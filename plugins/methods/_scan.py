from collections.abc import Iterator
from math import ceil

from plugin_api import LinearConstraintSet, Point

from ._search import SearchState as SearchState


def axis_values(low: float, high: float, step: float) -> Iterator[float]:
    yield low
    if low == high:
        return
    if low + step == low:
        raise ValueError("Шаг слишком мал для заданного диапазона координат.")
    for index in range(1, ceil((high - low) / step)):
        value = low + index * step
        if value < high:
            yield value
    yield high


def scan_points(constraints: LinearConstraintSet, step: float) -> Iterator[Point]:
    corners = constraints.vertices()
    if not corners:
        return
    yield from corners
    xmin, xmax = min(p[0] for p in corners), max(p[0] for p in corners)
    ymin, ymax = min(p[1] for p in corners), max(p[1] for p in corners)
    for x in axis_values(xmin, xmax, step):
        low, high = ymin, ymax
        for a, b, c in constraints.halfplanes():
            if b > 0:
                high = min(high, (c - a * x) / b)
            elif b < 0:
                low = max(low, (c - a * x) / b)
        if low <= high:
            for y in axis_values(low, high, step):
                if constraints.contains((x, y)):
                    yield x, y
    # Horizontal slices also cover edges nearly parallel to the x axis.
    for y in axis_values(ymin, ymax, step):
        low, high = xmin, xmax
        for a, b, c in constraints.halfplanes():
            if a > 0:
                high = min(high, (c - b * y) / a)
            elif a < 0:
                low = max(low, (c - b * y) / a)
        for point in ((low, y), (high, y)):
            if low <= high and constraints.contains(point):
                yield point
