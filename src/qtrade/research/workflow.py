"""Bounded Qlib experiment contracts, independent of model/runtime dependencies."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Any, cast

from qtrade.monitoring.data_freshness import AUTHORITY, signed

SCHEMA = "qtrade.research-workflow.v1"
DATASET_ID = "daily-liquid-990-20261023"
MODELS = ("linear", "lightgbm")
DEFAULT_SPLITS = {
    "train": ["2017-04-05", "2021-12-28"],
    "valid": ["2022-01-04", "2023-12-26"],
    "test": ["2024-01-02", "2026-10-23"],
}
LIMITATIONS = [
    "CURRENT_LIQUIDITY_UNIVERSE_SURVIVORSHIP_AND_SELECTION_BIAS",
    "POST_HOC_VIEWED_HISTORY_NOT_UNSEEN_FORWARD_EVIDENCE",
    "HISTORICAL_ST_AND_EXACT_LIMIT_PRICES_NOT_FULLY_CERTIFIED",
    "MISSING_QUOTE_NOT_CERTIFIED_SUSPENSION_REASON",
    "VENDOR_ADJUSTMENTS_NOT_INDEPENDENT_CORPORATE_ACTION_RECONCILIATION",
    "OPEN_PRICE_AND_PREVIOUS_VOLUME_CAP_NOT_AUCTION_REPLAY",
]


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def validate_spec(value: dict[str, Any]) -> dict[str, Any]:
    """Fail closed on arbitrary code, paths, unknown knobs and invalid time splits."""
    required = {
        "name", "dataset_id", "model", "features", "segments", "topk", "n_drop",
        "hold_thresh", "risk_degree", "buy_cost_bps", "sell_cost_bps",
        "min_cost", "initial_cash", "seed", "universe_policy",
    }
    if set(value) - {"source_snapshot_sha256"} != required:
        raise ValueError("SPEC_FIELDS_MISMATCH")
    if "source_snapshot_sha256" in value and (
        not isinstance(value["source_snapshot_sha256"], str)
        or re.fullmatch(r"[a-f0-9]{64}", value["source_snapshot_sha256"]) is None
    ):
        raise ValueError("INVALID_SOURCE_SNAPSHOT_IDENTITY")
    if not isinstance(value["name"], str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,59}", value["name"]
    ):
        raise ValueError("SAFE_EXPERIMENT_NAME_REQUIRED")
    if value["dataset_id"] != DATASET_ID or value["features"] != "Alpha158":
        raise ValueError("UNSUPPORTED_DATASET_OR_FEATURES")
    if value["model"] not in MODELS or value["universe_policy"] != "supported_a_share":
        raise ValueError("UNSUPPORTED_MODEL_OR_UNIVERSE_POLICY")
    ranges = {
        "topk": (10, 100), "n_drop": (1, 20), "hold_thresh": (2, 20),
        "seed": (0, 2**31 - 1),
    }
    for key, (lower, upper) in ranges.items():
        if type(value[key]) is not int or not lower <= value[key] <= upper:
            raise ValueError("INVALID_" + key.upper())
    if value["n_drop"] > value["topk"]:
        raise ValueError("DROP_EXCEEDS_TOPK")
    for key, lower_float, upper_float in (
        ("risk_degree", 0.1, 0.95), ("buy_cost_bps", 1, 100),
        ("sell_cost_bps", 1, 100), ("min_cost", 0, 100),
        ("initial_cash", 100_000, 100_000_000),
    ):
        if type(value[key]) not in (float, int) or not lower_float <= value[key] <= upper_float:
            raise ValueError("INVALID_" + key.upper())
    segments = value["segments"]
    if not isinstance(segments, dict) or set(segments) != {"train", "valid", "test"}:
        raise ValueError("INVALID_SEGMENTS")
    for pair in segments.values():
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("INVALID_SEGMENT_RANGE")
        if any(not isinstance(day, str) or date.fromisoformat(day).isoformat() != day
               for day in pair):
            raise ValueError("INVALID_SEGMENT_DATE")
        if pair[0] > pair[1]:
            raise ValueError("REVERSED_SEGMENT")
    if not (
        "2017-04-05" <= segments["train"][0] < segments["train"][1]
        < segments["valid"][0] < segments["valid"][1]
        < segments["test"][0] < segments["test"][1] <= "2026-10-23"
    ):
        raise ValueError("OVERLAPPING_OR_OUT_OF_DATA_SEGMENTS")
    # At least seven calendar days for the two-future-session label. The runner
    # additionally checks the actual exchange calendar, including long holidays.
    for left, right in (("train", "valid"), ("valid", "test")):
        if (date.fromisoformat(segments[right][0])
                - date.fromisoformat(segments[left][1])).days < 7:
            raise ValueError("LABEL_PURGE_GAP_REQUIRED")
    return cast(dict[str, Any], json.loads(canonical(value)))


def default_spec(model: str = "lightgbm") -> dict[str, Any]:
    return {
        "name": "alpha158-" + model, "dataset_id": DATASET_ID, "model": model,
        "features": "Alpha158", "segments": json.loads(canonical(DEFAULT_SPLITS)),
        "topk": 50, "n_drop": 5, "hold_thresh": 2, "risk_degree": 0.95,
        "buy_cost_bps": 8, "sell_cost_bps": 13, "min_cost": 5,
        "initial_cash": 10_000_000, "seed": 20261026,
        "universe_policy": "supported_a_share",
    }


def build_config(spec: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    spec = validate_spec(spec)
    config: dict[str, Any] = json.loads(canonical(baseline))
    config["name"] = spec["name"]
    config["seed"] = spec["seed"]
    config["segments"] = spec["segments"]
    config["account"] = spec["initial_cash"]
    config["portfolio"].update({key: spec[key] for key in (
        "topk", "n_drop", "hold_thresh", "risk_degree"
    )})
    config["exchange"].update(
        open_cost=spec["buy_cost_bps"] / 10000,
        close_cost=spec["sell_cost_bps"] / 10000,
        min_cost=spec["min_cost"],
    )
    if spec["model"] == "linear":
        config["model"] = {
            "class": "LinearModel", "module_path": "qlib.contrib.model.linear",
            "kwargs": {"estimator": "ridge", "alpha": 1.0},
        }
        config["fit"] = {}
    else:
        for key in ("seed", "feature_fraction_seed", "bagging_seed"):
            config["model"]["kwargs"][key] = spec["seed"]
    config["workflow_spec"] = spec
    config["universe_policy"] = "supported_a_share"
    config["source_constraint_policy"] = "RECONCILED_BACKFILL_ADDITIONAL_BLOCKS_V1"
    config["infer_processors"] = [
        {"class": "ProcessInf"},
        {"class": "RobustZScoreNorm", "kwargs": {
            "fields_group": "feature", "clip_outlier": True,
        }},
        {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
    ]
    config["cost_description"] = (
        f"Buy {spec['buy_cost_bps']}bp / sell {spec['sell_cost_bps']}bp, "
        f"minimum CNY{spec['min_cost']}; fixed commission/slippage proxy, "
        "no nonlinear impact or auction replay."
    )
    config["limitations"] += [
        "COMMON_TRAIN_FITTED_FEATURE_NORMALIZATION_AND_FILLNA",
        "STAR_EXCLUDED_FROM_TRAINING_RANKING_AND_EXECUTION",
    ]
    return config


def data_audit(*, aligned: bool = True) -> dict[str, Any]:
    """Coverage is evidence, never inferred from the experiment finishing."""
    return {
        "universe": {
            "status": "BIASED_CURRENT_SAMPLE",
            "historical_pit_certified": False,
            "selection": "2026-10-23 liquidity selection projected into history",
        },
        "corporate_actions": {"status": "VENDOR_EFFECTIVE_DATE_NOT_RECONCILED"},
        "historical_st_and_halts": {"status": "PARTIAL_OR_UNVERIFIED"},
        "exact_price_limits": {"status": "UNVERIFIED_CONSERVATIVE_PROXY"},
        "execution": {"status": "DAILY_OPEN_PROXY_WITH_CAUSAL_VOLUME_CAP"},
        "supported_universe": {
            "status": ("TRAIN_RANK_EXECUTION_ALIGNED_EXCLUDING_STAR" if aligned
                       else "LEGACY_STAR_TRAINING_EXECUTION_MISMATCH"),
        },
        "limitations": LIMITATIONS,
        "promotion": "NOT_ELIGIBLE",
    }


def capabilities() -> dict[str, Any]:
    return {
        "models": [
            {"id": "linear", "name": "Ridge 线性模型", "parameters": {"alpha": 1.0}},
            {"id": "lightgbm", "name": "LightGBM", "parameters": {
                "learning_rate": 0.05, "num_leaves": 31, "max_depth": 6,
                "max_rounds": 300, "early_stopping_rounds": 30,
            }},
        ],
        "feature_sets": ["Alpha158"],
        "portfolio_methods": ["TopkDropout"],
        "datasets": [{
            "id": DATASET_ID, "symbols": 990, "rows": 2157805,
            "start": "2016-10-24", "end": "2026-10-23",
            "status": "FROZEN_RESEARCH_DATASET", "audit": data_audit(),
        }],
        "default_spec": default_spec(),
        "default_linear_spec": default_spec("linear"),
    }


def catalog_envelope(
    *, published_at: str, experiments: list[dict[str, Any]], worker: dict[str, Any],
    source_snapshot_sha256: str | None = None,
) -> dict[str, Any]:
    available = capabilities()
    if source_snapshot_sha256 is not None:
        for key in ("default_spec", "default_linear_spec"):
            available[key]["source_snapshot_sha256"] = source_snapshot_sha256
    return signed({
        **AUTHORITY, "schema_version": SCHEMA, "published_at": published_at,
        **available, "experiments": experiments, "worker": worker,
        "source_snapshot_sha256": source_snapshot_sha256,
    })
