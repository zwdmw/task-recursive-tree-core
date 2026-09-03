from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import ModuleType


HARNESS_ROOT_ENV = "GEMINI_ER2_HARNESS_ROOT"
DEFAULT_HARNESS_ROOT = Path(r"D:\GeminiER2Harness")


class HarnessPathError(RuntimeError):
    pass


def resolve_harness_root(value: str | Path | None = None) -> Path:
    configured = value or os.environ.get(HARNESS_ROOT_ENV) or DEFAULT_HARNESS_ROOT
    root = Path(configured).expanduser().resolve()
    marker = root / "er2sim" / "task_compiler.py"
    if not marker.is_file():
        raise HarnessPathError(
            f"GeminiER2Harness root is invalid: {root} "
            f"(missing {marker.relative_to(root)})"
        )
    return root


def ensure_harness_importable(value: str | Path | None = None) -> Path:
    root = resolve_harness_root(value)
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    return root


def import_harness_module(
    module_name: str,
    *,
    harness_root: str | Path | None = None,
) -> ModuleType:
    ensure_harness_importable(harness_root)
    return importlib.import_module(module_name)
