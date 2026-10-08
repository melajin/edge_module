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


def quality_record(seq: int = 0, session: str = "0123456789abcdef") -> dict:
    record = report_record()
    record.update(samples=512, acquisition={
        "v": 1, "session": session, "seq": seq,
        "start_us": 1000000 + seq * 2000000,
        "end_us": 2277500 + seq * 2000000,
        "gap_us": None if seq == 0 else 722500,
        "valid": True, "reason": "ok",
    })
    return record


class ReportSerialLoggerTests(unittest.TestCase):
    def test_runtime_first_previous_error_and_preservation(self) -> None:
        records = [quality_record(0), quality_record(1), quality_record(2)]
        for seq, record in enumerate(records):
            record["runtime"] = {"v": 1, "pre_emit_us": 1283000,
                "previous_emit": None if seq == 0 else {"seq": seq - 1, "call_us": 740},
                "heap": {"free_bytes": 240000, "min_free_bytes": 220000, "largest_free_bytes": 120000}}
        records[2].update(reason="adxl_init", samples=0)
        records[2]["acquisition"].update(start_us=None, end_us=None, gap_us=None,
                                         valid=False, reason="no_samples")
        records[2]["runtime"]["pre_emit_us"] = 0
        for record in records:
            self.assertEqual(validate_record(record), [])
        output = io.StringIO()
        self.assertEqual(append_report_lines(map(json.dumps, records), output), (3, 0))
        for original, line in zip(records, output.getvalue().splitlines()):
            self.assertEqual(json.loads(line)["runtime"], original["runtime"])
        self.assertEqual(validate_record(report_record()), [])

    def test_runtime_rejects_bad_values_and_relations(self) -> None:
        def valid():
            r = quality_record(1)
            r["runtime"] = {"v": 1, "pre_emit_us": 1283000,
                "previous_emit": {"seq": 0, "call_us": 740},
                "heap": {"free_bytes": 100, "min_free_bytes": 80, "largest_free_bytes": 50}}
            return r
        cases = [("v", True), ("v", 2), ("pre_emit_us", True), ("pre_emit_us", -1),
                 ("pre_emit_us", 1.5), ("pre_emit_us", 2**53), ("previous_emit", None),
                 ("previous_emit", True), ("heap", [])]
        for key, value in cases:
            bad = valid(); bad["runtime"][key] = value
            with self.subTest(key=key, value=value):
                self.assertTrue(validate_record(bad))
        for obj, keys in [("previous_emit", ("seq", "call_us")),
                          ("heap", ("free_bytes", "min_free_bytes", "largest_free_bytes"))]:
            for key in keys:
                for value in [True, -1, 2**53, 1.5, None]:
                    bad = valid(); bad["runtime"][obj][key] = value
                    self.assertTrue(validate_record(bad))
                bad = valid(); del bad["runtime"][obj][key]
                self.assertTrue(validate_record(bad))
        for key in ["v", "pre_emit_us", "previous_emit", "heap"]:
            bad = valid(); del bad["runtime"][key]
            self.assertTrue(validate_record(bad))
        bad = valid(); bad["runtime"]["previous_emit"]["seq"] = 1
        self.assertTrue(validate_record(bad))
        bad = valid(); del bad["acquisition"]
        self.assertTrue(validate_record(bad))
        for key in ["min_free_bytes", "largest_free_bytes"]:
            bad = valid(); bad["runtime"]["heap"][key] = 101
            self.assertTrue(validate_record(bad))
        bad = valid(); bad["acquisition"]["seq"] = 0
        self.assertTrue(validate_record(bad))

    def test_valid_report_row_is_written_with_extra_fields_preserved(self) -> None:
        output, errors = io.StringIO(), io.StringIO()
        record = report_record()
        saved, rejected = append_report_lines(
            ["starting\n", json.dumps(record, ensure_ascii=False)], output, errors
        )

        self.assertEqual((saved, rejected), (1, 0))
        self.assertEqual(errors.getvalue(), "")
        stored = json.loads(output.getvalue())
        self.assertEqual(stored.pop("collector_report_transport")["status"], "unknown")
        self.assertEqual(stored, record)
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

    def test_acquisition_validation_and_original_fields(self) -> None:
        record = quality_record()
        record["acquisition"]["extension"] = "preserve"
        self.assertEqual(validate_record(record), [])
        output = io.StringIO()
        append_report_lines([json.dumps(record)], output)
        stored = json.loads(output.getvalue())
        self.assertEqual(stored.pop("collector_report_transport")["status"], "first")
        self.assertEqual(stored, record)
        for key, value in [("v", True), ("seq", True), ("seq", 2**53),
                           ("seq", 1.0), ("session", "short"), ("valid", 1),
                           ("start_us", -1), ("end_us", 10), ("gap_us", float("inf")),
                           ("reason", "partial_window")]:
            bad = quality_record()
            bad["acquisition"][key] = value
            with self.subTest(key=key, value=value):
                self.assertTrue(validate_record(bad))
        for count in [True, -1, 0, 1, 511, 513]:
            bad = quality_record()
            bad["samples"] = count
            self.assertTrue(validate_record(bad))

    def test_zero_partial_and_diagnostic_unavailable(self) -> None:
        no = quality_record()
        no["samples"] = 0
        no["acquisition"].update(start_us=None, end_us=None, gap_us=None,
                                 valid=False, reason="no_samples")
        self.assertEqual(validate_record(no), [])
        partial = quality_record()
        partial["samples"] = 3
        partial["acquisition"].update(end_us=1005000, valid=False, reason="partial_window")
        self.assertEqual(validate_record(partial), [])
        # FG/baseline diagnostic availability never changes acquisition validity.
        full = quality_record()
        full.update(reason="fg_missing", fs_hz=None, fg_hz=None, rpm=None)
        self.assertEqual(validate_record(full), [])

    def test_sequence_transitions_and_uncertainty_boundaries(self) -> None:
        records = [quality_record(0), quality_record(1), quality_record(3),
                   quality_record(3), quality_record(2), quality_record(4),
                   quality_record(0, "fedcba9876543210"), report_record(),
                   quality_record(2), quality_record(3)]
        lines = [json.dumps(row) for row in records]
        lines.insert(-1, '{broken')
        output, errors = io.StringIO(), io.StringIO()
        saved, rejected = append_report_lines(lines, output, errors)
        self.assertEqual((saved, rejected), (10, 1))
        transport = [json.loads(line)["collector_report_transport"] for line in output.getvalue().splitlines()]
        self.assertEqual([t["status"] for t in transport],
                         ["first", "contiguous", "gap", "duplicate", "out_of_order",
                          "contiguous", "session_changed", "unknown", "first", "first"])
        self.assertEqual(transport[2]["missing"], 1)
        self.assertIsNone(transport[4]["missing"])
        self.assertIsNone(transport[6]["missing"])

    def test_collector_collision_never_overwrites_device_field(self) -> None:
        record = quality_record()
        record["collector_report_transport"] = {"status": "forged"}
        output, errors = io.StringIO(), io.StringIO()
        self.assertEqual(append_report_lines([json.dumps(record)], output, errors), (0, 1))
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(record["collector_report_transport"], {"status": "forged"})

    def test_non_json_serial_line_breaks_continuity_claim(self) -> None:
        output = io.StringIO()
        append_report_lines([json.dumps(quality_record()), "garbled serial data",
                             json.dumps(quality_record(1))], output)
        self.assertEqual([json.loads(line)["collector_report_transport"]["status"]
                          for line in output.getvalue().splitlines()], ["first", "first"])


if __name__ == "__main__":
    unittest.main()
