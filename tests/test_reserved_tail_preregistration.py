import json
from dataclasses import asdict
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from qtrade.runtime import MinuteOverlayPolicy

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREREGISTRATION = (
    PROJECT_ROOT
    / "docs/preregistrations/baostock-reserved-tail-high-exposure-v1.json"
)
DATASET_MANIFEST = (
    PROJECT_ROOT / "deployment/evidence/long-five-minute-market-data/manifest.json"
)
PRIOR_REPORT = (
    PROJECT_ROOT / "deployment/evidence/long-five-minute-walk-forward/report.json"
)


def _canonical_sha256(payload: dict[str, object]) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def test_reserved_tail_preregistration_is_signed_and_identity_bound() -> None:
    preregistration = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    dataset = json.loads(DATASET_MANIFEST.read_text(encoding="utf-8"))
    prior_report = json.loads(PRIOR_REPORT.read_text(encoding="utf-8"))
    unsigned = {
        key: value
        for key, value in preregistration.items()
        if key != "preregistration_sha256"
    }

    assert _canonical_sha256(unsigned) == preregistration["preregistration_sha256"]
    assert preregistration["status"] == "LOCKED_BEFORE_RESERVED_TAIL_ENGINE_READ"
    assert preregistration["dataset"]["checksum"] == dataset["manifest"]["checksum"]
    assert preregistration["dataset"]["evidence_sha256"] == dataset["evidence_sha256"]
    assert (
        preregistration["prior_evidence"]["evidence_sha256"]
        == prior_report["evidence_sha256"]
    )
    assert preregistration["evaluation_lock"]["reserved_tail_days"] == dataset[
        "trading_days"
    ][-8:]
    assert preregistration["evaluation_lock"]["selection_after_tail_read"] is False
    assert preregistration["interpretation_lock"]["promotion"] == "NOT_ELIGIBLE"
    assert (
        preregistration["causal_contract"]["external_order_transmission"] is False
    )

    parameters = preregistration["candidate"]["parameters"]
    policy = MinuteOverlayPolicy(
        short_window=parameters["short_window"],
        long_window=parameters["long_window"],
        volatility_window=parameters["volatility_window"],
        maximum_minute_volatility=Decimal(parameters["maximum_minute_volatility"]),
        maximum_intraday_drawdown=Decimal(parameters["maximum_intraday_drawdown"]),
        reduction_multiplier=Decimal(parameters["reduction_multiplier"]),
        cooldown_bars=parameters["cooldown_bars"],
    )
    policy_payload = {key: str(value) for key, value in asdict(policy).items()}
    assert _canonical_sha256(
        {
            "policy_id": preregistration["candidate"]["policy_id"],
            "policy": policy_payload,
        }
    ) == preregistration["candidate"]["policy_hash"]
