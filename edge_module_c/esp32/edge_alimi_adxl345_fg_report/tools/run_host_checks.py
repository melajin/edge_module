"""Run the report FG signal tests and 219-update Python/C policy parity."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import json
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
        profile_executable = Path(temp) / "test_profiles.exe"
        run([
            "g++", "-std=c++11", "-Wall", "-Wextra", "-Werror", "-pedantic",
            "-Itests/stubs", "-Isrc", "src/acquisition_quality.cpp", "src/profile_manager.cpp",
            "src/v3_signal.c", "src/em_v3.c", "tests/test_profiles.cpp",
            "-o", str(profile_executable),
        ])
        profile_output = subprocess.check_output([str(profile_executable)], cwd=PROJECT, text=True)
        profile_lines = profile_output.splitlines()
        detail = json.loads(profile_lines[0])
        assert detail["event"] == "profile" and detail["v"] == 1
        assert detail["profile"]["baseline_ready"]
        assert [detail["profile"][slot]["id"] for slot in ("current", "candidate", "previous")] == [3, 4, 1]
        assert detail["profile"]["candidate"]["seq"] == 7
        print(profile_lines[1])
        acquisition_executable = Path(temp) / "test_acquisition.exe"
        run([
            "g++", "-std=c++11", "-Wall", "-Wextra", "-Werror", "-pedantic",
            "-Itests/stubs", "-Isrc", "src/acquisition_quality.cpp",
            "src/profile_manager.cpp",
            "src/v3_signal.c", "src/em_v3.c", "tests/test_acquisition.cpp",
            "-o", str(acquisition_executable),
        ])
        output = subprocess.check_output([str(acquisition_executable)], cwd=PROJECT, text=True)
        rows = [json.loads(line) for line in output.splitlines()]
        assert len(rows) == 6
        assert [r["acquisition"]["seq"] for r in rows[:5]] == list(range(5))
        assert rows[0]["acquisition"]["valid"] and rows[0]["fs_hz"] is None
        assert rows[0]["acquisition"]["gap_us"] is None
        assert rows[1]["acquisition"]["gap_us"] > 2**32
        assert rows[2]["acquisition"]["reason"] == "partial_window"
        assert rows[3]["reason"] == "adxl_init"
        assert rows[3]["acquisition"]["start_us"] is None
        assert rows[3]["acquisition"]["end_us"] is None
        assert rows[3]["acquisition"]["gap_us"] is None
        assert rows[4]["acquisition"]["valid"]
        for row in rows:
            runtime = row["runtime"]
            assert runtime["v"] == 1
            assert runtime["pre_emit_us"] >= 0
            assert runtime["heap"]["largest_free_bytes"] <= runtime["heap"]["free_bytes"]
            assert runtime["heap"]["min_free_bytes"] <= runtime["heap"]["free_bytes"]
            previous = runtime["previous_emit"]
            if row["acquisition"]["seq"] == 0:
                assert previous is None
            else:
                assert previous["seq"] == row["acquisition"]["seq"] - 1
                assert previous["call_us"] > 0
        assert rows[0]["runtime"]["pre_emit_us"] == rows[1]["runtime"]["pre_emit_us"] == rows[4]["runtime"]["pre_emit_us"] == 1282500
        assert rows[2]["runtime"]["pre_emit_us"] == 12500
        assert rows[3]["runtime"]["pre_emit_us"] == 0
        sys.path.insert(0, str(PROJECT.parents[1] / "pi"))
        from report_serial_logger import validate_record
        for row in rows[:5]:
            assert validate_record(row) == [], validate_record(row)
        longest = max(len(line.encode()) + 1 for line in output.splitlines())
        assert longest < 1152, "conservative complete JSON exceeds 100 ms 8N1 budget"
        assert longest < 2048, "conservative complete JSON exceeds the TX queue"
        assert longest * 10 / 115200 < 0.100, "JSON alone exhausts the 100 ms gap budget"
        print(f"Acquisition and main.cpp JSON checks passed; conservative frame {longest} bytes, "
              f"115200 8N1 wire time {longest * 10 / 115200 * 1000:.2f} ms")
        run([sys.executable, str(PROJECT / "tests" / "test_v3_policy_parity.py")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
