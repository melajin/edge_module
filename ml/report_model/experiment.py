"""Auditable train/dev/freeze/lock CLI for pinned UPATRAS data."""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys
import zipfile
import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy
import sklearn
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier, HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support
from sklearn.neighbors import NearestCentroid
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVC, SVC
from .data import CLASSES, assert_no_crossrole_duplicates, inventory, numeric_digest, read_member
from .features import PROFILES, extract, feature_names
from .sources import PINNED, prepare, sha256_file, verify

DEFAULT_OUTPUT = Path("output/fault_type_90_mechanical_20261002")
SEED = 20261002

def save_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

def code_hashes():
    return {p.name: sha256_file(p) for p in sorted(Path(__file__).parent.glob("*.py"))}

def environment():
    return {"python": sys.version, "platform": platform.platform(), "numpy": np.__version__,
            "scipy": scipy.__version__, "sklearn": sklearn.__version__,
            "model_identity": None, "usage": None, "cost": None}

def source_path(output: Path) -> Path:
    path = output / "sources" / "UPATRAS.zip"
    _, size, digest = PINNED["UPATRAS.zip"]
    verify(path, size, digest)
    return path

def feature_contract(output: Path, profile: str) -> dict:
    return {"source_sha256": PINNED["UPATRAS.zip"][2], "profile": profile,
            "features_py_sha256": sha256_file(Path(__file__).parent / "features.py"),
            "data_py_sha256": sha256_file(Path(__file__).parent / "data.py"),
            "split_sha256": sha256_file(output / "split_manifest.json")}

def build_features(output: Path, profile: str, allow_lock=False) -> pd.DataFrame:
    archive_path = source_path(output)
    members = inventory(archive_path)
    rows = []
    audit = []
    with zipfile.ZipFile(archive_path) as archive:
        for member in members:
            if member.role == "lock" and not allow_lock:
                audit.append({**asdict(member), "opened": False, "numeric_sha256": None})
                continue
            speed, _, waves = read_member(archive, member, allow_lock=allow_lock)
            audit.append({**asdict(member), "opened": True, "numeric_sha256": numeric_digest(waves)})
            for index, (rpm_hz, wave) in enumerate(zip(speed, waves, strict=True)):
                values = extract(wave, float(rpm_hz), profile)
                rows.append({"member": member.name, "condition": member.condition, "label": member.label,
                             "sequence": member.sequence, "role": member.role, "speed_hz": float(rpm_hz),
                             "speed_index": index, "numeric_sha256": numeric_digest(wave), **values})
            print(f"features: {profile} {member.role} {member.name}", flush=True)
    save_json(output / f"{profile}_access_{'lock' if allow_lock else 'dev'}.json", audit)
    # Preserve failed integrity-check evidence before refusing to fit any model.
    save_json(output / f"{profile}_digests_{'lock' if allow_lock else 'dev'}.json",
              [{key: row[key] for key in META} for row in rows])
    assert_no_crossrole_duplicates(rows)
    frame = pd.DataFrame(rows)
    cache_path = output / f"{profile}_{'lock' if allow_lock else 'dev'}_features.csv"
    frame.to_csv(cache_path, index=False)
    save_json(cache_path.with_suffix(".manifest.json"), {**feature_contract(output,profile),
              "cache_sha256": sha256_file(cache_path), "lock_raw_opened": allow_lock})
    return frame

META = {"member", "condition", "label", "sequence", "role", "speed_hz", "speed_index", "numeric_sha256"}

def matrices(frame: pd.DataFrame, names: list[str], representation: str) -> np.ndarray:
    features = frame[names].to_numpy(dtype=float)
    if representation == "raw":
        return features
    if representation != "normal_relative":
        raise ValueError(representation)
    calibration = frame[frame.role == "calibration"].set_index("speed_index")
    if len(calibration) != 75 or calibration.index.duplicated().any():
        raise ValueError("Exactly one held-out normal calibration waveform per speed is required")
    baseline = calibration.loc[frame.speed_index, names].to_numpy(dtype=float)
    return features - baseline

