"""Focused tests for report serial JSON validation and JSONL writing."""
from __future__ import annotations

import copy
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


def profile_event() -> dict:
    return {"event":"profile", "v":1, "command":"profile", "ok":True, "reason":"ok", "firmware":"postreport_v2",
        "profile":{"selected":{"context_id":"bench-1", "ppr":2}, "current":None, "candidate":None, "previous":None,
          "baseline_ready":False, "learning":False, "learn_count":0, "paused":False,
          "storage":{"hold":False,"reason":"ok","generation":0}, "legacy":{"valid":False,"reason":"absent"}}}


def baseline(identifier=1) -> dict:
    return {"id":identifier,"context_id":"bench-1","ppr":2,"amplitude_g":[.001,.2,4],"learn_count":336,
            "origin":"learned","session":"0123456789abcdef","seq":7}


class ReportSerialLoggerTests(unittest.TestCase):
    def test_profile_mixed_jsonl_does_not_consume_measurement_sequence(self):
        event = profile_event()
        event["profile"]["candidate"] = baseline()
        records = [report_record(), quality_record(0), event, quality_record(1), event,
                   quality_record(3), event, quality_record(0, "fedcba9876543210")]
        output = io.StringIO()
        self.assertEqual(append_report_lines(map(json.dumps,records),output),(8,0))
        saved = list(map(json.loads,output.getvalue().splitlines()))
        self.assertEqual(saved[2],event)
        self.assertEqual([r["collector_report_transport"]["status"] for r in saved if "event" not in r],
                         ["unknown","first","contiguous","gap","session_changed"])
        self.assertEqual(saved[5]["collector_report_transport"]["missing"],1)

    def test_profile_strict_values_and_relationships(self):
        e = profile_event(); p = e["profile"]
        p["current"] = baseline(); p["baseline_ready"] = True
        p["candidate"] = baseline(2); p["previous"] = baseline(3)
        self.assertEqual(validate_record(e),[])
        for path, values in [
            (("v",),[True,2]), (("command",),["unknown",[]]), (("ok",),[1]),
            (("reason",),["<img>","x"*65,""]), (("firmware",),["v3"]),
            (("profile","selected","ppr"),[True,-1,17,1.5]),
            (("profile","current","id"),[True,0,2**32]),
            (("profile","current","context_id"),[None,"x"*13,"<script>"]),
            (("profile","current","ppr"),[0,17,True]),
            (("profile","current","seq"),[True,-1,2**53]),
            (("profile","current","session"),["0123456789ABCDEF",None]),
            (("profile","current","amplitude_g"),[[True,.2,1],[0,.2,1],[.1,.2,4.01],[.1,float('inf'),1],[]]),
            (("profile","current","learn_count"),[True,0,335]),
            (("profile","current","origin"),["unknown",[]]),
            (("profile","storage","generation"),[True,-1,2**32]),
            (("profile","storage","hold"),[1,True]),
            (("profile","learning"),[1]), (("profile","learn_count"),[True,-1,337,1]),
            (("profile","legacy","valid"),[1]), (("profile","paused"),[1]),
        ]:
            for value in values:
                bad = copy.deepcopy(e); at = bad
                for key in path[:-1]: at = at[key]
                at[path[-1]] = value
                with self.subTest(path=path,value=value): self.assertTrue(validate_record(bad))
        for path in [(),("profile",),("profile","current"),("profile","selected"),("profile","storage"),("profile","legacy")]:
            bad=copy.deepcopy(e); at=bad
            for key in path: at=at[key]
            for key in list(at):
                missing=copy.deepcopy(bad); obj=missing
                for step in path: obj=obj[step]
                del obj[key]; self.assertTrue(validate_record(missing))
            at["extra"]=1; self.assertTrue(validate_record(bad))
        bad=copy.deepcopy(e); bad["profile"]["candidate"]["id"]=1
        self.assertTrue(validate_record(bad))
        bad=copy.deepcopy(e); bad["profile"]["selected"]["ppr"]=3
        self.assertTrue(validate_record(bad))
        bad=copy.deepcopy(e); bad["profile"]["current"]=None
        self.assertTrue(validate_record(bad))
        imported=copy.deepcopy(e); imported["profile"]["current"].update(origin="legacy_import",learn_count=0)
        self.assertEqual(validate_record(imported),[])
        fresh=profile_event(); fresh["profile"]["selected"].update(context_id=None,ppr=0)
        self.assertEqual(validate_record(fresh),[])
        fresh["profile"]["selected"]["ppr"]=2
        self.assertEqual(validate_record(fresh),[])
        p["learning"]=True; p["learn_count"]=7
        self.assertEqual(validate_record(e),[])  # Ready current can coexist with learning.

    def test_measurement_profile_pairs_and_event_collision(self):
        for context, identifier in [(None,None),("bench-1",None),("bench-1",1)]:
            self.assertEqual(validate_record(dict(report_record(),context_id=context,baseline_id=identifier)),[])
        for extra in [{"context_id":"bench-1"},{"baseline_id":1},{"context_id":None,"baseline_id":1},
                      {"context_id":"<img>","baseline_id":None},{"context_id":"bench-1","baseline_id":True}]:
            self.assertTrue(validate_record(dict(report_record(),**extra)))
        event=profile_event(); event["collector_report_transport"]={"status":"forged"}
        output=io.StringIO()
        self.assertEqual(append_report_lines([json.dumps(event)],output,io.StringIO()),(0,1))
        malformed=profile_event(); malformed["event"]="unknown"
        self.assertTrue(validate_record(malformed))

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
