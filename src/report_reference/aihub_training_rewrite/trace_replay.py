"""Recompute file and window summaries from the pinned AI-Hub verdict trace.

This replays already-computed instant verdicts and stored confirmation flags. It
does not recalculate the original V2 signal features or normal-baseline model.
"""
from __future__ import annotations

from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
from typing import Iterable

from .contracts import AIHUB_LABELS
from .v2_reference import instant_from_z

AIHUB_ROOT = Path("F:/aihub_training_runs")
INSTANT_PATH = AIHUB_ROOT / "mock_exam_v2_instant_diagnostic/instant_diagnostic.json"
BELT_PATH = AIHUB_ROOT / "belt_recurrence_diagnostic/belt_recurrence_diagnostic.json"
RUN_MANIFEST_PATH = AIHUB_ROOT / "mock_exam_v2_improvement/run_manifest.json"
RUN_REPORT_PATH = AIHUB_ROOT / "mock_exam_v2_improvement/report.json"
INSTANT_SHA256 = "5f07e199f75e9da2f440db80b2ff62c6c3279e53e460d8a129b1275185ea4184"
BELT_SHA256 = "a20506b2d5674bd1423522213e6669433255b8effccf99677defd72ca792d203"
RUN_MANIFEST_SHA256 = "24d814bb0f40ff07f7c45c6ff69ee5de8422fa9bd955a137dc5cb1fb9accb161"
RUN_REPORT_SHA256 = "3235cd9e29a02d87269119d354eddd1bab74c9042ab9ca2d5e96459d02e6f0cd"
SELECTED_V2 = "s3_h4_n5_original5"
NORMAL = "정상"
BELT = "벨트느슨함"
AIHUB_SOURCE_LABELS = set(AIHUB_LABELS)
BELT_RECURRENCE_LABELS = {NORMAL, BELT}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _contract_path(raw_path: str, project_root: Path) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else project_root / path


