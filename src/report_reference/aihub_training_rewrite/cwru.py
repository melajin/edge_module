"""Independent raw-signal replay of the frozen CWRU bearing locked test."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import joblib
import numpy as np
from scipy.fft import fft, fftfreq
from scipy.io import loadmat
from scipy.stats import kurtosis

from .trace_replay import sha256


DEFAULT_MANIFEST = Path("F:/aihub_training_runs/mock_exam_sets/manifest.json")
DEFAULT_TRAINING = Path("F:/aihub_training_runs/mock_exam_training/cwru")
DEFAULT_RESULT = Path("F:/aihub_training_runs/bearing_continuous_simulation/result.json")
RESULT_SHA256 = "c6065ebeee45e9bfd0dda31cb7e8d8d0c665a0df7332facb29e82c8cf80b43a8"
ACTIVE_MANIFEST_SHA256 = "7ba1a301bacfcdac0f85c28fde65fde248d24199200ca59c8b2560e237fc7280"
CWRU_MANIFEST_SHA256 = "d1f70a9f685adc5785c678ce6fb75afac25b24c53056f985dc6c1af7d8e9f2ed"
SELECTION_SHA256 = "ed38833d68b47822ab6f47b95a9aa13b40f22a2fb9c089b28ebd69ead6889b56"
MODEL_SHA256 = "6a4bfb1d803bde044dbe28080c452c256047c736a1c984c38781c850c4849ee5"
FAULT_CLASSES = {"ball", "inner_race", "outer_race"}
FEATURE_NAMES = ("rms", "kurtosis", "harmonic1_ratio", "harmonic2_ratio", "harmonic3_ratio", "high_freq_ratio")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_hash(path: Path, expected: str, label: str) -> None:
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"{label} SHA-256 mismatch: {actual} != {expected}")


def _record_feature(signal: np.ndarray, sample_rate_hz: float, rated_rpm: float) -> list[float]:
    """Reimplement ``extract_features_real`` for one CWRU sample window."""
    centered = np.asarray(signal, dtype=np.float64) - float(np.mean(signal))
    n = len(centered)
    if n != 4096:
        raise ValueError(f"CWRU feature input must be 4096 samples, got {n}")
    rms = float(np.sqrt(np.mean(centered ** 2)))
    kurt = float(kurtosis(centered))
    spectrum = fft(centered)
    frequencies = fftfreq(n, d=1.0 / float(sample_rate_hz))[:n // 2]
    magnitudes = np.abs(spectrum[:n // 2]) * 2.0 / n
    nominal_hz = float(rated_rpm) / 60.0
    lo, hi = nominal_hz * 0.8, nominal_hz * 1.2
    selected = (frequencies >= lo) & (frequencies <= hi)
    rotation_hz = float(frequencies[selected][np.argmax(magnitudes[selected])]) if selected.any() else nominal_hz
    bandwidth = max(5.0, 2.0 * float(sample_rate_hz) / n)

    def harmonic(center: float) -> float:
        mask = (frequencies >= center - bandwidth) & (frequencies <= center + bandwidth)
        return float(np.sum(magnitudes[mask]))

    h1, h2, h3 = (harmonic(rotation_hz * order) for order in (1, 2, 3))
    high = float(np.sum(magnitudes[(frequencies >= rotation_hz * 10.0)
                                   & (frequencies <= float(sample_rate_hz) / 2.0 * 0.8)]))
    total = float(np.sum(magnitudes[frequencies > 1.0])) + 1.0e-12
    return [rms, kurt, h1 / total, h2 / total, h3 / total, high / total]


def confirm_four_of_five(instant_fault: list[bool]) -> tuple[list[bool], int]:
    """File-local rolling 4/5; the first four windows are not scored."""
    history: list[bool] = []
    confirmed: list[bool] = []
    for value in instant_fault:
        history.append(bool(value))
        if len(history) > 5:
            history.pop(0)
        if len(history) == 5:
            confirmed.append(sum(history) >= 4)
    return confirmed, min(4, len(instant_fault))


def _metrics(truth: list[bool], predicted: list[bool]) -> dict:
    if len(truth) != len(predicted):
        raise ValueError("truth/prediction length differs")
    tn = sum(not y and not p for y, p in zip(truth, predicted))
    fp = sum(not y and p for y, p in zip(truth, predicted))
    fn = sum(y and not p for y, p in zip(truth, predicted))
    tp = sum(y and p for y, p in zip(truth, predicted))
    ratio = lambda numerator, denominator: numerator / denominator if denominator else None
    specificity = ratio(tn, tn + fp)
    recall = ratio(tp, tp + fn)
    return {"n": len(truth), "tn": tn, "fp": fp, "fn": fn, "tp": tp,
            "accuracy": ratio(tn + tp, len(truth)), "specificity": specificity,
            "bearing_recall": recall,
            "balanced_accuracy": (specificity + recall) / 2 if specificity is not None and recall is not None else None}


def replay(manifest_path: Path = DEFAULT_MANIFEST, training_path: Path = DEFAULT_TRAINING,
           result_path: Path = DEFAULT_RESULT) -> dict:
    """Recompute locked CWRU windows from pinned MAT files and frozen classifier."""
    manifest_path, training_path, result_path = map(Path, (manifest_path, training_path, result_path))
    _check_hash(manifest_path, ACTIVE_MANIFEST_SHA256, "active manifest")
    _check_hash(result_path, RESULT_SHA256, "CWRU result")
    result_reference = _read_json(result_path)
    active = _read_json(manifest_path)
    entry = next((item for item in active["subjects"] if item["subject"] == "cwru"), None)
    if entry is None:
        raise ValueError("CWRU subject absent from active manifest")
    cwru_manifest_path = manifest_path.parent / entry["manifest"]
    if sha256(cwru_manifest_path) != entry["sha256"] or entry["sha256"] != CWRU_MANIFEST_SHA256:
        raise ValueError("CWRU subject manifest SHA-256 mismatch")
    _check_hash(cwru_manifest_path, CWRU_MANIFEST_SHA256, "CWRU subject manifest")
    subject = _read_json(cwru_manifest_path)

    selection_path = training_path / "selection.json"
    model_path = training_path / "model.joblib"
    _check_hash(selection_path, SELECTION_SHA256, "selected model manifest")
    _check_hash(model_path, MODEL_SHA256, "selected model")
    selection = _read_json(selection_path)
    payload = joblib.load(model_path)
    if selection.get("selected_model") != "scaled_balanced_logistic" or payload.get("test_used_for_selection") is not False:
        raise ValueError("unexpected selected-model contract")
    classes = payload.get("classes")
    if classes != ["ball", "inner_race", "normal", "outer_race"]:
        raise ValueError("selected model class order changed")
    model = payload["model"]

    records = [record for record in subject["records"] if record["role"] == "locked_test"]
    expected_order = result_reference["inputs"]["locked_test_file_order"]
    if [record["id"] for record in records] != expected_order or len(records) != 10:
        raise ValueError("locked CWRU record order differs from pinned result")

    file_rows: list[dict] = []
    all_truth: list[bool] = []
    all_predictions: list[bool] = []
    source_hashes: dict[str, str] = {}
    for record in records:
        source = Path(record["source"])
        _check_hash(source, record["source_sha256"], f"source MAT {record['id']}")
        source_hashes[record["id"]] = record["source_sha256"]
        raw = np.asarray(loadmat(source, variable_names=[record["mat_channel"]])[record["mat_channel"]]).ravel()
        content_hash = hashlib.sha256(raw.astype("<f8").tobytes()).hexdigest()
        if content_hash != record["content_sha256"]:
            raise ValueError(f"CWRU selected channel content SHA-256 mismatch: {record['id']}")
        if len(raw) != record["row_stop"] or not np.isfinite(raw).all():
            raise ValueError(f"CWRU source length/nonfinite mismatch: {record['id']}")
        size, stride = int(record["window_size"]), int(record["window_stride"])
        if size != 4096 or stride != 2048:
            raise ValueError(f"unexpected CWRU window contract in {record['id']}")
        matrix = np.asarray([
            _record_feature(raw[start:start + size], record["sample_rate_hz"], record["source_metadata"][2])
            for start in range(0, len(raw) - size + 1, stride)
        ], dtype=np.float64)
        predicted_indices = model.predict(matrix).tolist()
        instant = [classes[int(index)] != "normal" for index in predicted_indices]
        confirmed, buffer_count = confirm_four_of_five(instant)
        truth_value = record["label"] in FAULT_CLASSES
        truth = [truth_value] * len(confirmed)
        metrics = _metrics(truth, confirmed)
        file_rows.append({"id": record["id"], "label": record["label"],
                          "total_windows": len(instant), "confirmation_buffer_windows": buffer_count,
                          "evaluated_windows": len(confirmed), "instant_fault_windows": sum(instant),
                          "confirmed_fault_windows": sum(confirmed), "metrics": metrics})
        all_truth.extend(truth)
        all_predictions.extend(confirmed)

    overall = _metrics(all_truth, all_predictions)
    expected_cwru = result_reference["cwru"]
    if overall != expected_cwru["overall"]:
        raise AssertionError(f"CWRU overall result mismatch: {overall} != {expected_cwru['overall']}")
    if sum(row["total_windows"] for row in file_rows) != expected_cwru["total_source_windows"]:
        raise AssertionError("CWRU source window count mismatch")
    if sum(row["confirmation_buffer_windows"] for row in file_rows) != expected_cwru["confirmation_buffer_windows"]:
        raise AssertionError("CWRU confirmation buffer count mismatch")
    expected_files = expected_cwru["files"]
    for actual, expected in zip(file_rows, expected_files):
        for key in ("id", "label", "total_windows", "confirmation_buffer_windows", "evaluated_windows",
                    "instant_fault_windows", "confirmed_fault_windows", "metrics"):
            if actual[key] != expected[key]:
                raise AssertionError(f"CWRU file {actual['id']} {key} mismatch")

    return {
        "scope": "raw_signal_software_replay_of_frozen_CWRU_locked_test_not_hardware_validation",
        "reference_result": str(result_path), "reference_result_sha256": sha256(result_path),
        "active_manifest_sha256": sha256(manifest_path), "cwru_manifest_sha256": sha256(cwru_manifest_path),
        "selection_sha256": sha256(selection_path), "model_sha256": sha256(model_path),
        "source_mat_sha256": source_hashes,
        "classifier": {"selected_model": selection["selected_model"], "classes": classes,
                       "feature_names": list(FEATURE_NAMES), "selection_or_fit_performed": False},
        "input_contract": {"sampling_hz": 12000, "window_samples": 4096, "stride_samples": 2048,
                           "rotation_hz": "source metadata rated RPM / 60", "truth": "normal vs ball/inner_race/outer_race"},
        "overall": overall,
        "total_source_windows": sum(row["total_windows"] for row in file_rows),
        "confirmation_buffer_windows": sum(row["confirmation_buffer_windows"] for row in file_rows),
        "evaluated_windows": len(all_truth), "file_count": len(file_rows), "files": file_rows,
        "aihub_metrics_aggregated": False,
    }
