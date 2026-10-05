"""Verify the checked-in 101-feature model and optionally replay its source folds."""
from __future__ import annotations

import argparse
import ctypes as C
import hashlib
import gc
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import joblib
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parents[1]
MODEL_PACKAGE = REPOSITORY_ROOT / "ml"
sys.path.insert(0, str(MODEL_PACKAGE))
from report_model.a_features import extract_a, restrict  # noqa: E402
from report_model.a_sources import selected_records  # noqa: E402

PTR = C.POINTER(C.c_double)


def compile_model(directory: Path):
    library_path = directory / ("ml_reference.dll" if sys.platform == "win32" else "ml_reference.so")
    command = [
        "gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic", "-shared",
        "-I", str(ROOT / "src"), "-o", str(library_path),
        str(ROOT / "src" / "ml_reference.c"), "-lm",
    ]
    print("$", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, check=True)
    lib = C.CDLL(str(library_path))
    lib.ml_reference_init.argtypes = []
    lib.ml_reference_init.restype = C.c_int
    lib.ml_reference_extract.argtypes = [PTR, C.c_double, PTR]
    lib.ml_reference_extract.restype = C.c_int
    lib.ml_reference_predict.argtypes = [PTR, PTR]
    lib.ml_reference_predict.restype = C.c_int
    if not lib.ml_reference_init():
        raise RuntimeError("C model workspace initialization failed")
    return lib


def release_windows_dll(lib) -> None:
    if sys.platform == "win32":
        handle = lib._handle
        lib._handle = 0
        free_library = C.windll.kernel32.FreeLibrary
        free_library.argtypes = [C.c_void_p]
        free_library.restype = C.c_int
        if not free_library(C.c_void_p(handle)):
            raise OSError("Could not unload the C model verification library")
    del lib
    gc.collect()


def generated_header_smoke() -> dict:
    with tempfile.TemporaryDirectory(prefix="edge-report-ml-generated-") as temp:
        lib = compile_model(Path(temp))
        samples = np.ascontiguousarray(
            np.sin(2 * np.pi * 20 * np.arange(400) / 400)
            + 0.3 * np.sin(2 * np.pi * 40 * np.arange(400) / 400), dtype=np.float64)
        features = np.zeros(101, dtype=np.float64)
        scores = np.zeros(4, dtype=np.float64)
        assert lib.ml_reference_extract(samples.ctypes.data_as(PTR), 1238., features.ctypes.data_as(PTR))
        prediction = lib.ml_reference_predict(features.ctypes.data_as(PTR), scores.ctypes.data_as(PTR))
        assert prediction in range(4) and np.isfinite(features).all() and np.isfinite(scores).all()
        zeros = np.zeros(400, dtype=np.float64)
        assert not lib.ml_reference_extract(zeros.ctypes.data_as(PTR), 1238., features.ctypes.data_as(PTR))
        release_windows_dll(lib)
    return {"generated_header": "passed", "features": 101, "classes": 4,
            "constant_signal_rejected": True, "input": "synthetic 400-point signal"}


