"""Reject neighboring model profiles and changed feature order before export."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.verify_ml import ROOT, validate_model_contract


class ReportModelContractTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / "verification/source_candidate/feature_contract.json"
        self.contract = json.loads(path.read_text(encoding="utf-8"))

    def test_limited800_profile_is_rejected(self):
        wrong = {**self.contract, "profile": "limited800"}
        with self.assertRaisesRegex(ValueError, "does not match report profile"):
            validate_model_contract(Path("unused"), wrong)

    def test_changed_feature_order_is_rejected(self):
        wrong = {**self.contract, "features": list(reversed(self.contract["features"]))}
        with self.assertRaisesRegex(ValueError, "feature names/order"):
            validate_model_contract(Path("unused"), wrong)


if __name__ == "__main__":
    unittest.main(verbosity=2)
