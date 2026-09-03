from __future__ import annotations

from threading import RLock
from types import MappingProxyType
from typing import Mapping, TypeVar, cast

from task_recursive_tree.artifacts.model import Artifact

ArtifactType = TypeVar("ArtifactType", bound=Artifact)


class ArtifactStore:
    """Immutable artifact storage with mutable, scoped symbolic aliases."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._artifacts: dict[str, Artifact] = {}
        self._aliases: dict[tuple[str, str], str] = {}

    def publish(self, scope: str, alias: str, artifact: Artifact) -> str:
        with self._lock:
            if artifact.artifact_id in self._artifacts:
                raise ValueError(
                    f"Artifact id already exists: {artifact.artifact_id}"
                )
            self._artifacts[artifact.artifact_id] = artifact
            self._aliases[(scope, alias)] = artifact.artifact_id
            return artifact.artifact_id

    def get(
        self,
        artifact_ref: str,
        expected_type: type[ArtifactType] | None = None,
    ) -> ArtifactType:
        with self._lock:
            try:
                artifact = self._artifacts[artifact_ref]
            except KeyError as exc:
                raise KeyError(f"Unknown artifact: {artifact_ref}") from exc
        if expected_type is not None and not isinstance(artifact, expected_type):
            raise TypeError(
                f"Artifact {artifact_ref} is {type(artifact).__name__}, "
                f"expected {expected_type.__name__}"
            )
        return cast(ArtifactType, artifact)

    def resolve(
        self,
        scope: str,
        alias: str,
        expected_type: type[ArtifactType] | None = None,
    ) -> ArtifactType:
        with self._lock:
            try:
                artifact_ref = self._aliases[(scope, alias)]
            except KeyError as exc:
                raise KeyError(
                    f"No artifact alias {scope!r}/{alias!r}"
                ) from exc
        return self.get(artifact_ref, expected_type)

    def aliases(self, scope: str) -> Mapping[str, str]:
        with self._lock:
            result = {
                alias: artifact_ref
                for (candidate_scope, alias), artifact_ref in self._aliases.items()
                if candidate_scope == scope
            }
        return MappingProxyType(result)

    def all(self) -> tuple[Artifact, ...]:
        with self._lock:
            return tuple(self._artifacts.values())