def models(round_name="round01"):
    base = {
        "centroid": make_pipeline(StandardScaler(), NearestCentroid()),
        "linear_c1": make_pipeline(StandardScaler(), LinearSVC(C=1, class_weight="balanced", max_iter=15000, random_state=SEED)),
        "rbf_c10": make_pipeline(StandardScaler(), SVC(C=10, gamma="scale", class_weight="balanced")),
        "rf": RandomForestClassifier(n_estimators=180, max_features="sqrt", min_samples_leaf=2,
                                      class_weight="balanced", random_state=SEED, n_jobs=2),
        "extra": ExtraTreesClassifier(n_estimators=240, max_features=.7, min_samples_leaf=1,
                                      class_weight="balanced", random_state=SEED, n_jobs=2),
        "histgb": HistGradientBoostingClassifier(max_iter=180, max_leaf_nodes=15, l2_regularization=1,
                                                 learning_rate=.08, random_state=SEED),
    }
    if round_name == "round02":
        # Fixed finite set, evaluated only on development sequences.
        return {"rbf_c100": make_pipeline(StandardScaler(), SVC(C=100, gamma="scale", class_weight="balanced")),
                "rbf_g001": make_pipeline(StandardScaler(), SVC(C=20, gamma=.01, class_weight="balanced")),
                "rbf_g01": make_pipeline(StandardScaler(), SVC(C=20, gamma=.1, class_weight="balanced")),
                "extra_full": ExtraTreesClassifier(n_estimators=300, max_features=1., min_samples_leaf=1,
                                                   class_weight="balanced", random_state=SEED, n_jobs=2),
                "rf_full": RandomForestClassifier(n_estimators=240, max_features=.7, min_samples_leaf=1,
                                                 class_weight="balanced", random_state=SEED, n_jobs=2)}
    return base

def metrics(truth, predicted) -> dict:
    truth, predicted = np.asarray(truth), np.asarray(predicted)
    precision, recall, f1, support = precision_recall_fscore_support(
        truth, predicted, labels=list(CLASSES), zero_division=0)
    matrix = confusion_matrix(truth, predicted, labels=list(CLASSES))
    cls = {label: {"precision": float(precision[i]), "recall": float(recall[i]),
                   "f1": float(f1[i]), "support": int(support[i])} for i, label in enumerate(CLASSES)}
    normal = truth == "normal"
    return {"classes": cls, "confusion": matrix.tolist(), "labels": list(CLASSES),
            "accuracy": float(np.mean(truth == predicted)), "macro_f1": float(f1.mean()),
            "minimum_precision_recall": float(min(precision.min(), recall.min())),
            "normal_false_positive_rate": float(np.mean(predicted[normal] != "normal")) if normal.any() else None,
            "coverage": 1., "n": int(len(truth)),
            "strict_90_all_classes": bool(np.all(precision > .90) and np.all(recall > .90))}

def stratified_cluster_bootstrap(frame: pd.DataFrame, predicted, replicates=1000) -> dict:
    # Whole measurement sequences, stratified by original fault condition. Never a wave-binomial CI.
    working = frame.copy()
    working["predicted"] = predicted
    rng = np.random.default_rng(SEED)
    pools = {key: [group for _, group in subset.groupby("member")]
             for key, subset in working.groupby("condition")}
    keys = [(c, metric) for c in CLASSES for metric in ("precision", "recall")]
    samples = {key: [] for key in keys}
    for _ in range(replicates):
        sampled = pd.concat([groups[i] for groups in pools.values()
                             for i in rng.integers(0, len(groups), size=len(groups))], ignore_index=True)
        result = metrics(sampled.label, sampled.predicted)
        for key in keys:
            samples[key].append(result["classes"][key[0]][key[1]])
    return {"method": "condition-stratified whole-sequence percentile bootstrap",
            "replicates": replicates, "sequence_count_by_condition": {k: len(v) for k,v in pools.items()},
            "warning": "A single sequence per condition gives a degenerate interval; independent reassembly/day is unknown.",
            "intervals_95": {f"{c}.{m}": np.quantile(samples[(c,m)], [.025,.975]).tolist() for c,m in keys}}

