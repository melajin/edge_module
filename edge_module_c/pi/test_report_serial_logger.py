"""Focused tests for report serial JSON validation and JSONL writing."""
from __future__ import annotations

import io
import json
import unittest

from report_serial_logger import append_report_lines, parse_report_line, validate_record


def report_record() -> dict:
    return {
        "reason": "phase_history_warmup",
        "fs_hz": 400.52,
        "fg_hz": 137.15,
        "rpm": 4114.5,
        "amp_1x_g": [0.12, 0.03, 0.01],
        "ratio_1x": None,
        "imbalance": "unavailable",
        "imbalance_votes": 0,
        "auto_confirm": False,
    }


class ReportSerialLoggerTests(unittest.TestCase):
    def test_valid_report_row_is_written_with_extra_fields_preserved(self) -> None:
        output, errors = io.StringIO(), io.StringIO()
        record = report_record()
        saved, rejected = append_report_lines(
            ["starting\n", json.dumps(record, ensure_ascii=False)], output, errors
        )

        self.assertEqual((saved, rejected), (1, 0))
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(json.loads(output.getvalue()), record)
        self.assertTrue(output.getvalue().endswith("\n"))

    def test_jsonl_input_skips_text_and_reports_malformed_or_wrong_schema_rows(self) -> None:
        output, errors = io.StringIO(), io.StringIO()
        lines = [
            "boot ready\n",
            '{"reason":\n',
            '{"reason":"ok","imbalance":"normal"}\n',
            json.dumps(report_record()) + "\n",
        ]
        saved, rejected = append_report_lines(lines, output, errors)

        self.assertEqual((saved, rejected), (1, 2))
        self.assertIn("input line 2: invalid JSON", errors.getvalue())
        self.assertIn("input line 3:", errors.getvalue())
        self.assertEqual(len(output.getvalue().splitlines()), 1)

    def test_required_field_types_and_json_constants_are_checked(self) -> None:
        record = report_record()
        record.update(fs_hz=True, amp_1x_g=[0.1, "0.2", 0.3], imbalance_votes=True)
        problems = validate_record(record)
        self.assertTrue(any(problem.startswith("fs_hz:") for problem in problems))
        self.assertTrue(any(problem.startswith("amp_1x_g:") for problem in problems))
        self.assertTrue(any(problem.startswith("imbalance_votes:") for problem in problems))

        parsed, issue = parse_report_line(
            '{"reason":"ok","fs_hz":NaN,"fg_hz":null,"rpm":null,'
            '"amp_1x_g":null,"ratio_1x":null,"imbalance":"unavailable",'
            '"imbalance_votes":0}'
        )
        self.assertIsNone(parsed)
        self.assertIn("invalid JSON", issue or "")


if __name__ == "__main__":
    unittest.main()
