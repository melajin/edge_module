"""Run the frozen physical-policy C tests without touching saved report results."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    print("$", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="edge-report-physical-") as directory:
        temp = Path(directory)
        c_exe = temp / "physical_c_tests.exe"
        obj = temp / "fault_evidence.o"
        cpp_exe = temp / "physical_cpp_tests.exe"
        cpp_source_exe = temp / "physical_cpp_source_tests.exe"
        run(["gcc", "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic",
             "-Iinclude", "src/fault_evidence.c", "tests/test_fault_evidence.c", "-lm",
             "-o", str(c_exe)])
        run([str(c_exe), str(temp / "c_results.json")])
        run(["gcc", "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic",
             "-Iinclude", "-c", "src/fault_evidence.c", "-o", str(obj)])
        run(["g++", "-std=c++11", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic",
             "-Iinclude", "tests/test_cpp_header.cpp", str(obj), "-lm", "-o", str(cpp_exe)])
        run([str(cpp_exe)])
        run(["g++", "-std=c++11", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic",
             "-Iinclude", "-x", "c++", "src/fault_evidence.c", "tests/test_fault_evidence.c",
             "-lm", "-o", str(cpp_source_exe)])
        run([str(cpp_source_exe), str(temp / "cpp_results.json")])
    print("PASS: report physical C/C++ host synthetic tests; no hardware measurements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