def report_predictions(destination: Path, frame: pd.DataFrame, predicted, result: dict):
    prediction = frame[[n for n in frame.columns if n in META]].copy()
    prediction["predicted"] = predicted
    prediction["correct"] = prediction.label == prediction.predicted
    prediction.to_csv(destination / "predictions.csv", index=False)
    by_condition = []
    for condition, group in prediction.groupby("condition"):
        by_condition.append({"condition": condition, "n": len(group), "recall": float(group.correct.mean()),
                             "true_class": group.label.iloc[0], "prediction_counts": dict(Counter(group.predicted))})
    by_speed = []
    # Boundaries fixed a priori; no low-performance band is dropped.
    for lo, hi in ((35,40), (40,45), (45,50)):
        subset = prediction[(prediction.speed_hz >= lo) & (prediction.speed_hz < hi)]
        by_speed.append({"range_hz": [lo,hi], **metrics(subset.label, subset.predicted)})
    save_json(destination / "by_condition.json", by_condition)
    save_json(destination / "by_speed_band.json", by_speed)
    save_json(destination / "by_sequence.json", [{"member": member, "n": len(group), "recall": float(group.correct.mean())}
                                               for member,group in prediction.groupby("member")])
    save_json(destination / "cluster_uncertainty.json", stratified_cluster_bootstrap(frame, predicted))
    save_json(destination / "metrics.json", result)
    fig, ax = plt.subplots(figsize=(7.4,6.6))
    mat = np.asarray(result["confusion"])
    plot = ax.imshow(mat, cmap="Blues")
    for i in range(4):
        for j in range(4):
            ax.text(j, i, str(mat[i,j]), ha="center", va="center", color="white" if mat[i,j] > .6*mat.max() else "black")
    ax.set(xticks=range(4), yticks=range(4), xticklabels=CLASSES, yticklabels=CLASSES,
           xlabel="Predicted state", ylabel="True state", title="All waveforms retained")
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    fig.colorbar(plot, ax=ax)
    fig.tight_layout()
    fig.savefig(destination / "confusion.png", dpi=170)
    plt.close(fig)

