"""Independent exact-input replay for the frozen Rotor Kit 1x classifier."""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
import re
import zipfile

import numpy as np
from scipy.signal import resample_poly

from .trace_replay import sha256

DEFAULT_ZIP = Path("F:/rotorkit_dataset_original.zip")
DEFAULT_RESULT = Path("F:/aihub_training_runs/rotorkit_imbalance_1x/result.json")
RESULT_SHA256 = "abadeabe298980a1e59015df33355b998875e2c87e8b90532527772938b0920a"
CONDITIONS = ("A__D", "AW__D", "A__DW", "Avv__D", "A__Dvv", "Avv__Dvv")
NAME_RE = re.compile(r"MA342_1200rpm_(.+)_(\d\d)\.csv$")


def synchronous_features(x: np.ndarray, fs: float = 1000.0, freq: float = 20.0,
                         window: int = 1024) -> tuple[np.ndarray, np.ndarray]:
    """Windowed 1x amplitude/phase; phase is corrected to file absolute time."""
    count = len(x) // window
    x = x[:count * window].reshape(count, window, 3)
    x = x - x.mean(axis=1, keepdims=True)
    starts = np.arange(count) * window
    local = np.arange(window)
    carrier = np.exp(-2j * np.pi * freq * local / fs)
    coeff = (2.0 / window) * np.einsum("nsa,s->na", x, carrier)
    coeff *= np.exp(-2j * np.pi * freq * starts / fs)[:, None]
    return np.abs(coeff), np.angle(coeff)


def confirmed(amplitude_ratio: np.ndarray, phase: np.ndarray, k: float, rmin: float) -> np.ndarray:
    out = np.zeros_like(amplitude_ratio, dtype=bool)
    for i in range(4, len(out)):
        sl = slice(i - 4, i + 1)
        votes = (amplitude_ratio[sl] > k).sum(axis=0) >= 4
        concentration = np.abs(np.mean(np.exp(1j * phase[sl]), axis=0))
        out[i] = votes & (concentration >= rmin)
    return out


def confusion(records: list[dict], k: float, rmin: float) -> dict:
    file = dict(tn=0, fp=0, fn=0, tp=0)
    window = dict(tn=0, fp=0, fn=0, tp=0)
    evaluable = total = 0
    for record in records:
        prediction = confirmed(record["ratio"], record["phase"], k, rmin)
        valid = np.arange(len(prediction)) >= 4
        truth = record["condition"] != "A__D"
        file_positive = bool(prediction[valid].any())
        file["tp" if truth and file_positive else "fn" if truth else "fp" if file_positive else "tn"] += 1
        for value in prediction[valid].any(axis=1):
            window["tp" if truth and value else "fn" if truth else "fp" if value else "tn"] += 1
        evaluable += int(valid.sum())
        total += len(prediction)
    tn, fp, fn, tp = (file[key] for key in ("tn", "fp", "fn", "tp"))
    normal_total, fault_total = tn + fp, fn + tp
    return {"file_confusion": file, "window_confusion": window,
            "normal_file_fpr": fp / normal_total if normal_total else None,
            "imbalance_file_recall": tp / fault_total if fault_total else None,
            "balanced_accuracy": ((tn / normal_total) + (tp / fault_total)) / 2 if normal_total and fault_total else None,
            "coverage": evaluable / total if total else 0.0,
            "evaluable_windows": evaluable, "total_windows": total}


def replay(zip_path: Path = DEFAULT_ZIP, result_path: Path = DEFAULT_RESULT) -> dict:
    if sha256(result_path) != RESULT_SHA256:
        raise ValueError("pinned Rotor Kit result SHA-256 mismatch")
    reference = json.loads(result_path.read_text(encoding="utf-8"))
    if sha256(zip_path) != reference["input"]["zip_sha256"]:
        raise ValueError("Rotor Kit source ZIP SHA-256 mismatch")
    records = []
    baselines = []
    actual_member_hashes = {}
    with zipfile.ZipFile(zip_path) as archive:
        selected = []
        for name in archive.namelist():
            match = NAME_RE.search(name)
            if match and match.group(1) in CONDITIONS and int(match.group(2)) <= 15:
                selected.append((name, match.group(1), int(match.group(2))))
        if len(selected) != 96:
            raise ValueError(f"expected 96 Rotor Kit ZIP members, got {len(selected)}")
        for name, condition, number in sorted(selected):
            raw = archive.read(name)
            actual_member_hashes[name] = hashlib.sha256(raw).hexdigest()
            expected_hash = reference["input"]["member_sha256"].get(name)
            if expected_hash != actual_member_hashes[name]:
                raise ValueError(f"Rotor Kit member SHA-256 mismatch: {name}")
            source = np.loadtxt(io.BytesIO(raw), delimiter=",", dtype=np.float64)
            if source.ndim != 2 or source.shape[1] != 3:
                raise ValueError(f"expected 3-axis acceleration input: {name}")
            signal = resample_poly(source, 1, 3, axis=0)
            amplitude, phase = synchronous_features(signal)
            role = "baseline" if number <= 7 else "validation" if number <= 11 else "locked_test"
            if condition == "A__D" and role == "baseline":
                baselines.append(amplitude)
            records.append({"member": name, "condition": condition, "number": number,
                            "role": role, "amplitude": amplitude, "phase": phase})
    baseline = np.median(np.concatenate(baselines), axis=0)
    for record in records:
        record["ratio"] = record["amplitude"] / baseline
    selected_rule = reference.get("selected")
    if not selected_rule or reference.get("status") != "locked_test_run_once":
        raise ValueError("pinned Rotor Kit result has no fixed selected rule")
    params = (float(selected_rule["k"]), float(selected_rule["phase_r"]))
    locked = confusion([r for r in records if r["role"] == "locked_test"], *params)
    expected = reference["locked_test"]
    for key in ("file_confusion", "window_confusion", "evaluable_windows", "total_windows"):
        if locked[key] != expected[key]:
            raise AssertionError(f"Rotor Kit locked {key} mismatch: {locked[key]} != {expected[key]}")
    for key in ("normal_file_fpr", "imbalance_file_recall", "balanced_accuracy", "coverage"):
        if not math.isclose(locked[key], expected[key], rel_tol=0, abs_tol=1e-12):
            raise AssertionError(f"Rotor Kit locked {key} mismatch")
    return {"scope": "software_replay_of_locked_test_not_hardware_validation",
            "input_zip": str(zip_path), "input_zip_sha256": sha256(zip_path),
            "reference_result": str(result_path), "reference_result_sha256": sha256(result_path),
            "verified_members": len(actual_member_hashes),
            "window_contract": {"source_hz": 3000, "target_hz": 1000, "window_samples": 1024,
                                "overlap": 0, "synchronous_frequency_hz": 20.0,
                                "baseline": "A__D members 00-07 axis-wise median 1x amplitude",
                                "rule": "4/5 amplitude_ratio_1x > selected k and 5-phase concentration >= selected r"},
            "fixed_selected_rule": {"k": params[0], "phase_concentration_threshold": params[1],
                                    "source": "pinned pre-existing result.selected; not retuned"},
            "locked_test": locked,
            "interpretation": "The locked result remains weak: 4/20 fault files detected, 20% file recall; the selected validation metrics are separate."}
