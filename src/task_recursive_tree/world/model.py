from __future__ import annotations

from dataclasses import replace
from threading import RLock

from task_recursive_tree.world.state import (
    EntityState,
    GridMap,
    Observation,
    RobotState,
    WorldSnapshot,
)


class WorldModel:
    """Authoritative observed state used by planners and predicates."""

    def __init__(self, grid: GridMap, initial: Observation) -> None:
        self._grid = grid
        self._lock = RLock()
        self._revision = 0
        self._frame_graph_revision = initial.frame_graph_revision
        self._source_epoch = str(initial.source_epoch or "default")
        self._observation_sequence = int(initial.sequence)
        self._observed_at = initial.observed_at
        self._observation_complete = bool(initial.complete)
        self._observation_confidence = float(initial.confidence)
        self._robot = initial.robot
        self._entities = {
            entity.entity_id: entity for entity in initial.entities
        }
        self._entity_generations = {
            entity.entity_id: entity.generation
            for entity in initial.entities
        }

    def snapshot(self) -> WorldSnapshot:
        with self._lock:
            return WorldSnapshot(
                revision=self._revision,
                frame_graph_revision=self._frame_graph_revision,
                grid=self._grid,
                robot=self._robot,
                entities=self._entities,
                source_epoch=self._source_epoch,
                observation_sequence=self._observation_sequence,
                observed_at=self._observed_at,
                observation_complete=self._observation_complete,
                observation_confidence=self._observation_confidence,
            )

    def ingest(self, observation: Observation) -> WorldSnapshot:
        with self._lock:
            source_epoch = str(observation.source_epoch or "default")
            if source_epoch == self._source_epoch:
                if observation.sequence > 0:
                    if observation.sequence <= self._observation_sequence:
                        raise ValueError(
                            "observation sequence must increase within "
                            "one source epoch"
                        )
                    next_sequence = observation.sequence
                else:
                    next_sequence = self._observation_sequence + 1
            else:
                next_sequence = (
                    observation.sequence
                    if observation.sequence > 0
                    else 1
                )
            robot_changed = (
                observation.robot.base_pose != self._robot.base_pose
                or observation.robot.joints != self._robot.joints
                or observation.robot.gripper_open != self._robot.gripper_open
                or observation.robot.held_object_id
                != self._robot.held_object_id
            )
            next_epoch = self._robot.state_epoch + (1 if robot_changed else 0)
            self._robot = replace(observation.robot, state_epoch=next_epoch)

            next_entities: dict[str, EntityState] = (
                {}
                if observation.complete
                else dict(self._entities)
            )
            for observed in observation.entities:
                previous = self._entities.get(observed.entity_id)
                changed = (
                    previous is None
                    or previous.pose != observed.pose
                    or previous.radius != observed.radius
                    or previous.tags != observed.tags
                    or dict(previous.properties) != dict(observed.properties)
                )
                if previous is None:
                    prior_generation = self._entity_generations.get(
                        observed.entity_id
                    )
                    generation = (
                        0
                        if prior_generation is None
                        else prior_generation + 1
                    )
                    version = 0
                else:
                    generation = previous.generation
                    version = previous.version + (1 if changed else 0)
                next_entities[observed.entity_id] = replace(
                    observed,
                    version=version,
                    generation=generation,
                )
                self._entity_generations[observed.entity_id] = generation

            self._entities = next_entities
            self._frame_graph_revision = observation.frame_graph_revision
            self._source_epoch = source_epoch
            self._observation_sequence = next_sequence
            self._observed_at = observation.observed_at
            self._observation_complete = bool(observation.complete)
            self._observation_confidence = float(observation.confidence)
            self._revision += 1
            return self.snapshot()