def develop(output: Path, profile: str, round_name: str):
    if (output / "freeze.json").exists():
        raise RuntimeError("Frozen evaluation contract: create a new explicitly authorized development branch")
    path = output / profile / round_name
    path.mkdir(parents=True, exist_ok=False)
    cache = output / f"{profile}_dev_features.csv"
    if cache.exists():
        source_path(output)
        cache_manifest = json.loads(cache.with_suffix(".manifest.json").read_text(encoding="utf-8"))
        expected = feature_contract(output,profile)
        if any(cache_manifest.get(k) != v for k,v in expected.items()) or cache_manifest.get("cache_sha256") != sha256_file(cache):
            raise ValueError("Feature cache source/code/split/profile/content provenance mismatch")
        if cache_manifest.get("lock_raw_opened"):
            raise ValueError("Lock-derived feature cache cannot be used for development")
        audit = json.loads((output / f"{profile}_access_dev.json").read_text(encoding="utf-8"))
        if any(row["role"] == "lock" and row["opened"] for row in audit):
            raise ValueError("Development cache consumed locked waveforms")
        frame = pd.read_csv(cache)
        assert_no_crossrole_duplicates(frame.to_dict("records"))
    else:
        frame = build_features(output, profile)
    if set(frame.role) != {"train", "dev", "calibration"}:
        raise ValueError("Unexpected role in development table")
    train = frame.role == "train"
    dev = frame.role == "dev"
    all_names = [n for n in frame.columns if n not in META]
    candidates = []
    specifications = []
    for family in ("base5", "time_order", "spectral", "ar24", "extended"):
        names = feature_names(all_names, family)
        for representation in ("raw", "normal_relative"):
            x = matrices(frame, names, representation)
            for model_name, estimator in models(round_name).items():
                identifier = f"{family}__{representation}__{model_name}"
                estimator.fit(x[train], frame.loc[train, "label"])
                predicted = estimator.predict(x[dev])
                result = metrics(frame.loc[dev, "label"], predicted)
                candidate = {"id": identifier, "family": family, "representation": representation,
                             "model": model_name, "n_features": len(names), **result}
                candidates.append(candidate)
                parameters = {k: repr(v) for k,v in estimator.get_params().items()}
                specifications.append({"id": identifier, "features": names, "parameters": parameters})
                joblib.dump({"estimator": estimator, "features": names, "representation": representation,
                             "profile": profile, "id": identifier}, path / (identifier + ".joblib"))
                print(f"candidate {identifier}: minP/R={result['minimum_precision_recall']:.4f} macroF1={result['macro_f1']:.4f}", flush=True)
    candidates.sort(key=lambda c: (-c["minimum_precision_recall"], -c["macro_f1"], c["n_features"], c["id"]))
    save_json(path / "candidates.json", candidates)
    save_json(path / "model_specifications.json", specifications)
    pd.DataFrame([{k:v for k,v in c.items() if not isinstance(v,(dict,list))} for c in candidates]).to_csv(path / "leaderboard.csv", index=False)
    best = candidates[0]
    artifact = joblib.load(path / (best["id"] + ".joblib"))
    x = matrices(frame, artifact["features"], artifact["representation"])
    predicted = artifact["estimator"].predict(x[dev])
    report_predictions(path, frame.loc[dev], predicted, metrics(frame.loc[dev,"label"], predicted))
    importance = permutation_importance(artifact["estimator"], x[dev], frame.loc[dev,"label"],
                                       scoring="f1_macro", n_repeats=5, random_state=SEED, n_jobs=2)
    pd.DataFrame({"feature": artifact["features"], "dev_permutation_delta_macro_f1": importance.importances_mean,
                  "std": importance.importances_std}).sort_values("dev_permutation_delta_macro_f1", ascending=False).to_csv(path / "feature_importance.csv", index=False)
    save_json(path / "run_manifest.json", {"phase": "development", "profile": profile, "round": round_name,
               "timestamp_utc": datetime.now(timezone.utc).isoformat(), "source_sha256": PINNED["UPATRAS.zip"][2],
               "code_sha256": code_hashes(), "environment": environment(),
               "feature_contract": feature_contract(output,profile), "feature_cache_sha256": sha256_file(cache),
               "candidates_sha256": sha256_file(path / "candidates.json"),
               "model_sha256": {p.name: sha256_file(p) for p in path.glob("*.joblib")},
               "roles": frame.role.value_counts().to_dict(), "best": best, "seed": SEED,
               "selection_rule": "maximize minimum class precision/recall, then macroF1, then fewer features",
               "fit_roles": ["train"], "selection_roles": ["dev"], "lock_raw_opened": False,
               "calibration": "Healthy measurement 01, each matching speed; retrospective timing unknown",
               "independence": "Whole measurement sequences; independent reassembly/day not documented"})
    save_json(output / "latest_development.json", {"profile": profile, "round": round_name, "best": best,
                                                  "path": str(path)})
    return best

