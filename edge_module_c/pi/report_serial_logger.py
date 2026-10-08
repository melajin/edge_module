#!/usr/bin/env python3
"""Append FG report JSON records from an ESP32 serial port to JSONL.

The logger checks the eight report fields and appends each accepted JSON object
with its recorded fields and values intact. The captured status and vote count
remain available as source fields for later review.

Usage:
    python report_serial_logger.py --port COM3 --output ./logs/fg-report.jsonl

Install the serial transport with ``python -m pip install pyserial``.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Iterable, TextIO


BAUD_RATE = 115200
REQUIRED_FIELDS = (
    "reason",
    "fs_hz",
    "fg_hz",
    "rpm",
    "amp_1x_g",
    "ratio_1x",
    "imbalance",
    "imbalance_votes",
)
NUMERIC_FIELDS = ("fs_hz", "fg_hz", "rpm", "ratio_1x")
MAX_SAFE_INTEGER = 2**53 - 1
COLLECTOR_FIELD = "collector_report_transport"


def _is_counter(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_SAFE_INTEGER


def validate_acquisition(record: dict) -> list[str]:
    if "acquisition" not in record:
        return []  # Legacy records have unknown acquisition quality.
    a = record["acquisition"]
    if not isinstance(a, dict):
        return ["acquisition: expected an object"]
    errors = []
    for key in ("v", "session", "seq", "start_us", "end_us", "gap_us", "valid", "reason"):
        if key not in a:
            errors.append(f"acquisition.{key}: missing field")
    if type(a.get("v")) is not int or a["v"] != 1:
        errors.append("acquisition.v: expected 1")
    if not isinstance(a.get("session"), str) or not re.fullmatch(r"[0-9a-fA-F]{16}", a["session"]):
        errors.append("acquisition.session: expected 16 hexadecimal digits")
    if not _is_counter(a.get("seq")):
        errors.append("acquisition.seq: expected a nonnegative safe integer")
    for key in ("start_us", "end_us", "gap_us"):
        if a.get(key) is not None and not _is_counter(a[key]):
            errors.append(f"acquisition.{key}: expected a nonnegative safe integer or null")
    if type(a.get("valid")) is not bool:
        errors.append("acquisition.valid: expected a boolean")
    reasons = {"ok", "no_samples", "partial_window", "sample_time", "sample_rate", "sensor_range", "numeric_input"}
    if not isinstance(a.get("reason"), str) or a["reason"] not in reasons:
        errors.append("acquisition.reason: unsupported quality reason")
    count = record.get("samples")
    if not _is_counter(count) or count > 512:
        errors.append("samples: acquisition requires an integer from 0 to 512")
    if errors:
        return errors
    start, end, gap = a["start_us"], a["end_us"], a["gap_us"]
    if count == 0:
        if any(x is not None for x in (start, end, gap)) or a["reason"] != "no_samples" or a["valid"]:
            errors.append("acquisition: no samples requires null times and invalid/no_samples")
    elif start is None or end is None or end < start or (count > 1 and end == start):
        errors.append("acquisition: samples require ordered start/end times")
    elif count == 1 and end != start:
        errors.append("acquisition: one sample requires equal start/end times")
    if count and count < 512 and (a["valid"] or a["reason"] != "partial_window"):
        errors.append("acquisition: incomplete samples require invalid/partial_window")
    if count == 512 and a["reason"] in {"no_samples", "partial_window"}:
        errors.append("acquisition: full sample count conflicts with reason")
    if a["valid"] != (a["reason"] == "ok"):
        errors.append("acquisition: valid and reason disagree")
    if a["valid"] and start is not None and end is not None and end > start:
        rate = 511_000_000 / (end - start)
        if rate < 380 or rate > 420:
            errors.append("acquisition: valid window rate is outside 380..420 Hz")
    if a["seq"] == 0 and gap is not None:
        errors.append("acquisition: first attempt cannot have a previous sample gap")
    if start is not None and gap is not None and gap > start:
        errors.append("acquisition: gap exceeds the boot clock")
    return errors


class SequenceTracker:
    """Receiver evidence only: sequence continuity is not a CRC check."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.session: str | None = None
        self.seq: int | None = None

    def observe(self, record: dict) -> dict:
        a = record.get("acquisition")
        status, missing = "unknown", None
        if a is None:
            self.reset()
        else:
            session, seq = a["session"].lower(), a["seq"]
            if self.session is None:
                status = "first"
            elif session != self.session:
                status = "session_changed"
            elif seq == self.seq:
                status, missing = "duplicate", 0
            elif seq < self.seq:
                status = "out_of_order"
            else:
                missing = seq - self.seq - 1
                status = "gap" if missing else "contiguous"
            if status not in {"duplicate", "out_of_order"}:
                self.session, self.seq = session, seq
        return {"source": "report_serial_logger", "status": status, "missing": missing}


