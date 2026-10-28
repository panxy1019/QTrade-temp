"""Replay captured tiny native metrics against the existing local Decimal API."""
from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from qtrade.research.metrics import FactorObservation, evaluate_factor, evaluate_factor_periods


def verify(capture: Path) -> dict:
    value = json.loads(capture.read_bytes())
    assert value["pyqlib"] == "0.9.7"
    assert all(value["checks"].values())
    assert all(count == 0 for count in value["guard_counts"].values())
    assert not value["audit"]["blocked"]
    descriptors = value["alpha158_descriptors"]
    assert len(descriptors) == len({row["name"] for row in descriptors}) == 158
    canonical = json.dumps(descriptors, sort_keys=True, separators=(",", ":")).encode()
    assert sha256(canonical).hexdigest() == value["alpha158_descriptor_sha256"]
    rows = []
    period_observations = []
    maximum_error = Decimal(0)
    for record in value["synthetic_ic"]:
        pairs = [(symbol, Decimal(str(score)), Decimal(str(label)))
                 for symbol, score, label in zip("ABCDEF", record["scores"], record["labels"],
                                                 strict=True)
                 if score is not None and label is not None]
        assert len(pairs) == record["valid_pairs"]
        metric = evaluate_factor([score for _, score, _ in pairs],
                                 [label for _, _, label in pairs], quantiles=3)
        if record["ic"] is None:
            assert record["rank_ic"] is None and metric.ic == metric.rank_ic == 0
            rows.append({"day": record["day"], "classification": "EXPLICIT_UNDEFINED_DIFFERENCE",
                         "native_ic": None, "qtrade_ic": "0"})
        else:
            ic_error = abs(metric.ic - Decimal(str(record["ic"])))
            ric_error = abs(metric.rank_ic - Decimal(str(record["rank_ic"])))
            maximum_error = max(maximum_error, ic_error, ric_error)
            assert max(ic_error, ric_error) < Decimal("1e-12")
            rows.append({"day": record["day"], "classification": "PAIRED_NUMERIC_MATCH",
                         "ic_error": str(ic_error), "rank_ic_error": str(ric_error),
                         "valid_pairs": len(pairs)})
        if len(period_observations) < 2:
            day = date.fromisoformat(record["day"])
            period_observations.append([
                FactorObservation(symbol, score, label, day,
                                  datetime.combine(day, datetime.min.time(), UTC))
                for symbol, score, label in pairs
            ])
    metrics = evaluate_factor_periods(period_observations, quantiles=3)
    ddof0 = Decimal(str(value["synthetic_summary"]["icir_ddof0"]))
    ddof1 = Decimal(str(value["synthetic_summary"]["icir_ddof1"]))
    assert abs(metrics.icir - ddof0) < Decimal("1e-12")
    assert abs(ddof0 - ddof1) > Decimal(".001")
    direct_refs = [int(offset) for descriptor in descriptors
                   for offset in re.findall(r"Ref\(\$[a-z]+,\s*(-?\d+)\)", descriptor["formula"])]
    assert direct_refs and min(direct_refs) >= 0
    return {
        "schema_version": "qtrade.step2-native-capture-replay.v1",
        "capture_sha256": sha256(capture.read_bytes()).hexdigest(),
        "local_metrics_source_sha256": sha256(Path(
            "src/qtrade/research/metrics.py").read_bytes()).hexdigest(),
        "alpha158_descriptor_sha256": value["alpha158_descriptor_sha256"],
        "descriptor_count": 158,
        "raw_fields": sorted({field for row in descriptors for field in row["raw_fields"]}),
        "direct_ref_offsets": sorted(set(direct_refs)),
        "direct_ref_audit_is_not_full_dependency_window_certification": True,
        "comparison_rows": rows, "maximum_ic_or_rank_ic_absolute_error": str(maximum_error),
        "qtrade_icir_ddof0": str(metrics.icir), "native_sigana_source_policy_ddof1": str(ddof1),
        "all_checks_passed": True,
        "scope": "SYNTHETIC_METRIC_REPLAY_NOT_FACTOR_OR_PRODUCTION_ACCEPTANCE",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    encoded = json.dumps(verify(args.capture), indent=2, sort_keys=True, allow_nan=False)
    if args.output:
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    print(encoded)