def verify_aihub_pins(run_manifest_path: Path = RUN_MANIFEST_PATH,
                      run_report_path: Path = RUN_REPORT_PATH,
                      instant_script_path: Path | None = None,
                      project_root: Path | None = None) -> dict:
    """Verify fixed run JSON hashes before trusting their internal source pins."""
    manifest_hash = sha256(run_manifest_path)
    if manifest_hash != RUN_MANIFEST_SHA256:
        raise ValueError("upstream run_manifest.json SHA-256 mismatch")
    report_hash = sha256(run_report_path)
    if report_hash != RUN_REPORT_SHA256:
        raise ValueError("upstream report.json SHA-256 mismatch")

    root = project_root or Path(__file__).resolve().parents[1]
    manifest = _read_json(run_manifest_path)
    report = _read_json(run_report_path)
    if report.get("development_selection", {}).get("selected") != SELECTED_V2:
        raise ValueError("upstream selected V2 candidate differs from s3_h4_n5_original5")
    contract = manifest.get("contract", {})
    code = contract.get("code", {})
    if not code:
        raise ValueError("upstream run manifest has no contract.code pins")
    checked_code = {}
    for raw_path, expected in sorted(code.items()):
        path = _contract_path(raw_path, root)
        actual = sha256(path)
        if actual != expected:
            raise ValueError(f"upstream Python source hash mismatch: {path}")
        checked_code[raw_path] = actual

    binary = contract.get("binary_pins", {}).get("original", {})
    source_files = binary.get("source_files", {})
    if not source_files:
        raise ValueError("upstream baseline C core pin has no source_files")
    checked_core = {}
    for raw_path, expected in sorted(source_files.items()):
        path = _contract_path(raw_path, root)
        actual = sha256(path)
        if actual != expected:
            raise ValueError(f"upstream baseline C source hash mismatch: {path}")
        checked_core[raw_path] = actual
    core_source_hash = hashlib.sha256(json.dumps(
        checked_core, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if core_source_hash != binary.get("source_hash"):
        raise ValueError("upstream baseline C aggregate source hash mismatch")
    library = _contract_path(binary["library_path"], root)
    actual_library_hash = sha256(library)
    if actual_library_hash != binary.get("library_sha256"):
        raise ValueError("upstream baseline C library SHA-256 mismatch")

    script = instant_script_path or root / "aihub_training/mock_exam_v2_instant_diagnostic.py"
    instant_manifest = _read_json(INSTANT_PATH)
    aggregator_hash = sha256(script)
    if instant_manifest.get("script_sha256") != aggregator_hash:
        raise ValueError("instant diagnostic script_sha256 does not match aggregate runner source")
    return {
        "run_manifest": str(run_manifest_path), "run_manifest_sha256": manifest_hash,
        "run_report": str(run_report_path), "run_report_sha256": report_hash,
        "selected_candidate": SELECTED_V2,
        "locked_test_read": bool(report.get("locked_test_read")),
        "hardware_validation": bool(report.get("hardware_validation")),
        "python_source_pins": checked_code,
        "baseline_c_core_source_pins": checked_core,
        "baseline_c_core_aggregate_sha256": core_source_hash,
        "baseline_c_library": str(library), "baseline_c_library_sha256": actual_library_hash,
        "instant_trace_aggregator": str(script), "instant_trace_aggregator_sha256": aggregator_hash,
        "note": "instant_diagnostic.script_sha256 pins its aggregator only; upstream model sources are independently pinned in run_manifest.contract.code.",
    }


def _load_rows_and_file_counts(manifest_path: Path) -> tuple[list[dict], dict[str, int], dict[str, str]]:
    if sha256(manifest_path) != INSTANT_SHA256:
        raise ValueError("pinned AI-Hub instant trace manifest SHA-256 mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "v2_instant_temporal_diagnostic_v1":
        raise ValueError("unexpected AI-Hub instant trace schema")
    source_root = Path(manifest["source_root"]).resolve()
    rows: list[dict] = []
    file_counts: dict[str, int] = defaultdict(int)
    used_sources: dict[str, str] = {}
    expected_groups = {name: detail for name, detail in manifest["groups"].items()
                       if detail.get("state") == "evaluated"}
    if len(expected_groups) != 28:
        raise ValueError(f"expected 28 evaluated groups, found {len(expected_groups)}")
    by_path = {Path(item["path"]).resolve(): item for item in manifest["source_artifacts"]}
    for group_id in sorted(expected_groups):
        directory = source_root / group_id
        diag_path = (directory / "diagnosis.json").resolve()
        seq_path = (directory / "validation_window_sequences.jsonl.gz").resolve()
        for path, kind in ((diag_path, "diagnosis_json"), (seq_path, "validation_sequence_gzip")):
            pinned = by_path.get(path)
            if not pinned or pinned.get("kind") != kind or sha256(path) != pinned.get("sha256"):
                raise ValueError(f"pinned source artifact mismatch: {path}")
            used_sources[str(path)] = pinned["sha256"]
        diagnosis = json.loads(diag_path.read_text(encoding="utf-8"))
        if diagnosis.get("group") != group_id or diagnosis.get("original_calibration_state") != "completed":
            raise ValueError(f"ineligible diagnosis group in pinned cohort: {group_id}")
        for block in diagnosis["blocks"]:
            source_label = block.get("source_label", block.get("label"))
            if source_label in AIHUB_SOURCE_LABELS:
                file_counts[source_label] += int(block["files"])
        with gzip.open(seq_path, "rt", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                raw = json.loads(line)
                if raw.get("group") != group_id:
                    raise ValueError(f"group mismatch in sequence at {seq_path}:{line_number}")
                if type(raw.get("instant_valid")) is not bool or type(raw.get("confirmed_valid")) is not bool:
                    raise ValueError("trace validity fields must be explicit booleans")
                if raw["instant_valid"] and type(raw.get("instant_flag")) is not bool:
                    raise ValueError("instant_valid trace row lacks an instant flag")
                if raw["confirmed_valid"] and type(raw.get("is_anomaly")) is not bool:
                    raise ValueError("confirmed_valid trace row lacks a confirmed verdict")
                if raw["instant_valid"] and raw.get("z") is None:
                    raise ValueError("instant-valid trace row lacks selected C z values")
                rows.append({
                    "group": group_id, "session": raw["session"], "block": int(raw["block"]),
                    "window": int(raw["window"]), "ending_member": raw.get("ending_member"),
                    "source_label": raw.get("source_label", raw["label"]),
                    "instant_valid": raw["instant_valid"],
                    "instant_flag": raw.get("instant_flag"),
                    "confirmed_valid": raw["confirmed_valid"],
                    "is_anomaly": raw.get("is_anomaly"),
                    "z": raw.get("z"),
                })
    expected_counts = {"정상": 19565, "베어링불량": 25060, "회전체불평형": 13082,
                       "축정렬불량": 19137, "벨트느슨함": 16343}
    if file_counts != expected_counts:
        raise ValueError(f"available AI-Hub eligible source file counts changed: {dict(file_counts)}")
    return rows, dict(file_counts), used_sources


def _confusion(predictions: dict[int, bool], labels: dict[int, str], allowed: set[str]) -> dict:
    selected = {member: label for member, label in labels.items() if label in allowed}
    scored = set(predictions).intersection(selected)
    tn = fp = fn = tp = 0
    for member in scored:
        y = selected[member] != NORMAL
        p = predictions[member]
        if y and p: tp += 1
        elif y: fn += 1
        elif p: fp += 1
        else: tn += 1
    return {"available": len(selected), "valid": len(scored), "unscored": len(selected) - len(scored),
            "tn": tn, "fp": fp, "fn": fn, "tp": tp}


def _file_snapshots(rows: Iterable[dict], *, verdict_key: str, valid_key: str,
                    allowed: set[str] | None = None) -> tuple[dict[int, bool], dict[int, str]]:
    predictions: dict[int, bool] = {}
    labels: dict[int, str] = {}
    for row in rows:
        member = row.get("ending_member")
        label = row.get("source_label")
        if member is None or (allowed is not None and label not in allowed):
            continue
        labels[int(member)] = label
        if row[valid_key]:
            predictions[int(member)] = bool(row[verdict_key])
    return predictions, labels


def _class_instant_metrics(rows: list[dict], declared_file_counts: dict[str, int]) -> dict:
    predictions, labels = _file_snapshots(rows, verdict_key="instant_flag", valid_key="instant_valid")
    result: dict[str, dict] = {}
    for source_label, canonical in AIHUB_LABELS.items():
        if canonical == "normal":
            continue
        available = declared_file_counts[source_label]
        if available == 0:
            continue
        members = {member for member, label in labels.items() if label == source_label}
        scored = members.intersection(predictions)
        tp = sum(predictions[m] for m in scored)
        fn = len(scored) - tp
        result[canonical] = {"source_label": source_label, "available": available,
                             "instant_valid": len(scored), "tp": tp, "fn": fn,
                             "unscored": available - len(scored),
                             "conditional_recall": tp / len(scored) if scored else None,
                             "all_available_capture": tp / available if available else None,
                             "denominator_unit": "eligible validation source CSV; last instant-valid window verdict"}
    return result


def _binary_file_counts(predictions: dict[int, bool], labels: dict[int, str],
                        available_by_label: dict[str, int], allowed: set[str]) -> dict:
    selected_labels = {member: label for member, label in labels.items() if label in allowed}
    scored = set(predictions).intersection(selected_labels)
    tn = fp = fn = tp = 0
    for member in scored:
        is_fault = selected_labels[member] != NORMAL
        predicted = predictions[member]
        if is_fault and predicted: tp += 1
        elif is_fault: fn += 1
        elif predicted: fp += 1
        else: tn += 1
    available = sum(available_by_label[label] for label in allowed)
    valid = len(scored)
    return {"available_files": available, "scored_files": valid,
            "unscored_files": available - valid, "tn": tn, "fp": fp, "fn": fn, "tp": tp,
            "coverage": {"numerator": valid, "denominator": available,
                         "rate": valid / available if available else None},
            "normal_fpr": {"numerator": fp, "denominator": tn + fp,
                           "rate": fp / (tn + fp) if tn + fp else None},
            "fault_recall": {"numerator": tp, "denominator": fn + tp,
                             "rate": tp / (fn + tp) if fn + tp else None},
            "positive_label": "any of four direct fault labels; not a four-way predicted type"}


def _z_flag_parity(rows: list[dict]) -> dict:
    valid = 0
    mismatches = []
    for row in rows:
        if not row["instant_valid"]:
            continue
        valid += 1
        expected = bool(row["instant_flag"])
        actual = instant_from_z(row["z"], sigma=3.0, minimum_deviated_features=1).flag
        if actual != expected:
            mismatches.append({"group": row["group"], "session": row["session"],
                               "block": row["block"], "window": row["window"],
                               "stored": expected, "python": actual})
    if mismatches:
        raise AssertionError(f"selected V2 Python decision differs from stored C z fixture: {mismatches[:5]}")
    return {"valid_stored_c_windows": valid, "matching": valid, "mismatches": 0,
            "scope": "decision-layer parity from stored native-C z vectors; no raw-signal feature replay"}


def apply_belt_recurrence(rows: list[dict], window_count: int = 20) -> list[dict]:
    """Apply the stored 20-window belt rule with gaps/invalid/label changes resetting state."""
    if window_count != 20:
        raise ValueError("historical belt rule is pinned to 20 windows")
    by_session: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        by_session[(row["group"], row["session"])].append(row)
    evaluated: list[dict] = []
    for session_rows in by_session.values():
        history: list[bool] = []
        previous = None
        active_source = None
        for row in sorted(session_rows, key=lambda item: (item["block"], item["window"])):
            source = row["source_label"]
            relevant = source in BELT_RECURRENCE_LABELS
            contiguous = previous is not None and (
                (row["block"] == previous["block"] and row["window"] == previous["window"] + 1)
                or (row["block"] == previous["block"] + 1 and row["window"] == 0)
            )
            if not relevant or not row["instant_valid"] or source != active_source or (previous is not None and not contiguous):
                history = []
            if not relevant or not row["instant_valid"]:
                active_source, previous = None, None
                continue
            active_source = source
            history.append(bool(row["instant_flag"]))
            if len(history) > window_count:
                history.pop(0)
            valid = len(history) == window_count
            groups = [sum(history[offset:offset + 5]) for offset in range(0, 20, 5)] if valid else []
            flag = valid and sum(hits > 0 for hits in groups) >= 3 and sum(history) >= 5
            evaluated.append({**row, "recurrence_valid": valid, "recurrence_flag": bool(flag), "group_hits": groups})
            previous = row
    return evaluated


def _metric_counts(predictions: dict[int, bool], labels: dict[int, str], available_by_label: dict[str, int]) -> dict:
    scored = set(predictions).intersection(labels)
    belt_files = {member for member, label in labels.items() if label == BELT}
    normal_files = {member for member, label in labels.items() if label == NORMAL}
    tp = sum(predictions[m] for m in scored & belt_files)
    fn = sum(not predictions[m] for m in scored & belt_files)
    fp = sum(predictions[m] for m in scored & normal_files)
    tn = sum(not predictions[m] for m in scored & normal_files)
    belt_available, normal_available = available_by_label[BELT], available_by_label[NORMAL]
    return {
        "available": belt_available + normal_available, "valid": len(scored),
        "unscored": belt_available + normal_available - len(scored),
        "tn": tn, "fp": fp, "tp": tp, "fn": fn,
        "belt_available": belt_available, "belt_scored": tp + fn,
        "belt_buffer_or_unscored": belt_available - tp - fn,
        "normal_available": normal_available, "normal_scored": tn + fp,
        "normal_buffer_or_unscored": normal_available - tn - fp,
        "conditional_recall": {"numerator": tp, "denominator": tp + fn,
                                "rate": tp / (tp + fn) if tp + fn else None},
        "overall_capture": {"numerator": tp, "denominator": belt_available,
                            "rate": tp / belt_available if belt_available else None},
        "normal_fpr": {"numerator": fp, "denominator": tn + fp,
                        "rate": fp / (tn + fp) if tn + fp else None},
    }


def _window_confusion(rows: list[dict], valid_key: str, flag_key: str) -> dict:
    selected = [r for r in rows if r.get("source_label") in BELT_RECURRENCE_LABELS and r.get(valid_key)]
    tn = fp = fn = tp = 0
    for row in selected:
        positive = row["source_label"] == BELT
        predicted = bool(row[flag_key])
        if positive and predicted: tp += 1
        elif positive: fn += 1
        elif predicted: fp += 1
        else: tn += 1
    return {"tn": tn, "fp": fp, "fn": fn, "tp": tp, "n": len(selected)}


def replay_aihub(manifest_path: Path = INSTANT_PATH, belt_result_path: Path = BELT_PATH,
                 run_manifest_path: Path = RUN_MANIFEST_PATH,
                 run_report_path: Path = RUN_REPORT_PATH) -> dict:
    if sha256(belt_result_path) != BELT_SHA256:
        raise ValueError("pinned belt recurrence result SHA-256 mismatch")
    belt_reference = json.loads(belt_result_path.read_text(encoding="utf-8"))
    rows, available_source_label_counts, source_hashes = _load_rows_and_file_counts(manifest_path)
    available_by_label = {source: available_source_label_counts[source] for source in AIHUB_SOURCE_LABELS}
    instant, instant_labels = _file_snapshots(rows, verdict_key="instant_flag", valid_key="instant_valid")
    instant_scored = set(instant).intersection(instant_labels)
    overall_instant = _binary_file_counts(instant, instant_labels, available_by_label, AIHUB_SOURCE_LABELS)
    original = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_instant = original["overall"]["file_outcomes"]["instant"]
    for metric, source_key in (("available_files", "available"), ("scored_files", "valid"),
                               ("unscored_files", "unscored"), ("tn", "tn"), ("fp", "fp"),
                               ("fn", "fn"), ("tp", "tp")):
        if overall_instant[metric] != expected_instant[source_key]:
            raise AssertionError(f"AI-Hub instant {metric} mismatch: {overall_instant[metric]}")
    class_results = _class_instant_metrics(rows, available_source_label_counts)
    # Check the four direct classes against the pinned source_class counts. These
    # are existing instant results, not a re-run of the upstream V2 feature code.
    expected_source_classes = original["overall"]["source_classes"]
    for canonical, item in class_results.items():
        expected_label = item["source_label"]
        expected = expected_source_classes[expected_label]
        for field, actual in (("available", item["available"]), ("instant_valid", item["instant_valid"]),
                              ("tp", item["tp"]), ("fn", item["fn"]), ("unscored", item["unscored"])):
            if expected[field] != actual:
                raise AssertionError(f"AI-Hub {canonical} {field} mismatch: {actual} != {expected[field]}")

    # The stored confirmed output is replayed only as an observed baseline. It is
    # independent of the new belt recurrence candidate computed below.
    confirmed_all, confirmed_all_labels = _file_snapshots(
        rows, verdict_key="is_anomaly", valid_key="confirmed_valid")
    overall_four_of_five = _binary_file_counts(confirmed_all, confirmed_all_labels,
                                               available_by_label, AIHUB_SOURCE_LABELS)
    expected_run = _read_json(run_report_path)
    expected_four = expected_run["development_selection"]["scores"][SELECTED_V2]["candidate"]
    for key in ("tn", "fp", "fn", "tp"):
        if overall_four_of_five[key] != expected_four["counts"][key]:
            raise AssertionError(f"AI-Hub eligible trace 4/5 {key} differs from selected report")
    confirmed_belt, confirmed_belt_labels = _file_snapshots(
        rows, verdict_key="is_anomaly", valid_key="confirmed_valid", allowed=BELT_RECURRENCE_LABELS)
    stored_four_of_five = _metric_counts(confirmed_belt, confirmed_belt_labels, {
        NORMAL: available_source_label_counts[NORMAL], BELT: available_source_label_counts[BELT]})
    expected_belt_four = belt_reference["summary"]["comparison_confirmed_4_of_5"]
    if stored_four_of_five != expected_belt_four:
        raise AssertionError("stored AI-Hub 4-of-5 comparator differs from pinned belt result")

    recurrence = apply_belt_recurrence(rows)
    recurrence_predictions: dict[int, bool] = {}
    recurrence_labels: dict[int, str] = {}
    for row in recurrence:
        member = row.get("ending_member")
        if member is not None:
            recurrence_labels[int(member)] = row["source_label"]
            if row["recurrence_valid"]:
                recurrence_predictions[int(member)] = bool(row["recurrence_flag"])
    file_metrics = _metric_counts(recurrence_predictions, recurrence_labels, available_by_label)
    expected_candidate = belt_reference["summary"]["file_metrics"]
    for key in ("available", "valid", "unscored", "tn", "fp", "tp", "fn", "belt_available",
                "belt_scored", "belt_buffer_or_unscored", "normal_available", "normal_scored",
                "normal_buffer_or_unscored"):
        if file_metrics[key] != expected_candidate[key]:
            raise AssertionError(f"belt recurrence file metric {key} mismatch")
    windows = _window_confusion(recurrence, "recurrence_valid", "recurrence_flag")
    expected_window = belt_reference["summary"]["window_metrics"]["confusion"]
    for key in ("tn", "fp", "tp", "fn"):
        if windows[key] != expected_window[key]:
            raise AssertionError(f"belt recurrence window confusion {key} mismatch")
    run_report = _read_json(run_report_path)
    report_score = run_report["development_selection"]["scores"][SELECTED_V2]["candidate"]
    if report_score["available_files"] != expected_four["available_files"] or report_score["n_evaluated"] != expected_four["n_evaluated"]:
        raise AssertionError("AI-Hub report and run_manifest selected metrics disagree")
    pin_check = verify_aihub_pins(run_manifest_path, run_report_path)
    z_parity = _z_flag_parity(rows)
    return {
        "scope": "replay_of_pinned_existing_verdict_trace_not_raw_signal_recomputation_or_hardware_test",
        "input_manifest": str(manifest_path), "input_manifest_sha256": sha256(manifest_path),
        "belt_result": str(belt_result_path), "belt_result_sha256": sha256(belt_result_path),
        "run_report": str(run_report_path), "run_report_sha256": sha256(run_report_path),
        "upstream_pins": pin_check,
        "trace_source_sha256": source_hashes,
        "selected_v2_candidate": SELECTED_V2,
        "selected_v2_stored_z_decision_parity": z_parity,
        "cohort_scope": {
            "upstream_report_scope": "development_and_group_cross_validation_not_final_external_test",
            "locked_test_read": False, "hardware_validation": False,
            "all_validation_files_in_report": report_score["available_files"],
            "report_scored_files": report_score["n_evaluated"],
            "report_coverage": report_score["coverage"],
            "eligible_trace_files": sum(available_by_label.values()),
            "eligible_trace_group_count": 28,
            "eligible_trace_4_of_5_scored_files": overall_four_of_five["scored_files"],
            "eligible_trace_4_of_5_coverage": overall_four_of_five["coverage"]["rate"],
            "note": "The trace excludes 13 calibration-failed groups; its 93,187-file coverage denominator is different from the report's 143,386 all-validation-files denominator.",
        },
        "taxonomy_label_map": AIHUB_LABELS,
        "direct_class_instant_files": class_results,
        "overall_instant_file_metrics": overall_instant,
        "overall_stored_4_of_5_file_metrics_eligible_trace": overall_four_of_five,
        "stored_4_of_5_belt_normal_comparator": stored_four_of_five,
        "belt_20_window_development_candidate": {"rule": belt_reference["summary"]["rule"],
                                                  "file_metrics": file_metrics,
                                                  "window_confusion": windows},
        "mechanical_looseness": {"availability": "unsupported", "independent_label": False,
                                  "reason": "No independent AI-Hub label; rotate type2/type3 are mixed or combined."},
    }