def collected_record(record: dict, tracker: SequenceTracker) -> dict:
    # Reserved field collisions are rejected at collection, never overwritten.
    if COLLECTOR_FIELD in record:
        raise ValueError(f"{COLLECTOR_FIELD}: reserved collector field already present")
    return {**record, COLLECTOR_FIELD: tracker.observe(record)}


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON constant {value} is not a finite number")


def _is_finite_number(value: object) -> bool:
    return type(value) is int or (type(value) is float and math.isfinite(value))


def validate_record(value: object) -> list[str]:
    """Return schema errors for the eight FG report fields.

    Other top-level fields remain valid and are preserved in the saved JSONL.
    """
    if not isinstance(value, dict):
        return ["record must be a JSON object"]

    errors = [f"{name}: missing field" for name in REQUIRED_FIELDS if name not in value]
    if not isinstance(value.get("reason"), str):
        errors.append("reason: expected a string")

    for name in NUMERIC_FIELDS:
        item = value.get(name)
        if item is not None and not _is_finite_number(item):
            errors.append(f"{name}: expected a finite number or null")

    amplitude = value.get("amp_1x_g")
    if amplitude is not None and not (
        isinstance(amplitude, list)
        and len(amplitude) == 3
        and all(_is_finite_number(item) for item in amplitude)
    ):
        errors.append("amp_1x_g: expected three finite numbers or null")

    if not isinstance(value.get("imbalance"), str):
        errors.append("imbalance: expected a string")

    votes = value.get("imbalance_votes")
    if isinstance(votes, bool) or not isinstance(votes, int):
        errors.append("imbalance_votes: expected an integer")

    return errors + validate_acquisition(value)


def parse_report_line(line: bytes | str) -> tuple[dict | None, str | None]:
    """Parse one serial line; return (record, error), or (None, None) for console text."""
    try:
        text = line.decode("utf-8").strip() if isinstance(line, bytes) else line.strip()
    except UnicodeDecodeError as exc:
        return None, f"serial text is not UTF-8: {exc}"

    if not text or not text.startswith("{"):
        return None, None

    try:
        value = json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"invalid JSON: {exc}"

    errors = validate_record(value)
    if errors:
        return None, "; ".join(errors)
    return value, None


def write_record(output: TextIO, record: dict) -> None:
    output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    output.flush()


def append_report_lines(
    lines: Iterable[bytes | str], output: TextIO, errors: TextIO = sys.stderr
) -> tuple[int, int]:
    """Append valid FG records and return (saved, rejected-schema-or-json)."""
    saved = rejected = 0
    tracker = SequenceTracker()
    for line_number, line in enumerate(lines, start=1):
        record, issue = parse_report_line(line)
        if issue:
            tracker.reset()
            rejected += 1
            print(f"[report-logger] input line {line_number}: {issue}", file=errors)
        elif record is not None:
            try:
                collected = collected_record(record, tracker)
            except ValueError as exc:
                tracker.reset()
                rejected += 1
                print(f"[report-logger] input line {line_number}: {exc}", file=errors)
                continue
            write_record(output, collected)
            saved += 1
        elif line.strip():
            # Console text may also be a damaged frame; do not carry a prior
            # continuity claim across an unrecognized nonempty serial line.
            tracker.reset()
    return saved, rejected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ESP32 FG 보고서 JSON 행을 검증해 JSONL로 이어 씁니다."
    )
    parser.add_argument("--port", required=True, help="직렬 포트 (예: COM3 또는 /dev/ttyUSB0)")
    parser.add_argument("--output", required=True, type=Path, help="기록을 추가할 JSONL 파일")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        import serial
    except ImportError:
        print("pyserial을 설치하세요: python -m pip install pyserial", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    input_line = 0
    tracker = SequenceTracker()
    print(f"[report-logger] {args.port} · {BAUD_RATE} baud → {args.output}", flush=True)
    try:
        with serial.Serial(args.port, BAUD_RATE, timeout=1) as port, args.output.open(
            "a", encoding="utf-8", newline="\n"
        ) as output:
            while True:
                line = port.readline()
                if not line:
                    continue
                input_line += 1
                record, issue = parse_report_line(line)
                if issue:
                    tracker.reset()
                    print(f"[report-logger] serial line {input_line}: {issue}", file=sys.stderr)
                elif record is not None:
                    try:
                        collected = collected_record(record, tracker)
                    except ValueError as exc:
                        tracker.reset()
                        print(f"[report-logger] serial line {input_line}: {exc}", file=sys.stderr)
                        continue
                    write_record(output, collected)
                    count += 1
                    print(f"[report-logger] JSONL 기록 {count}건", flush=True)
                elif line.strip():
                    tracker.reset()
    except KeyboardInterrupt:
        print(f"\n[report-logger] 종료 · 기록 {count}건", flush=True)
        return 0
    except serial.SerialException as exc:
        print(f"[report-logger] 직렬 연결 오류: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
