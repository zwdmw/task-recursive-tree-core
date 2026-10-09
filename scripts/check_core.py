"""Execute the standalone demos and core tests without an external Harness."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")
    with tempfile.TemporaryDirectory(prefix="trt-core-check-") as folder:
        for name, flags in (("normal", []), ("blocked", ["--blocked"])):
            result = subprocess.run(
                [sys.executable, "-m", "task_recursive_tree", *flags,
                 "--tree-output", str(Path(folder) / f"{name}.json")],
                cwd=ROOT, env=env, capture_output=True, text=True,
                encoding="utf-8", timeout=120,
            )
            if result.returncode or "status=succeeded" not in result.stdout:
                print(result.stdout)
                print(result.stderr, file=sys.stderr)
                return result.returncode or 1
            print(f"{name} demo: succeeded", flush=True)
    tests = sorted(path for path in (ROOT / "tests").glob("test_*.py")
                   if not path.name.startswith("test_gemini_er2_"))
    return subprocess.call(
        [sys.executable, "-m", "pytest", *map(str, tests)],
        cwd=ROOT, env=env,
    )


if __name__ == "__main__":
    raise SystemExit(main())
