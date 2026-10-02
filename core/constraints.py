from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations
from math import hypot, isfinite

from plugin_api import Point


@dataclass(frozen=True)
class PredicateConstraints2D:
    bounds: tuple[Point, Point]
    predicates: tuple[Callable[[float, float], bool], ...]

    def __post_init__(self) -> None:
        LinearConstraints2D(self.bounds)
        if not self.predicates or any(not callable(predicate) for predicate in self.predicates):
            raise ValueError("Нужно задать функции проверки ограничений.")

    @property
    def constraint_types(self) -> tuple[str, ...]:
        return ("bounds", "predicate")

    def contains(self, point: Point) -> bool:
        return (
            len(point) == 2
            and all(isfinite(v) for v in point)
            and all(low <= v <= high for v, (low, high) in zip(point, self.bounds))
            and all(predicate(*point) for predicate in self.predicates)
        )


@dataclass(frozen=True)
class LinearConstraints2D:
    bounds: tuple[Point, Point]
    inequalities: tuple[tuple[float, float, float], ...] = ()

    @property
    def constraint_types(self) -> tuple[str, ...]:
        return ("bounds", "linear_inequality") if self.inequalities else ("bounds",)

    def __post_init__(self) -> None:
        if len(self.bounds) != 2 or any(
            len(bound) != 2 or not all(isfinite(v) for v in bound) or bound[0] > bound[1]
            for bound in self.bounds
        ):
            raise ValueError("Нужны две конечные упорядоченные пары границ.")
        if any(len(row) != 3 or not all(isfinite(v) for v in row) for row in self.inequalities):
            raise ValueError("Линейное ограничение задаётся тройкой (a, b, c): ax + by ≤ c.")

    def contains(self, point: Point) -> bool:
        x, y = point
        return (
            all(isfinite(v) for v in point)
            and all(low <= v <= high for v, (low, high) in zip(point, self.bounds))
            and all(
                a * x + b * y <= c + 1e-12 * max(1.0, abs(c), abs(a * x), abs(b * y))
                for a, b, c in self.inequalities
            )
        )

    def with_bounds(self, bounds: tuple[Point, Point]) -> "LinearConstraints2D":
        narrowed = LinearConstraints2D(bounds, self.inequalities)
        if any(
            low < original_low or high > original_high
            for (low, high), (original_low, original_high) in zip(bounds, self.bounds)
        ):
            raise ValueError("Новые границы должны лежать внутри исходных диапазонов.")
        return narrowed

    def halfplanes(self) -> tuple[tuple[float, float, float], ...]:
        (xmin, xmax), (ymin, ymax) = self.bounds
        return (
            *self.inequalities,
            (-1.0, 0.0, -xmin),
            (1.0, 0.0, xmax),
            (0.0, -1.0, -ymin),
            (0.0, 1.0, ymax),
        )

    def vertices(self) -> list[Point]:
        result: list[Point] = []
        for (a, b, c), (d, e, f) in combinations(self.halfplanes(), 2):
            determinant = a * e - b * d
            if determinant == 0:
                continue
            x, y = (c * e - b * f) / determinant, (a * f - c * d) / determinant
            (xmin, xmax), (ymin, ymax) = self.bounds
            point = (min(xmax, max(xmin, x)), min(ymax, max(ymin, y)))
            if hypot(point[0] - x, point[1] - y) <= 1e-10 and self.contains(point):
                if point not in result:
                    result.append(point)
        return result
