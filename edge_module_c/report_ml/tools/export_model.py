"""Export the five existing CV fold coefficients and their source provenance as C headers."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_CONTRACT = ROOT / "verification/source_candidate/feature_contract.json"
REFERENCE_FOLDS = ROOT / "verification/source_candidate/fit_provenance.json"


def c_array(name: str, values: np.ndarray) -> str:
    values = np.asarray(values)
    shape = "".join(f"[{size}]" for size in values.shape)

    def encode(value):
        if np.ndim(value):
            return "{" + ",".join(encode(item) for item in value) + "}"
        return format(float(value), ".17g")

    return f"static const double {name}{shape}={encode(values)};\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True,
                        help="candidate directory containing feature_contract.json and fold0..4.joblib")
    parser.add_argument("--header-out", type=Path, default=ROOT / "src/ml_model_generated.h")
    parser.add_argument("--dsp-header-out", type=Path, default=ROOT / "src/ml_dsp_tables.h")
    parser.add_argument("--provenance-out", type=Path, default=ROOT / "verification/model_provenance.json")
    args = parser.parse_args()
    source = args.model_dir.resolve()
    contract = json.loads((source / "feature_contract.json").read_text(encoding="utf-8"))
    reference = json.loads(REFERENCE_CONTRACT.read_text(encoding="utf-8"))
    identity = (contract.get("profile"), contract.get("input"), contract.get("family"))
    if identity != ("limited400", "single_ch2", "extended") or identity != (
            reference.get("profile"), reference.get("input"), reference.get("family")):
        parser.error("model directory must match the report's limited400/single_ch2/extended contract")
    if contract.get("features") != reference.get("features") or len(contract.get("features", [])) != 101:
        parser.error("model directory must match the report's exact 101-feature names and order")
    expected_folds = json.loads(REFERENCE_FOLDS.read_text(encoding="utf-8"))
    for fold, record in enumerate(expected_folds):
        actual = hashlib.sha256((source / f"fold{fold}.joblib").read_bytes()).hexdigest()
        if actual != record["model_sha256"]:
            parser.error(f"fold{fold}.joblib does not match the report's selected model")
    models = [joblib.load(source / f"fold{k}.joblib") for k in range(5)]
    classes = list(models[0].classes_)
    if classes != ["imbalance", "mechanical_looseness", "misalignment", "normal"]:
        parser.error(f"unexpected class order: {classes}")
    header = "/* Frozen report CV folds; mean-margin ensemble; source provenance recorded. */\n"
    for name, getter in (("ML_MEAN", lambda model: model[0].mean_),
                         ("ML_SCALE", lambda model: model[0].scale_),
                         ("ML_COEF", lambda model: model[1].coef_),
                         ("ML_INTERCEPT", lambda model: model[1].intercept_)):
        header += c_array(name, np.array([getter(model) for model in models]))
    weights = np.mean([model[1].coef_ / model[0].scale_ for model in models], axis=0)
    bias = np.mean([model[1].intercept_ - np.sum(model[1].coef_ * model[0].mean_ / model[0].scale_, axis=1)
                    for model in models], axis=0)
    header += c_array("ML_ENSEMBLE_WEIGHT", weights) + c_array("ML_ENSEMBLE_BIAS", bias)
    angles = 2 * np.pi * np.arange(400) / 400
    dsp = "/* Fixed 400point DFT lookup: double precision, no perwindow trig. */\n"
    dsp += c_array("ML_COS", np.cos(angles))
    dsp += c_array("ML_SIN", np.sin(angles))
    dsp += c_array("ML_HANN", .5 - .5 * np.cos(angles))
    args.header_out.parent.mkdir(parents=True, exist_ok=True)
    args.dsp_header_out.parent.mkdir(parents=True, exist_ok=True)
    args.provenance_out.parent.mkdir(parents=True, exist_ok=True)
    args.header_out.write_text(header, encoding="utf-8")
    args.dsp_header_out.write_text(dsp, encoding="utf-8")
    provenance = {
        "model_dir": str(source),
        "model_id": "limited400_ch2_extended_linear_c1_cv5_mean_margin_20261002",
        "classes": classes,
        "feature_names": contract["features"],
        "sha256": {f"fold{k}.joblib": hashlib.sha256((source / f"fold{k}.joblib").read_bytes()).hexdigest()
                   for k in range(5)},
        "training": "existing development CV folds",
        "source_dataset": "https://data.mendeley.com/datasets/zx8pfhdtnb/3",
        "ensemble_evaluated": False,
        "live_validated": False,
        "headers": {"model": str(args.header_out), "dsp": str(args.dsp_header_out)},
    }
    args.provenance_out.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    print(json.dumps({"folds": 5, "features": 101, "classes": classes,
                      "model_header": str(args.header_out), "dsp_header": str(args.dsp_header_out),
                      "provenance": str(args.provenance_out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
