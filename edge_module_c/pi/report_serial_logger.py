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

    return errors


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
    for line_number, line in enumerate(lines, start=1):
        record, issue = parse_report_line(line)
        if issue:
            rejected += 1
            print(f"[report-logger] input line {line_number}: {issue}", file=errors)
        elif record is not None:
            write_record(output, record)
            saved += 1
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
                    print(f"[report-logger] serial line {input_line}: {issue}", file=sys.stderr)
                elif record is not None:
                    write_record(output, record)
                    count += 1
                    print(f"[report-logger] JSONL 기록 {count}건", flush=True)
    except KeyboardInterrupt:
        print(f"\n[report-logger] 종료 · 기록 {count}건", flush=True)
        return 0
    except serial.SerialException as exc:
        print(f"[report-logger] 직렬 연결 오류: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