def freeze(output: Path, profile: str, round_name: str, candidate: str):
    if (output / "freeze.json").exists():
        raise RuntimeError("Already frozen; preserve the original freeze record")
    path = output / profile / round_name
    run = json.loads((path / "run_manifest.json").read_text(encoding="utf-8"))
    if run["code_sha256"] != code_hashes() or run["feature_contract"] != feature_contract(output,profile):
        raise ValueError("Code/source/split/profile changed since the development run")
    if run["feature_cache_sha256"] != sha256_file(output / f"{profile}_dev_features.csv"):
        raise ValueError("Development feature cache changed")
    if run["candidates_sha256"] != sha256_file(path / "candidates.json"):
        raise ValueError("Development candidate metrics changed")
    candidates = json.loads((path / "candidates.json").read_text(encoding="utf-8"))
    chosen = next(x for x in candidates if x["id"] == candidate)
    if not chosen["strict_90_all_classes"]:
        raise ValueError("Development precision/recall for every class must strictly exceed 90% before freeze")
    model = path / (candidate + ".joblib")
    if run["model_sha256"][model.name] != sha256_file(model):
        raise ValueError("Development model changed")
    contract = {"profile": profile, "round": round_name, "candidate": candidate, "model_path": str(model),
                "model_sha256": sha256_file(model), "source_sha256": PINNED["UPATRAS.zip"][2],
                "code_sha256": code_hashes(), "metrics": chosen,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(), "lock_raw_opened": False}
    save_json(output / "freeze.json", contract)
    return contract

def evaluate_lock(output: Path, authorization: Path):
    freeze_path = output / "freeze.json"
    contract = json.loads(freeze_path.read_text(encoding="utf-8"))
    permission = json.loads(authorization.read_text(encoding="utf-8"))
    if permission.get("phase") != "approve-lock" or permission.get("freeze_sha256") != sha256_file(freeze_path):
        raise PermissionError("Main-agent explicit freeze-hash lock authorization required")
    if (output / "lock_access_started.json").exists():
        raise RuntimeError("Lock already consumed; tuning/repeated certification on this lock is forbidden")
    if contract["code_sha256"] != code_hashes():
        raise ValueError("Code changed since freeze")
    model = Path(contract["model_path"])
    if contract["model_sha256"] != sha256_file(model):
        raise ValueError("Frozen model changed")
    source_path(output)
    marker = {"timestamp_utc": datetime.now(timezone.utc).isoformat(),
                   "freeze_sha256": sha256_file(freeze_path), "authorization_sha256": sha256_file(authorization),
                   "status": "consumed even if process fails"}
    # Exclusive creation serializes concurrent attempts; a crash still consumes the lock.
    with (output / "lock_access_started.json").open("x", encoding="utf-8") as stream:
        json.dump(marker,stream,indent=2)
    artifact = joblib.load(model)
    frame = build_features(output, contract["profile"], allow_lock=True)
    x = matrices(frame, artifact["features"], artifact["representation"])
    lock = frame.role == "lock"
    predicted = artifact["estimator"].predict(x[lock])
    result = metrics(frame.loc[lock, "label"], predicted)
    path = output / contract["profile"] / "lock"
    path.mkdir(exist_ok=False)
    report_predictions(path, frame.loc[lock], predicted, result)
    save_json(path / "evaluation_manifest.json", {"contract": contract, "authorization": permission,
                "result": result, "role_counts": frame.role.value_counts().to_dict(),
                "lock_consumed": True, "hardware_measurement": False})
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "develop", "freeze", "evaluate-lock"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--profile", choices=PROFILES, default="raw1024")
    parser.add_argument("--round", choices=("round01", "round02"), default="round01")
    parser.add_argument("--candidate")
    parser.add_argument("--authorization", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.output)
        save_json(args.output / "split_manifest.json", {"members": [asdict(m) for m in inventory(source_path(args.output))],
                   "classes": list(CLASSES), "split_frozen_before_raw_read": True,
                   "direct_metadata_predictors": False, "all_speeds_retained": True})
        print("Pinned files and sequence-level split verified", flush=True)
    elif args.command == "develop":
        print(json.dumps(develop(args.output,args.profile,args.round),indent=2))
    elif args.command == "freeze":
        if not args.candidate:
            parser.error("--candidate is required")
        print(json.dumps(freeze(args.output,args.profile,args.round,args.candidate),indent=2))
    else:
        if not args.authorization:
            parser.error("--authorization from main is required")
        print(json.dumps(evaluate_lock(args.output,args.authorization),indent=2))

if __name__ == "__main__":
    main()
