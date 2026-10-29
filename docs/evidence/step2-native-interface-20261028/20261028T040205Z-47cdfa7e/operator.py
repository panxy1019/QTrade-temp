"""Pinned Qlib source/descriptor and synthetic IC audit; no provider or training.

Run through qlib-env/bin/python -B - with this file on stdin. All source input
is installed public package code. No external formulas or datasets are accepted.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone

TASK_VOLUME = Path("/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade")
ENV_ROOT = TASK_VOLUME / "qlib-env"
audit = {"blocked": [], "source_reads": set(), "null_sink_open_count": 0}


def hook(event, args):
    if event == "open":
        path, mode, flags = args
        writing = (isinstance(mode, str) and any(x in mode for x in "wax+")) or (
            isinstance(flags, int) and flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
            )
        )
        if writing and path == os.devnull:
            # dill imports inspect Python file types with the kernel null sink.
            audit["null_sink_open_count"] += 1
            return
        if writing:
            audit["blocked"].append({"event": event, "kind": "WRITE"})
            raise PermissionError("NATIVE_AUDIT_FILE_WRITES_DISABLED")
        if isinstance(path, (str, bytes)):
            path = Path(os.fsdecode(path)).resolve()
            if path.is_relative_to(TASK_VOLUME) and not path.is_relative_to(ENV_ROOT):
                audit["blocked"].append({"event": event, "kind": "PRODUCTION_READ"})
                raise PermissionError("NATIVE_AUDIT_PRODUCTION_INPUT_READ_DISABLED")
            if str(path).startswith("/root/") and not path.is_relative_to(ENV_ROOT):
                audit["blocked"].append({"event": event, "kind": "OTHER_ROOT_READ"})
                raise PermissionError("NATIVE_AUDIT_OTHER_ROOT_READ_DISABLED")
            if path.is_relative_to(ENV_ROOT):
                audit["source_reads"].add(str(path.relative_to(ENV_ROOT)))
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system"}:
        audit["blocked"].append({"event": event, "kind": "NETWORK_OR_PROCESS"})
        raise PermissionError("NATIVE_AUDIT_NETWORK_AND_SUBPROCESS_DISABLED")


sys.addaudithook(hook)
# Avoid tempfile's write-probe during dill import; audit still forbids real writes.
tempfile.tempdir = "/tmp"
assert shutil.disk_usage(TASK_VOLUME).free >= 100 * 2**30
dist = importlib.metadata.distribution("pyqlib")
assert dist.version == "0.9.7"

import builtins
import numpy as np
import pandas as pd
import scipy.stats  # Resolve lazy Spearman dependency before expression guard.
import qlib
from qlib.contrib.data.handler import Alpha158
from qlib.contrib.data.loader import Alpha158DL
from qlib.contrib.eva.alpha import calc_ic
from qlib.data.data import BaseProvider, ExpressionProvider
from qlib.data.dataset.handler import DataHandlerLP
from qlib.data.dataset.loader import QlibDataLoader
from qlib.workflow.record_temp import SigAnaRecord

import_blocked_attempts = list(audit["blocked"])
audit["blocked"].clear()

targets = {
    "Alpha158.get_feature_config": Alpha158.get_feature_config,
    "Alpha158DL.get_feature_config": Alpha158DL.get_feature_config,
    "calc_ic": calc_ic,
    "SigAnaRecord": SigAnaRecord,
    "SigAnaRecord._generate": SigAnaRecord._generate,
    "ExpressionProvider.get_expression_instance": ExpressionProvider.get_expression_instance,
}
signatures = {name: str(inspect.signature(value)) for name, value in targets.items()}
source_paths = [
    "qlib/contrib/data/handler.py", "qlib/contrib/data/loader.py", "qlib/data/ops.py",
    "qlib/contrib/eva/alpha.py", "qlib/workflow/record_temp.py", "qlib/data/data.py",
    "qlib/data/dataset/handler.py", "qlib/data/dataset/loader.py",
]
source_hashes = {}
for relative in source_paths:
    source_hashes[relative] = hashlib.sha256(dist.locate_file(relative).read_bytes()).hexdigest()
excerpts = {name: inspect.getsource(value) for name, value in targets.items()}

guard_counts = {}


def denied(name):
    guard_counts[name] = 0

    def fail(*args, **kwargs):
        guard_counts[name] += 1
        raise RuntimeError("NATIVE_AUDIT_FORBIDDEN_CALL_" + name)

    return fail


qlib.init = denied("qlib.init")
DataHandlerLP.__init__ = denied("DataHandlerLP.__init__")
QlibDataLoader.load = denied("QlibDataLoader.load")
BaseProvider.features = denied("BaseProvider.features")
ExpressionProvider.get_expression_instance = denied("ExpressionProvider.get_expression_instance")
builtins.eval = denied("builtins.eval")

fields, names = Alpha158DL.get_feature_config()
handler_fields, handler_names = Alpha158.get_feature_config(None)
assert fields == handler_fields and names == handler_names
assert len(fields) == len(names) == len(set(names)) == 158
descriptors = [{"ordinal": i, "name": name, "formula": field,
                "raw_fields": sorted(set(re.findall(r"\$([A-Za-z_]+)", field)))}
               for i, (name, field) in enumerate(zip(names, fields))]
descriptor_bytes = json.dumps(descriptors, sort_keys=True, separators=(",", ":")).encode()

dates = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"])
instruments = list("ABCDEF")
index = pd.MultiIndex.from_product([dates, instruments], names=["datetime", "instrument"])
score_rows = [[1, 1, 2, 3, 5, 8], [6, 5, 4, 3, 2, 1], [1, 1, 1, 1, 1, 1],
              [1, 2, np.nan, 4, 5, 6]]
label_rows = [[2, 0, 1, 3, 6, 5], [1, 3, 2, 5, 4, 6], [1, 2, 3, 4, 5, 6],
              [6, 5, 4, 3, np.nan, 1]]
scores = pd.Series(np.array(score_rows).reshape(-1), index=index, name="score")
labels = pd.Series(np.array(label_rows).reshape(-1), index=index, name="label")
ic, ric = calc_ic(scores, labels)
drop_ic, drop_ric = calc_ic(scores, labels, dropna=True)
reordered_ic, reordered_ric = calc_ic(scores.iloc[::-1], labels)
assert np.allclose(ic.to_numpy(), reordered_ic.to_numpy(), equal_nan=True, atol=1e-14)
assert np.allclose(ric.to_numpy(), reordered_ric.to_numpy(), equal_nan=True, atol=1e-14)
assert pd.isna(ic.iloc[2]) and pd.isna(ric.iloc[2])
assert len(drop_ic) == len(drop_ric) == 3


def finite(value):
    return None if pd.isna(value) or not np.isfinite(value) else float(value)


results = []
for i, day in enumerate(dates):
    paired = pd.concat([scores.xs(day), labels.xs(day)], axis=1).dropna()
    results.append({"day": str(day.date()), "scores": [finite(x) for x in score_rows[i]],
                    "labels": [finite(x) for x in label_rows[i]], "valid_pairs": len(paired),
                    "ic": finite(ic.loc[day]), "rank_ic": finite(ric.loc[day])})
nonconstant = ic.iloc[:2]
summary = {
    "ic_mean": finite(nonconstant.mean()), "ic_std_ddof0": finite(nonconstant.std(ddof=0)),
    "ic_std_ddof1": finite(nonconstant.std(ddof=1)),
    "icir_ddof0": finite(nonconstant.mean() / nonconstant.std(ddof=0)),
    "icir_ddof1": finite(nonconstant.mean() / nonconstant.std(ddof=1)),
    "sigana_policy_from_source": "mean/std(), pandas default ddof=1; ICIR unannualized",
}
assert not audit["blocked"]
assert all(count == 0 for count in guard_counts.values())
out = {
    "schema_version": "qtrade.step2-native-interface-audit.v1",
    "observed_at": datetime.now(timezone.utc).isoformat(),
    "scope": "INSTALLED_SOURCE_DESCRIPTORS_AND_SYNTHETIC_IC_ONLY",
    "python": sys.version, "pyqlib": dist.version,
    "packages": {name: importlib.metadata.version(name) for name in
                 ("pandas", "numpy", "scipy", "pyqlib")},
    "free_gib": shutil.disk_usage(TASK_VOLUME).free / 2**30,
    "source_hashes": source_hashes, "signatures": signatures, "source_excerpts": excerpts,
    "alpha158_descriptors": descriptors,
    "alpha158_descriptor_sha256": hashlib.sha256(descriptor_bytes).hexdigest(),
    "alpha158_handler_and_loader_equal": True,
    "synthetic_ic": results, "synthetic_summary": summary,
    "checks": {"descriptor_count_unique_158": True, "constant_is_nan": True,
               "dropna_removes_constant_day": True, "index_reorder_invariant": True,
               "forbidden_calls_zero": True, "no_persistent_file_writes": True,
               "no_production_input_reads": True, "no_network_or_subprocess": True},
    "guard_counts": guard_counts,
    "audit": {"blocked": audit["blocked"], "installed_env_read_file_count": len(audit["source_reads"]),
              "null_sink_open_count": audit["null_sink_open_count"],
              "import_blocked_attempts": import_blocked_attempts},
    "live_trading_enabled": False, "external_order_transmission": False,
    "limitations": ["NO_DATA_PROVIDER_OR_ALPHA158_NUMERIC_EVALUATION",
                    "NO_FACTOR_REGISTRY_OR_TASK_IMPLEMENTATION",
                    "NO_PRODUCTION_OR_PROSPECTIVE_RESEARCH_ACCEPTANCE"],
}
print(json.dumps(out, sort_keys=True, allow_nan=False))
