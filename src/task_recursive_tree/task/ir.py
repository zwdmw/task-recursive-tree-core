from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from task_recursive_tree.selection.model import SpatialSelector


@dataclass(frozen=True)
class TaskProgram:
    program_id: str
    action: str
    arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.action != "place":
            raise ValueError(f"Unsupported task action: {self.action!r}")
        required = {"object_selector", "destination_selector"}
        missing = required.difference(self.arguments)
        if missing:
            raise ValueError(f"Missing task arguments: {sorted(missing)}")
        for key in required:
            if not isinstance(self.arguments[key], SpatialSelector):
                raise TypeError(f"{key} must be a SpatialSelector")
        object.__setattr__(
            self, "arguments", MappingProxyType(dict(self.arguments))
        )

    @classmethod
    def place(
        cls,
        object_selector: SpatialSelector,
        destination_selector: SpatialSelector,
        *,
        program_id: str | None = None,
    ) -> TaskProgram:
        return cls(
            program_id=program_id or f"place-{uuid4().hex[:10]}",
            action="place",
            arguments={
                "object_selector": object_selector,
                "destination_selector": destination_selector,
            },
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TaskProgram:
        allowed = {"program_id", "action", "arguments"}
        unknown = set(data).difference(allowed)
        if unknown:
            raise ValueError(f"Unknown TaskProgram fields: {sorted(unknown)}")
        raw_args = dict(data.get("arguments", {}))
        args: dict[str, Any] = {}
        for name in ("object_selector", "destination_selector"):
            value = raw_args.get(name)
            if isinstance(value, SpatialSelector):
                args[name] = value
            elif isinstance(value, Mapping):
                args[name] = SpatialSelector(**value)
            else:
                raise TypeError(f"{name} must be a mapping")
        return cls(
            program_id=str(data.get("program_id") or f"place-{uuid4().hex[:10]}"),
            action=str(data.get("action")),
            arguments=args,
        )

