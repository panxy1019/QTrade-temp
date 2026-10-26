"""Freeze fitted Qlib state and prove fresh-process inference without refitting."""

from __future__ import annotations

import argparse
import json
import os
import pickle
import re
from datetime import UTC, datetime
from hashlib import sha256
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.atomic_json import write_json_immutable

PACKAGES = ("pyqlib", "lightgbm", "numpy", "pandas", "mlflow")
BUNDLE_FILES = {"params.pkl", "dataset", "reference-features.parquet", "reference-scores.parquet"}


def checksum(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def processor_hash(dataset: Any) -> str:
    handler = dataset.handler
    processors = {key: getattr(handler, key) for key in (
        "infer_processors", "learn_processors", "shared_processors",
    )}
    return sha256(pickle.dumps(processors, protocol=4)).hexdigest()


def freeze(
    root: Path, *, dataset: Any, model: Any, prediction: Any, provider: Path,
    data_sha256: str, source_revision: str, source_snapshot_sha256: str,
) -> dict[str, Any]:
    pd = import_module("pandas")

    if root.exists():
        raise FileExistsError("RESTORE_BUNDLE_ALREADY_EXISTS")
    if (re.fullmatch(r"[a-f0-9]{40}", source_revision) is None or any(
            re.fullmatch(r"[a-f0-9]{64}", value) is None
            for value in (data_sha256, source_snapshot_sha256))):
        raise ValueError("RESTORE_BUNDLE_IDENTITY_REQUIRED")
    dates = prediction.index.get_level_values("datetime").unique().sort_values()[-20:]
    if len(dates) < 2:
        raise ValueError("RESTORE_REFERENCE_REQUIRES_MULTIPLE_SESSIONS")
    reference = prediction[prediction.index.get_level_values("datetime").isin(dates)].to_frame(
        "score")
    features = dataset.prepare("test", col_set="feature", data_key="infer").loc[reference.index]
    if len(features.columns) != 158 or features.empty or not features.index.is_unique:
        raise ValueError("RESTORE_FEATURE_REFERENCE_INVALID")
    root.mkdir(parents=True, exist_ok=False)
    features.to_parquet(root / "reference-features.parquet")
    reference.to_parquet(root / "reference-scores.parquet")
    fitted_hash = processor_hash(dataset)
    # Same contract as official TrainerR._exe_task: fitted processors survive,
    # concrete data frames do not get serialized into the online artifact.
    dataset.config(dump_all=False, recursive=True)
    for name, obj in (("params.pkl", model), ("dataset", dataset)):
        with (root / name).open("xb") as handle:
            pickle.dump(obj, handle, protocol=4)
            handle.flush()
            os.fsync(handle.fileno())
    if processor_hash(dataset) != fitted_hash:
        raise ValueError("PROCESSORS_CHANGED_WHILE_FREEZING")
    for name in BUNDLE_FILES:
        with (root / name).open('rb') as handle:
            os.fsync(handle.fileno())
    provider_files = {p.relative_to(provider).as_posix(): checksum(p)
                      for p in sorted(provider.rglob('*')) if p.is_file()}
    value = signed({
        **AUTHORITY, "schema_version": "qtrade.qlib-restore-bundle.v1",
        "created_at": datetime.now(UTC).isoformat(), "code_revision": source_revision,
        "data_sha256": data_sha256, "source_snapshot_sha256": source_snapshot_sha256,
        "feature_order": [str(c) for c in features.columns],
        "processor_sha256": fitted_hash,
        "reference_start": str(pd.Timestamp(dates[0]).date()),
        "reference_end": str(pd.Timestamp(dates[-1]).date()),
        "reference_rows": len(reference), "packages": {p: version(p) for p in PACKAGES},
        "files": {name: checksum(root / name) for name in sorted(BUNDLE_FILES)},
        "provider_files": provider_files, "state": "AWAITING_INDEPENDENT_RESTORE",
        "historical_pit_certified": False, "model_activated": False,
    })
    write_json_immutable(root / "manifest.json", value)
    return value


def verify_bundle(root: Path, provider: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads((root / "manifest.json").read_text())
    if (signed({k: v for k, v in value.items() if k != "evidence_sha256"}) != value
            or any(value.get(k) != v for k, v in AUTHORITY.items())
            or value.get("schema_version") != "qtrade.qlib-restore-bundle.v1"
            or value.get("model_activated") is not False
            or value.get("historical_pit_certified") is not False
            or set(value["files"]) != BUNDLE_FILES
            or not 1 <= len(value["provider_files"]) <= 30_000
            or len(value["feature_order"]) != 158
            or len(set(value["feature_order"])) != 158):
        raise ValueError("RESTORE_BUNDLE_CONTRACT_INVALID")
    for base, entries in ((root, value["files"]), (provider, value["provider_files"])):
        for name, digest in entries.items():
            path = base / name
            if (path.is_symlink() or not path.resolve().is_relative_to(base.resolve())
                    or not path.is_file() or re.fullmatch(r"[a-f0-9]{64}", digest) is None
                    or checksum(path) != digest):
                raise ValueError("RESTORE_BUNDLE_FILE_MISMATCH")
    actual = {p.relative_to(provider).as_posix() for p in provider.rglob('*') if p.is_file()}
    if actual != set(value["provider_files"]):
        raise ValueError("RESTORE_PROVIDER_HAS_UNBOUND_FILES")
    return value


class FrozenRecorder:
    def __init__(self, root: Path):
        self.root = root

    def load_object(self, name: str) -> Any:
        if name not in {"params.pkl", "dataset"}:
            raise ValueError("RESTORE_OBJECT_NOT_ALLOWLISTED")
        # Only local operator-created, checksum-verified artifacts reach this method.
        with (self.root / name).open('rb') as handle:
            return pickle.load(handle)


def restore(root: Path, provider: Path, *, output: Path) -> dict[str, Any]:
    import numpy as np
    pd = import_module("pandas")
    qlib = import_module("qlib")
    DataHandlerLP = import_module("qlib.data.dataset.handler").DataHandlerLP
    RMDLoader = import_module("qlib.workflow.online.update").RMDLoader

    manifest = verify_bundle(root, provider)
    if {p: version(p) for p in PACKAGES} != manifest['packages']:
        raise ValueError("RESTORE_ENVIRONMENT_CHANGED")
    if version('pyqlib') != '0.9.7':
        raise ValueError("RESTORE_QLIB_VERSION_NOT_AUDITED")
    tracking_uri = (output.parent / 'restore-tracking').resolve().as_uri()
    qlib.init(provider_uri=str(provider), region='cn', kernels=2,
              exp_manager={'class': 'MLflowExpManager', 'module_path': 'qlib.workflow.expm',
                           'kwargs': {'uri': tracking_uri,
                                      'default_exp_name': 'restore-only-no-training'}})
    loader = RMDLoader(FrozenRecorder(root))
    unprepared = loader.rec.load_object('dataset')
    before = processor_hash(unprepared)
    if before != manifest['processor_sha256']:
        raise ValueError("RESTORED_PROCESSOR_STATE_MISMATCH")

    def forbidden_fit(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("REFIT_FORBIDDEN_DURING_RESTORE")

    DataHandlerLP.fit = forbidden_fit
    DataHandlerLP.fit_process_data = forbidden_fit
    data = loader.get_dataset(manifest['reference_start'], manifest['reference_end'],
                              unprepared_dataset=unprepared)
    model = loader.get_model()
    features = data.prepare('test', col_set='feature', data_key='infer')
    scores = model.predict(data, segment='test').to_frame('score')
    reference_features = pd.read_parquet(root / 'reference-features.parquet')
    reference_scores = pd.read_parquet(root / 'reference-scores.parquet')
    if (features.columns.tolist() != reference_features.columns.tolist()
            or not features.index.equals(reference_features.index)
            or not scores.index.equals(reference_scores.index)
            or scores.columns.tolist() != reference_scores.columns.tolist()
            or len(scores) != manifest['reference_rows']):
        raise ValueError("RESTORE_FEATURE_OR_SCORE_IDENTITY_MISMATCH")
    after = processor_hash(data)
    if after != before:
        raise ValueError("RESTORE_MUTATED_FITTED_PROCESSORS")
    if not np.allclose(features, reference_features, equal_nan=True, atol=1e-12, rtol=1e-12):
        raise ValueError("RESTORE_FEATURE_VALUES_CHANGED")
    if (not np.isfinite(scores.to_numpy()).all() or not np.allclose(
            scores, reference_scores, equal_nan=False, atol=1e-10, rtol=1e-10)):
        raise ValueError("RESTORE_PREDICTION_VALUES_CHANGED")
    result = signed({
        **AUTHORITY, 'schema_version': 'qtrade.qlib-independent-restore.v1',
        'state': 'PARITY_VERIFIED_NOT_ACTIVATED', 'verified_at': datetime.now(UTC).isoformat(),
        'bundle_sha256': manifest['evidence_sha256'], 'pid': os.getpid(),
        'feature_count': len(features.columns), 'reference_rows': len(scores),
        'reference_start': manifest['reference_start'], 'reference_end': manifest['reference_end'],
        'max_absolute_score_difference': float(np.abs(scores-reference_scores).to_numpy().max()),
        'fitted_processors_unchanged': before == after,
        'fit_methods_forbidden': True, 'official_loader': 'qlib.workflow.online.update.RMDLoader',
        'handler_init_type': 'DataHandlerLP.IT_LS', 'model_activated': False,
        'limitations': ['FROZEN_DATA_RESTORE_ONLY_NOT_NEW_DAY_PREDICTION',
                        'CURRENT_UNIVERSE_NON_PIT', 'MANUAL_ACTIVATION_STILL_REQUIRED'],
    })
    write_json_immutable(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--provider', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
        raise RuntimeError('LIVE_TRADING_MUST_REMAIN_DISABLED')
    print(json.dumps(restore(args.bundle, args.provider, output=args.output)))


if __name__ == '__main__':
    main()
