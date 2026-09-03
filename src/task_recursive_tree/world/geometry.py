from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, hypot, pi, sin, sqrt


@dataclass(frozen=True)
class Pose:
    x: float
    y: float
    z: float = 0.0
    yaw: float = 0.0
    frame: str = "world"

    def translated(
        self, dx: float, dy: float, dz: float = 0.0
    ) -> Pose:
        return Pose(
            x=self.x + dx,
            y=self.y + dy,
            z=self.z + dz,
            yaw=self.yaw,
            frame=self.frame,
        )

    def relative_to(self, base: Pose) -> Pose:
        dx = self.x - base.x
        dy = self.y - base.y
        c = cos(-base.yaw)
        s = sin(-base.yaw)
        return Pose(
            x=c * dx - s * dy,
            y=s * dx + c * dy,
            z=self.z - base.z,
            yaw=normalize_angle(self.yaw - base.yaw),
            frame="base",
        )


def distance(a: Pose, b: Pose) -> float:
    return hypot(a.x - b.x, a.y - b.y)


def distance_3d(a: Pose, b: Pose) -> float:
    return sqrt(
        (a.x - b.x) ** 2
        + (a.y - b.y) ** 2
        + (a.z - b.z) ** 2
    )


def heading(a: Pose, b: Pose) -> float:
    return atan2(b.y - a.y, b.x - a.x)


def normalize_angle(value: float) -> float:
    while value > pi:
        value -= 2.0 * pi
    while value < -pi:
        value += 2.0 * pi
    return value


def point_segment_distance(
    px: float,
    py: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
) -> float:
    abx = bx - ax
    aby = by - ay
    denominator = abx * abx + aby * aby
    if denominator == 0.0:
        return hypot(px - ax, py - ay)
    t = ((px - ax) * abx + (py - ay) * aby) / denominator
    t = max(0.0, min(1.0, t))
    closest_x = ax + t * abx
    closest_y = ay + t * aby
    return hypot(px - closest_x, py - closest_y)
