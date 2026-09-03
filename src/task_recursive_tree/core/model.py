from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class PredicateFormula:
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "arguments", MappingProxyType(dict(self.arguments))
        )


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)
    retryable: bool = False
    repairable: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))
