from __future__ import annotations

from math import acos, atan2, cos, pi, sin

from task_recursive_tree.world.state import JointConfiguration


def solve_planar_2link(
    x: float,
    y: float,
    link_lengths: tuple[float, float],
    joint_limits: tuple[tuple[float, float], tuple[float, float]],
) -> tuple[JointConfiguration, ...]:
    link1, link2 = link_lengths
    radius_sq = x * x + y * y
    cosine_q2 = (
        radius_sq - link1 * link1 - link2 * link2
    ) / (2.0 * link1 * link2)
    if cosine_q2 < -1.0 - 1e-9 or cosine_q2 > 1.0 + 1e-9:
        return ()
    cosine_q2 = max(-1.0, min(1.0, cosine_q2))
    candidates: list[JointConfiguration] = []
    for q2 in (acos(cosine_q2), -acos(cosine_q2)):
        q1 = atan2(y, x) - atan2(
            link2 * sin(q2), link1 + link2 * cos(q2)
        )
        q1 = _normalize(q1)
        q2 = _normalize(q2)
        candidate = (q1, q2)
        if all(
            lower <= value <= upper
            for value, (lower, upper) in zip(candidate, joint_limits)
        ):
            candidates.append(candidate)
    return tuple(candidates)


def forward_planar(
    joints: JointConfiguration, link_lengths: tuple[float, float]
) -> tuple[float, float]:
    q1, q2 = joints
    link1, link2 = link_lengths
    return (
        link1 * cos(q1) + link2 * cos(q1 + q2),
        link1 * sin(q1) + link2 * sin(q1 + q2),
    )


def _normalize(value: float) -> float:
    while value > pi:
        value -= 2.0 * pi
    while value < -pi:
        value += 2.0 * pi
    return value

