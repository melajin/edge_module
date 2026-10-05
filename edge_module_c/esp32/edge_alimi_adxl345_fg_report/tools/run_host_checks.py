"""Run the report FG signal tests and 219-update Python/C policy parity."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    print("$", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=PROJECT, check=True)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="edge-alimi-report-v3-") as temp:
        executable = Path(temp) / "test_signal.exe"
        run([
            "gcc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
            "-Isrc", "src/v3_signal.c", "tests/test_signal.c", "-lm", "-o", str(executable),
        ])
        run([str(executable)])
        run([sys.executable, str(PROJECT / "tests" / "test_v3_policy_parity.py")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
