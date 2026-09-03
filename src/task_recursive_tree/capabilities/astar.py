from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from math import hypot, sqrt
from typing import Iterable

from task_recursive_tree.world.state import GridCell


class PathNotFound(RuntimeError):
    def __init__(
        self,
        start: GridCell,
        goal: GridCell,
        explored: frozenset[GridCell],
    ) -> None:
        super().__init__(f"No grid path from {start} to {goal}")
        self.start = start
        self.goal = goal
        self.explored = explored


@dataclass(frozen=True)
class AStarResult:
    cells: tuple[GridCell, ...]
    cost: float
    expanded_nodes: int


def astar_grid(
    start: GridCell,
    goal: GridCell,
    *,
    occupied: Iterable[GridCell],
    width: int,
    height: int,
    allow_diagonal: bool = True,
) -> AStarResult:
    blocked = set(occupied)
    blocked.discard(start)
    if not _in_bounds(start, width, height):
        raise ValueError(f"Start is outside the grid: {start}")
    if not _in_bounds(goal, width, height):
        raise ValueError(f"Goal is outside the grid: {goal}")
    if goal in blocked:
        raise PathNotFound(start, goal, frozenset())

    frontier: list[tuple[float, float, GridCell]] = []
    heappush(frontier, (_heuristic(start, goal), 0.0, start))
    came_from: dict[GridCell, GridCell | None] = {start: None}
    cost_so_far: dict[GridCell, float] = {start: 0.0}
    explored: set[GridCell] = set()

    while frontier:
        _, current_cost, current = heappop(frontier)
        if current in explored:
            continue
        explored.add(current)
        if current == goal:
            return AStarResult(
                cells=_reconstruct(came_from, goal),
                cost=current_cost,
                expanded_nodes=len(explored),
            )

        for neighbor, step_cost in _neighbors(
            current,
            blocked=blocked,
            width=width,
            height=height,
            allow_diagonal=allow_diagonal,
        ):
            candidate_cost = current_cost + step_cost
            if candidate_cost >= cost_so_far.get(neighbor, float("inf")):
                continue
            cost_so_far[neighbor] = candidate_cost
            came_from[neighbor] = current
            priority = candidate_cost + _heuristic(neighbor, goal)
            heappush(frontier, (priority, candidate_cost, neighbor))

    raise PathNotFound(start, goal, frozenset(explored))


def _neighbors(
    cell: GridCell,
    *,
    blocked: set[GridCell],
    width: int,
    height: int,
    allow_diagonal: bool,
) -> tuple[tuple[GridCell, float], ...]:
    x, y = cell
    cardinal = ((1, 0), (-1, 0), (0, 1), (0, -1))
    diagonal = ((1, 1), (1, -1), (-1, 1), (-1, -1))
    candidates: list[tuple[GridCell, float]] = []
    for dx, dy in cardinal + (diagonal if allow_diagonal else ()):
        neighbor = (x + dx, y + dy)
        if not _in_bounds(neighbor, width, height) or neighbor in blocked:
            continue
        if dx and dy:
            if (x + dx, y) in blocked or (x, y + dy) in blocked:
                continue
            candidates.append((neighbor, sqrt(2.0)))
        else:
            candidates.append((neighbor, 1.0))
    return tuple(candidates)


def _reconstruct(
    came_from: dict[GridCell, GridCell | None], goal: GridCell
) -> tuple[GridCell, ...]:
    path = [goal]
    current = goal
    while came_from[current] is not None:
        current = came_from[current]  # type: ignore[assignment]
        path.append(current)
    path.reverse()
    return tuple(path)


def _in_bounds(cell: GridCell, width: int, height: int) -> bool:
    return 0 <= cell[0] < width and 0 <= cell[1] < height


def _heuristic(a: GridCell, b: GridCell) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])

