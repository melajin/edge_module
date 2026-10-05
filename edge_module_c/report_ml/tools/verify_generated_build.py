"""Compile the checked-in model header and run a deterministic C smoke case."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="edge-alimi-101-model-") as temp:
        executable = Path(temp) / "test_generated_model.exe"
        command = [
            "gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic",
            "-I", str(ROOT / "src"), str(ROOT / "src" / "ml_reference.c"),
            str(ROOT / "tests" / "test_generated_model.c"), "-lm", "-o", str(executable),
        ]
        print("$", subprocess.list2cmdline(command), flush=True)
        subprocess.run(command, check=True)
        subprocess.run([str(executable)], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
