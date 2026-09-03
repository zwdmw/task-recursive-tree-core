from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .program_normalization import (
    PROGRAM_NORMALIZATION_MODEL_VERSION,
    ProgramNormalizationResult,
    normalize_model_task_program,
)


class HarnessTaskProgramPlannerAdapter:
    """Canonicalize Harness planner output before session artifacts are saved."""

    def __init__(self, planner: Any) -> None:
        self._planner = planner
        self._last_normalization: ProgramNormalizationResult | None = None

    def parse_task_program(self, *args: Any, **kwargs: Any) -> Any:
        self._last_normalization = None
        program = self._planner.parse_task_program(*args, **kwargs)
        result = normalize_model_task_program(program)
        self._last_normalization = result
        return result.program

    @property
    def parse_report(self) -> Any:
        report = getattr(self._planner, "parse_report", None)
        if not isinstance(report, Mapping):
            return report
        normalized = copy.deepcopy(dict(report))
        result = self._last_normalization
        if result is not None and result.changed_paths:
            normalized["task_recursive_tree_normalization"] = {
                "model_version": PROGRAM_NORMALIZATION_MODEL_VERSION,
                "paths": list(result.changed_paths),
                "applied": result.applied,
            }
        return normalized

    @property
    def last_normalization(self) -> ProgramNormalizationResult | None:
        return self._last_normalization

    @property
    def wrapped(self) -> Any:
        return self._planner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._planner, name)


__all__ = ["HarnessTaskProgramPlannerAdapter"]