def full_parity(source: Path, model_dir: Path) -> dict:
    lib = None
    with tempfile.TemporaryDirectory(prefix="edge-report-ml-parity-") as temp:
        try:
            lib = compile_model(Path(temp))
            contract = json.loads((model_dir / "feature_contract.json").read_text(encoding="utf-8"))
            names = validate_model_contract(model_dir, contract)
            models = [joblib.load(model_dir / f"fold{k}.joblib") for k in range(5)]
            manifest = json.loads((source / "source_manifest.json").read_text(encoding="utf-8"))
            waves = []
            for trial in manifest["trials"]:
                if trial["trial"] in (1, 2, 4, 5):
                    records = selected_records(source, trial, allow_lock=True)
                    waves.extend((f"trial{trial['trial']}/{record['name']}", wave[2])
                                 for record, wave in records[:4])
            rng = np.random.default_rng(20261002)
            t = np.arange(25000) / 25000
            waves.extend((f"synthetic{k}", rng.normal(0, .1, 25000)
                          + np.sin(2 * np.pi * (20 + k) * t)
                          + .3 * np.sin(2 * np.pi * (40 + k) * t)) for k in range(10))
            details = []
            worst = worst_score = 0.0
            for label, raw in waves:
                expected = np.array([extract_a(raw, "limited400")[name] for name in names])
                wave = np.ascontiguousarray(restrict(raw, "limited400")[0], dtype=np.float64)
                got, scores = np.zeros(101), np.zeros(4)
                started = time.perf_counter()
                if not lib.ml_reference_extract(wave.ctypes.data_as(PTR), 1238., got.ctypes.data_as(PTR)):
                    raise AssertionError(f"C feature extraction failed for {label}")
                elapsed = time.perf_counter() - started
                pred = lib.ml_reference_predict(got.ctypes.data_as(PTR), scores.ctypes.data_as(PTR))
                expected_scores = np.mean([m.decision_function(expected.reshape(1, -1))[0] for m in models], axis=0)
                error = float(np.max(np.abs(got - expected)))
                score_error = float(np.max(np.abs(scores - expected_scores)))
                np.testing.assert_allclose(got, expected, rtol=2e-7, atol=2e-7)
                np.testing.assert_allclose(scores, expected_scores, rtol=2e-7, atol=2e-7)
                assert pred == int(np.argmax(expected_scores))
                worst, worst_score = max(worst, error), max(worst_score, score_error)
                details.append({"input": label, "feature_max_abs_error": error,
                                "margin_max_abs_error": score_error,
                                "label": list(models[0].classes_)[pred], "host_seconds": elapsed})
            zeros, features = np.zeros(400), np.zeros(101)
            assert not lib.ml_reference_extract(zeros.ctypes.data_as(PTR), 1238., features.ctypes.data_as(PTR))
            zeros[0] = np.nan
            assert not lib.ml_reference_extract(zeros.ctypes.data_as(PTR), 1238., features.ctypes.data_as(PTR))
            result = {"cases": len(details), "feature_max_abs_error": worst,
                      "margin_max_abs_error": worst_score, "zero_and_nan_rejected": True,
                      "hardware_validation": False, "source_dir": str(source),
                      "model_dir": str(model_dir), "folds": 5, "feature_count": 101,
                      "case_details": details}
        finally:
            if lib is not None:
                release_windows_dll(lib)
    return result


def validate_model_contract(model_dir: Path, contract: dict) -> list[str]:
    reference_path = ROOT / "verification/source_candidate/feature_contract.json"
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    expected_identity = ("limited400", "single_ch2", "extended")
    identity = (contract.get("profile"), contract.get("input"), contract.get("family"))
    if identity != expected_identity or identity != (
            reference.get("profile"), reference.get("input"), reference.get("family")):
        raise ValueError(f"Candidate contract {identity} does not match report profile {expected_identity}")
    names = contract.get("features", [])
    if names != reference.get("features") or len(names) != 101:
        raise ValueError("Candidate feature names/order differ from the report's 101-feature contract")
    fit_provenance = json.loads((ROOT / "verification/source_candidate/fit_provenance.json").read_text(encoding="utf-8"))
    expected_hashes = {f"fold{fold}.joblib": record["model_sha256"] for fold, record in enumerate(fit_provenance)}
    for filename, expected in expected_hashes.items():
        actual = hashlib.sha256((model_dir / filename).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"{filename} does not match the report's selected candidate fold")
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-parity", action="store_true",
                        help="compare C against the five source folds using explicit external inputs")
    parser.add_argument("--source-dir", type=Path, help="local extracted A-source bundle directory")
    parser.add_argument("--model-dir", type=Path, help="candidate directory containing five fold joblib files")
    parser.add_argument("--result", type=Path, help="optional path for JSON output")
    args = parser.parse_args()
    if args.full_parity:
        if not args.source_dir or not args.model_dir:
            parser.error("--full-parity requires both --source-dir and --model-dir")
        result = full_parity(args.source_dir.resolve(), args.model_dir.resolve())
    else:
        result = generated_header_smoke()
    if args.result:
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "case_details"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
