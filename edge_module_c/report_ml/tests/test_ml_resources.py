"""Compile real C lifecycle checks; optionally compare exact output to a Git baseline.

All objects and baseline sources live in a temporary directory. No verification
artifacts are written. Requires gcc; --baseline-ref enables the numeric check.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
BASELINE_REF = None


def run(command):
    result = subprocess.run(command, cwd=REPO, check=True, capture_output=True, text=True)
    return result.stdout


def compile_and_run(directory, source, numeric=False):
    flags = ["gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic",
             "-I", str(source), "-I", str(ROOT / "src")]
    obj = directory / "reference.o"
    injection = [] if numeric else ["-Dcalloc=test_calloc"]
    run(flags + injection + ["-c", str(source / "ml_reference.c"), "-o", str(obj)])
    exe = directory / "resource_test.exe"
    mode = ["-DML_NUMERIC_ONLY"] if numeric else []
    run(flags + mode + [str(ROOT / "tests/test_ml_resources.c"),
                        str(source / "ml_live.c"), str(obj), "-lm", "-o", str(exe)])
    return run([str(exe)])


class ResourceTests(unittest.TestCase):
    def test_resource_lifecycle_and_reasons(self):
        with tempfile.TemporaryDirectory(prefix="edge-ml-resources-") as temp:
            output = compile_and_run(Path(temp), ROOT / "src")
        self.assertIn("resource lifecycle passed", output)
        print(output.strip())

    def test_exact_feature_score_and_live_interpolation_parity(self):
        if BASELINE_REF is None:
            self.skipTest("supply --baseline-ref for pre-change numerical comparison")
        with tempfile.TemporaryDirectory(prefix="edge-ml-parity-") as temp:
            temp = Path(temp)
            baseline = temp / "baseline"
            baseline.mkdir()
            for name in ("ml_reference.c", "ml_reference.h", "ml_live.c", "ml_live.h"):
                content = run(["git", "show", f"{BASELINE_REF}:edge_module_c/report_ml/src/{name}"])
                (baseline / name).write_text(content, encoding="utf-8")
            old = compile_and_run(temp, baseline, numeric=True)
            current = compile_and_run(temp, ROOT / "src", numeric=True)
            self.assertEqual(current, old, "101 features, reference/live scores or labels changed")
            self.assertEqual(len(current.splitlines()), 110)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", help="Git revision containing the pre-change implementation")
    args, remaining = parser.parse_known_args()
    BASELINE_REF = args.baseline_ref
    unittest.main(argv=[__file__] + remaining, verbosity=2)
