from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from secrets import compare_digest
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from qtrade.accounting import Ledger
from qtrade.audit import AuditLog
from qtrade.control import PlatformRuntime
from qtrade.domain import CancelCommand, Order, RuntimeMode
from qtrade.execution import FeeSchedule, PaperGateway
from qtrade.monitoring import MetricsRegistry
from qtrade.oms import OrderManager
from qtrade.risk import RiskEngine, RiskLimits

_MAX_MARKET_PARQUET_BYTES = 64 * 1024 * 1024
_MAX_MARKET_PARQUET_ROWS = 2_000_000
_MAX_MARKET_PARQUET_ROW_GROUPS = 1024
_MAX_MARKET_PARQUET_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
_MAX_HISTORICAL_SERIES_ROWS = 2000


class Role(StrEnum):
    READ = "read"
    CONTROL = "control"
    TRADE = "trade"


class ControlRequest(BaseModel):
    reason: str = "manual control"


class CancelRequest(BaseModel):
    internal_order_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ApiAuthConfig:
    control_token: str | None = None
    trade_token: str | None = None

    @classmethod
    def from_environment(cls) -> ApiAuthConfig:
        import os

        return cls(
            control_token=os.getenv("QTRADE_CONTROL_TOKEN"),
            trade_token=os.getenv("QTRADE_TRADE_TOKEN"),
        )


@dataclass(slots=True)
class AppContext:
    runtime: PlatformRuntime
    metrics: MetricsRegistry
    oms: OrderManager
    ledger: Ledger
    audit: AuditLog
    recover: Callable[[str], None] | None = None
    cancel: Callable[[CancelCommand], Order] | None = None
    daily_report_root: Path | None = None
    research_comparison_path: Path | None = None
    walk_forward_report_path: Path | None = None
    robustness_report_path: Path | None = None
    research_artifact_root: Path | None = None
    historical_manifest_path: Path | None = None


def bootstrap_context(mode: RuntimeMode = RuntimeMode.PAPER) -> AppContext:
    audit = AuditLog()
    metrics = MetricsRegistry()
    ledger = Ledger(opening_cash=Decimal("0"), audit=audit)
    oms = OrderManager(audit)
    risk = RiskEngine(
        RiskLimits(
            max_order_value=Decimal("0"),
            max_position_quantity=0,
            max_gross_exposure=Decimal("0"),
        ),
        audit,
    )
    PaperGateway(oms, ledger, audit, FeeSchedule("bootstrap", Decimal("0"), Decimal("0")))
    return AppContext(PlatformRuntime(mode, risk, audit, metrics), metrics, oms, ledger, audit)


def _require_role(required: Role, auth: ApiAuthConfig) -> Callable[[str | None], str]:
    def dependency(authorization: str | None = Header(default=None)) -> str:
        if required is Role.READ:
            return Role.READ.value
        expected = auth.trade_token if required is Role.TRADE else auth.control_token
        if expected is None:
            raise HTTPException(status_code=503, detail="control authentication is not configured")
        supplied = ""
        if authorization is not None and authorization.startswith("Bearer "):
            supplied = authorization.removeprefix("Bearer ")
        if not supplied or not compare_digest(supplied, expected):
            raise HTTPException(status_code=403, detail="invalid credentials")
        return required.value

    return dependency


def create_app(context: AppContext | None = None, auth: ApiAuthConfig | None = None) -> FastAPI:
    state = context or bootstrap_context()
    auth_config = auth or ApiAuthConfig.from_environment()
    app = FastAPI(title="Q Control Plane", version="0.1.0")
    web_dist = Path(__file__).parents[3] / "web" / "dist"
    if (web_dist / "assets").is_dir():
        app.mount("/web/assets", StaticFiles(directory=web_dist / "assets"), name="web-assets")

    @app.get("/health")
    def health(_: str = Depends(_require_role(Role.READ, auth_config))) -> dict[str, Any]:
        return asdict(state.runtime.health())

    @app.get("/system")
    def system(_: str = Depends(_require_role(Role.READ, auth_config))) -> dict[str, Any]:
        return asdict(state.runtime.status)

    @app.get("/risk")
    def risk(_: str = Depends(_require_role(Role.READ, auth_config))) -> dict[str, str]:
        return {"state": state.runtime.status.risk_state.value}

    @app.get("/orders")
    def orders(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> list[dict[str, object]]:
        return [
            {
                "internal_order_id": order.internal_order_id,
                "symbol": order.request.symbol,
                "status": order.status.value,
                "quantity": order.request.quantity,
                "filled_quantity": order.filled_quantity,
            }
            for order in state.oms.orders
        ]

    @app.get("/positions")
    def positions(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> list[dict[str, Any]]:
        return [asdict(position) for position in state.ledger.account.positions.values()]

    @app.get("/account")
    def account(_: str = Depends(_require_role(Role.READ, auth_config))) -> dict[str, str]:
        return {"cash": str(state.ledger.account.cash)}

    @app.get("/metrics")
    def metrics(_: str = Depends(_require_role(Role.READ, auth_config))) -> dict[str, object]:
        return state.metrics.snapshot()

    @app.get("/market")
    def market(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_market_dataset(state.historical_manifest_path)
        return {
            "provider_connected": False,
            "execution_gateway_connected": state.metrics.gateway_connected,
            "mode": state.runtime.status.mode,
            "snapshot_readiness": False,
            "historical": dataset,
        }

    @app.get("/instruments")
    def instruments(_: str = Depends(_require_role(Role.READ, auth_config))) -> list[object]:
        dataset = _load_verified_market_dataset(state.historical_manifest_path)
        if dataset is None:
            return []
        universe = dataset.get("universe")
        if not isinstance(universe, dict):
            return []
        symbols = universe.get("symbols")
        if not isinstance(symbols, list):
            return []
        return [
            {
                "symbol": symbol,
                "security_type": "ETF",
                "provider": universe.get("provider"),
                "point_in_time": universe.get("point_in_time"),
                "data_version": dataset.get("data_version"),
            }
            for symbol in symbols
            if isinstance(symbol, str)
        ]

    @app.get("/historical-bars/latest")
    def latest_historical_bars(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> list[dict[str, str]]:
        dataset = _load_verified_market_dataset(state.historical_manifest_path)
        if dataset is None:
            return []
        return _load_latest_historical_bars(
            state.historical_manifest_path,
            data_version=str(dataset["data_version"]),
            expected_checksum=str(dataset["checksum"]),
            expected_universe=dataset["universe"],
            expected_bar_count=dataset["bar_count"],
            expected_observed_end=dataset["observed_end"],
        )

    @app.get("/historical-bars/{symbol}")
    def historical_bar_series(
        symbol: str,
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_market_dataset(state.historical_manifest_path)
        empty: dict[str, object] = {
            "symbol": symbol,
            "data_version": None,
            "dataset_checksum": None,
            "observation_type": "HISTORICAL_DAILY_BAR_SERIES",
            "total_count": 0,
            "truncated": False,
            "bars": [],
        }
        if dataset is None:
            return empty
        series = _load_historical_bar_series(
            state.historical_manifest_path,
            symbol=symbol,
            data_version=str(dataset["data_version"]),
            expected_checksum=str(dataset["checksum"]),
            expected_universe=dataset["universe"],
            expected_bar_count=dataset["bar_count"],
        )
        if series is None:
            return empty
        bars, total_count = series
        return {
            "symbol": symbol,
            "data_version": dataset["data_version"],
            "dataset_checksum": dataset["checksum"],
            "observation_type": "HISTORICAL_DAILY_BAR_SERIES",
            "total_count": total_count,
            "truncated": total_count > len(bars),
            "bars": bars,
        }

    @app.get("/strategies")
    def strategies(_: str = Depends(_require_role(Role.READ, auth_config))) -> list[object]:
        report = _load_verified_research_report(state.research_comparison_path)
        if report is None:
            return []
        raw_experiments = report.get("experiments", [])
        experiments = raw_experiments if isinstance(raw_experiments, list) else []
        return [
            {
                "strategy_id": value.get("experiment_id"),
                "name": value.get("experiment_id"),
                "family": "ETF_ROTATION",
                "status": "EXPERIMENT",
                "role": "NONE",
                "metrics": value.get("metrics", {}),
                "scenario_type": value.get("scenario_type"),
                "data_version": report.get("data_version"),
            }
            for value in experiments
            if isinstance(value, dict)
        ]

    @app.get("/experiments")
    def experiments(_: str = Depends(_require_role(Role.READ, auth_config))) -> dict[str, object]:
        report = _load_verified_research_report(state.research_comparison_path)
        return report or {
            "classification": "NO_VERIFIED_RESEARCH_RESULT",
            "experiments": [],
            "promotion": "NOT_EVALUATED",
            "promotion_reasons": ["MISSING_VERIFIED_RESEARCH_ARTIFACT"],
        }

    @app.get("/walk-forward")
    def walk_forward(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        report = _load_verified_research_report(state.walk_forward_report_path)
        return report or {
            "classification": "NO_VERIFIED_WALK_FORWARD_RESULT",
            "promotion": "NOT_EVALUATED",
            "promotion_reasons": ["MISSING_VERIFIED_WALK_FORWARD_ARTIFACT"],
            "result": {"folds": []},
        }

    @app.get("/robustness")
    def robustness(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        report = _load_verified_robustness_report(state.robustness_report_path)
        return report or {
            "classification": "NO_VERIFIED_ROBUSTNESS_RESULT",
            "promotion": "NOT_EVALUATED",
            "promotion_reasons": ["MISSING_VERIFIED_ROBUSTNESS_ARTIFACT"],
            "result": {"scenario_metrics": []},
        }

    @app.get("/backtest-analytics")
    def backtest_analytics(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        analytics = _load_verified_backtest_analytics(
            state.research_comparison_path,
            state.research_artifact_root,
        )
        return analytics or {
            "classification": "NO_VERIFIED_BACKTEST_ANALYTICS",
            "promotion": "NOT_EVALUATED",
            "leader": None,
            "curve": [],
        }

    @app.get("/portfolio")
    def portfolio(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        return {"positions": len(state.ledger.account.positions)}

    @app.get("/trades")
    def trades(_: str = Depends(_require_role(Role.READ, auth_config))) -> list[object]:
        return []

    @app.get("/pnl")
    def pnl(_: str = Depends(_require_role(Role.READ, auth_config))) -> dict[str, str]:
        realized = sum(
            (position.realized_pnl for position in state.ledger.account.positions.values()),
            start=Decimal("0"),
        )
        return {"realized": str(realized)}

    @app.get("/audit")
    def audit(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> list[dict[str, object]]:
        return [
            {
                "timestamp": event.timestamp.isoformat(),
                "event_type": event.event_type,
                "severity": event.severity,
                "message": event.message,
                "order_id": event.order_id,
            }
            for event in state.audit.events
        ]

    @app.get("/reports")
    def reports(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> list[dict[str, object]]:
        if state.daily_report_root is None or not state.daily_report_root.is_dir():
            return []
        summaries: list[dict[str, object]] = []
        for path in sorted(state.daily_report_root.glob("*.json"), reverse=True):
            raw = path.read_text(encoding="utf-8")
            envelope = json.loads(raw)
            report = envelope.get("report", {})
            digest = envelope.get("sha256")
            if not isinstance(report, dict) or not isinstance(digest, str):
                continue
            canonical = json.dumps(
                report, sort_keys=True, separators=(",", ":"), default=str
            )
            if sha256(canonical.encode()).hexdigest() != digest:
                continue
            summaries.append(
                {
                    "report_id": report.get("report_id"),
                    "trading_day": report.get("trading_day"),
                    "generated_at": report.get("generated_at"),
                    "data_version": report.get("data_version"),
                    "strategy_versions": report.get("strategy_versions", []),
                    "sha256": digest,
                }
            )
        return summaries

    @app.post("/orders/cancel")
    def cancel_order(
        payload: CancelRequest,
        _: str = Depends(_require_role(Role.TRADE, auth_config)),
    ) -> dict[str, object]:
        if state.cancel is None:
            raise HTTPException(status_code=503, detail="order cancellation is not configured")
        try:
            cancelled = state.cancel(
                CancelCommand(payload.internal_order_id, payload.idempotency_key)
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="order not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "internal_order_id": cancelled.internal_order_id,
            "status": cancelled.status.value,
        }

    @app.post("/system/start")
    def start(
        _: ControlRequest,
        actor: str = Depends(_require_role(Role.CONTROL, auth_config)),
    ) -> dict[str, Any]:
        try:
            state.runtime.start(actor=actor)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return asdict(state.runtime.status)

    @app.post("/system/stop")
    def stop(
        _: ControlRequest,
        actor: str = Depends(_require_role(Role.CONTROL, auth_config)),
    ) -> dict[str, Any]:
        state.runtime.stop(actor=actor)
        return asdict(state.runtime.status)

    @app.post("/system/pause")
    def pause(
        _: ControlRequest,
        actor: str = Depends(_require_role(Role.CONTROL, auth_config)),
    ) -> dict[str, Any]:
        state.runtime.pause(actor=actor)
        return asdict(state.runtime.status)

    @app.post("/system/resume")
    def resume(
        _: ControlRequest,
        actor: str = Depends(_require_role(Role.CONTROL, auth_config)),
    ) -> dict[str, Any]:
        try:
            state.runtime.resume(actor=actor)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return asdict(state.runtime.status)

    @app.post("/system/kill")
    def kill(
        payload: ControlRequest,
        actor: str = Depends(_require_role(Role.CONTROL, auth_config)),
    ) -> dict[str, Any]:
        state.runtime.kill(actor=actor, reason=payload.reason)
        return asdict(state.runtime.status)

    @app.post("/system/recover")
    def recover(
        _: ControlRequest,
        actor: str = Depends(_require_role(Role.CONTROL, auth_config)),
    ) -> dict[str, Any]:
        if state.recover is None:
            raise HTTPException(status_code=503, detail="recovery is not configured")
        try:
            state.recover(actor)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return asdict(state.runtime.status)

    def web_index() -> str:
        index = web_dist / "index.html"
        if index.is_file():
            return index.read_text(encoding="utf-8")
        return Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return web_index()

    @app.get("/web/{path:path}", response_class=HTMLResponse)
    def web_dashboard(path: str) -> str:
        del path
        return web_index()

    return app


def context_from_runtime(bundle: object) -> AppContext:
    """Build API state from the canonical PAPER/SHADOW runtime bundle."""
    from qtrade.runtime import PaperRuntimeBundle

    if not isinstance(bundle, PaperRuntimeBundle):
        raise TypeError("expected PaperRuntimeBundle")
    return AppContext(
        bundle.runtime,
        bundle.metrics,
        bundle.oms,
        bundle.ledger,
        bundle.audit,
        recover=lambda actor: bundle.reconcile_and_recover(actor=actor),
        cancel=bundle.service.cancel,
        daily_report_root=Path("runtime/reports"),
        research_comparison_path=Path("runtime/research/akshare-v4/comparison.json"),
        walk_forward_report_path=Path(
            "runtime/research/akshare-walk-forward-v1.json"
        ),
        robustness_report_path=Path(
            "runtime/research/akshare-robustness-v1.json"
        ),
        research_artifact_root=Path("runtime/research/akshare-v4"),
        historical_manifest_path=Path(
            "runtime/data/akshare/"
            "akshare-sina-etf30-current-20260812-v1-raw-daily-20240101-20260812/"
            "manifest.json"
        ),
    )


def _load_verified_research_report(path: Path | None) -> dict[str, object] | None:
    if path is None or not path.is_file():
        return None
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(envelope, dict):
            return None
        report = envelope.get("report")
        digest = envelope.get("sha256")
        if not isinstance(report, dict) or not isinstance(digest, str):
            return None
        canonical = json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        if sha256(canonical.encode("utf-8")).hexdigest() != digest:
            return None
        return report
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _load_verified_robustness_report(path: Path | None) -> dict[str, object] | None:
    report = _load_verified_research_report(path)
    if report is None:
        return None
    try:
        if (
            report.get("classification")
            != "post_rejection_diagnostic_reuses_observations_not_new_heldout"
            or report.get("promotion") != "NOT_ELIGIBLE"
        ):
            return None
        required_biases = {
            "CURRENT_UNIVERSE_SURVIVORSHIP_BIAS",
            "POST_REJECTION_OBSERVATIONS_REUSED_FOR_DIAGNOSTICS",
        }
        biases = report["known_biases"]
        reasons = report["promotion_reasons"]
        scenarios = report["scenarios"]
        result = report["result"]
        if (
            not isinstance(report["data_version"], str)
            or not _is_sha256(report["data_checksum"])
            or not isinstance(report["git_commit"], str)
            or not isinstance(biases, list)
            or not required_biases.issubset(biases)
            or not isinstance(reasons, list)
            or not reasons
            or not all(isinstance(reason, str) for reason in reasons)
            or not isinstance(scenarios, list)
            or not 1 <= len(scenarios) <= 20
            or not isinstance(result, dict)
        ):
            return None
        scenario_ids = _validated_robustness_scenario_ids(scenarios)
        metrics = result["scenario_metrics"]
        if not isinstance(metrics, list) or len(metrics) != len(scenario_ids):
            return None
        sharpes: list[Decimal] = []
        for expected_id, scenario_metrics in zip(scenario_ids, metrics, strict=True):
            if (
                not isinstance(scenario_metrics, list)
                or len(scenario_metrics) != 2
                or scenario_metrics[0] != expected_id
                or not isinstance(scenario_metrics[1], list)
            ):
                return None
            values: dict[str, Decimal] = {}
            for item in scenario_metrics[1]:
                if (
                    not isinstance(item, list)
                    or len(item) != 2
                    or not isinstance(item[0], str)
                    or not isinstance(item[1], str)
                ):
                    return None
                value = Decimal(item[1])
                if not value.is_finite() or item[0] in values:
                    return None
                values[item[0]] = value
            sharpes.append(values["sharpe"])
        worst = Decimal(result["worst_sharpe"])
        dispersion = Decimal(result["dispersion"])
        legacy_flag = "any_negative_sharpe" not in result and "cliff_detected" in result
        any_negative = result[
            "cliff_detected" if legacy_flag else "any_negative_sharpe"
        ]
        digest = result["result_hash"]
        if (
            not worst.is_finite()
            or not dispersion.is_finite()
            or not isinstance(any_negative, bool)
            or not _is_sha256(digest)
            or worst != min(sharpes)
            or dispersion != max(sharpes) - min(sharpes)
            or any_negative != any(value < 0 for value in sharpes)
        ):
            return None
        payload = {
            "scenarios": scenarios,
            "results": metrics,
            "worst_sharpe": str(worst),
            "dispersion": str(dispersion),
            "cliff_detected" if legacy_flag else "any_negative_sharpe": any_negative,
        }
        calculated = sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()
        if calculated != digest:
            return None
        if legacy_flag:
            normalized = dict(report)
            normalized_result = dict(result)
            normalized_result.pop("cliff_detected")
            normalized_result["any_negative_sharpe"] = any_negative
            normalized_result["source_result_hash"] = digest
            normalized_payload = {
                "scenarios": scenarios,
                "results": metrics,
                "worst_sharpe": str(worst),
                "dispersion": str(dispersion),
                "any_negative_sharpe": any_negative,
            }
            normalized_result["result_hash"] = sha256(
                json.dumps(normalized_payload, sort_keys=True, default=str).encode()
            ).hexdigest()
            normalized["result"] = normalized_result
            return normalized
        return report
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return None


def _validated_robustness_scenario_ids(scenarios: list[object]) -> list[str]:
    identities: list[str] = []
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ValueError("invalid robustness scenario")
        identity = scenario["scenario_id"]
        overrides = scenario["parameter_overrides"]
        multiplier = Decimal(scenario["cost_multiplier"])
        regime = scenario["regime"]
        universe = scenario["universe_override"]
        if not isinstance(overrides, list):
            raise ValueError("invalid robustness scenario")
        override_keys: list[str] = []
        for override in overrides:
            if (
                not isinstance(override, list)
                or len(override) != 2
                or not all(isinstance(value, str) and value for value in override)
                or override[0] in override_keys
            ):
                raise ValueError("invalid robustness scenario")
            override_keys.append(override[0])
        if (
            not isinstance(identity, str)
            or not identity
            or identity in identities
            or not multiplier.is_finite()
            or multiplier <= 0
            or not isinstance(regime, str)
            or not regime
            or (
                universe is not None
                and (
                    not isinstance(universe, list)
                    or not universe
                    or not all(isinstance(symbol, str) and symbol for symbol in universe)
                    or len(universe) != len(set(universe))
                )
            )
        ):
            raise ValueError("invalid robustness scenario")
        identities.append(identity)
    return identities


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _load_verified_backtest_analytics(
    comparison_path: Path | None,
    artifact_root: Path | None,
) -> dict[str, object] | None:
    report = _load_verified_research_report(comparison_path)
    if report is None or artifact_root is None:
        return None
    try:
        if (
            report.get("classification")
            != "exploratory_noncommercial_current_universe_not_pit"
            or report.get("promotion") != "NOT_ELIGIBLE"
        ):
            return None
        experiments = report["experiments"]
        if not isinstance(experiments, list) or not experiments:
            return None
        verified_rows: list[tuple[Decimal, dict[str, object]]] = []
        for value in experiments:
            if not isinstance(value, dict):
                return None
            metrics = value["metrics"]
            if not isinstance(metrics, dict):
                return None
            sharpe = Decimal(metrics["sharpe"])
            if not sharpe.is_finite():
                return None
            verified_rows.append((sharpe, value))
        leader = max(verified_rows, key=lambda item: item[0])[1]
        root = artifact_root.resolve()
        leader_artifacts = _load_verified_experiment_analytics(leader, report, root)
        if leader_artifacts is None:
            return None
        benchmark_candidates = []
        for _, row in verified_rows:
            candidate = _load_verified_experiment_analytics(row, report, root)
            if candidate is None:
                return None
            candidate_record = candidate.get("record") if candidate is not None else None
            if isinstance(candidate_record, dict) and candidate_record.get("model") == "buy-hold":
                benchmark_candidates.append(candidate)
        if len(benchmark_candidates) != 1:
            return None
        benchmark = benchmark_candidates[0]
        benchmark_curve = benchmark.get("curve")
        if benchmark_curve is None:
            return None
        combined_curve = _aligned_strategy_benchmark_curve(
            leader_artifacts["curve"], benchmark_curve
        )
        leader_record = leader_artifacts["record"]
        if not isinstance(leader_record, dict):
            return None
        return {
            "classification": report["classification"],
            "promotion": "NOT_ELIGIBLE",
            "data_version": report["data_version"],
            "git_commit": report["git_commit"],
            "leader": leader,
            "benchmark": benchmark["row"],
            "split": leader_record["split"],
            "record_hash": leader_artifacts["record_hash"],
            "benchmark_record_hash": benchmark["record_hash"],
            "artifact_hashes": leader_artifacts["artifact_hashes"],
            "benchmark_artifact_hashes": benchmark["artifact_hashes"],
            "curve": combined_curve,
            "monthly_returns": leader_artifacts["monthly_returns"],
            "trades": leader_artifacts["trades"],
            "positions": leader_artifacts["positions"],
        }
    except (
        InvalidOperation,
        KeyError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


def _load_verified_experiment_analytics(
    row: dict[str, object], report: dict[str, object], root: Path
) -> dict[str, object] | None:
    try:
        experiment_id = row["experiment_id"]
        if not isinstance(experiment_id, str) or not _is_safe_identifier(experiment_id):
            return None
        record_path = (root / f"{experiment_id}.json").resolve()
        artifacts_dir = (root / f"{experiment_id}-artifacts").resolve()
        if not record_path.is_relative_to(root) or not artifacts_dir.is_relative_to(root):
            return None
        envelope = json.loads(record_path.read_text(encoding="utf-8"))
        if not isinstance(envelope, dict):
            return None
        record = envelope["record"]
        record_hash = envelope["spec_hash"]
        if not isinstance(record, dict) or not _is_sha256(record_hash):
            return None
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
        if sha256(canonical.encode()).hexdigest() != record_hash:
            return None
        if (
            record.get("experiment_id") != experiment_id
            or record.get("data_version") != report.get("data_version")
            or record.get("git_commit") != report.get("git_commit")
            or record.get("metrics") != row.get("metrics")
        ):
            return None
        universe = record.get("universe")
        if (
            not isinstance(universe, list)
            or not universe
            or any(
                not isinstance(symbol, str)
                or len(symbol) != 6
                or not symbol.isascii()
                or not symbol.isdigit()
                for symbol in universe
            )
            or len(set(universe)) != len(universe)
        ):
            return None
        allowed_symbols = frozenset(universe)
        artifact_manifest = _validated_artifact_manifest(record["artifacts"])
        metrics = tuple(
            sorted((key, Decimal(value)) for key, value in record["metrics"].items())
        )
        result_payload = {
            "artifact_manifest": tuple(sorted(artifact_manifest.items())),
            "metrics": metrics,
            "record_hash": record_hash,
            "strategy_id": experiment_id,
        }
        calculated_result_hash = sha256(
            json.dumps(
                result_payload,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest()
        if calculated_result_hash != row.get("result_hash"):
            return None
        equity = _load_hashed_json(artifacts_dir / "equity.json", artifact_manifest["equity"])
        drawdown = _load_hashed_json(
            artifacts_dir / "drawdown.json", artifact_manifest["drawdown"]
        )
        curve = _validated_equity_drawdown_curve(equity, drawdown)
        curve_sessions = frozenset(point["session"] for point in curve)
        trades = _validated_backtest_trades(
            _load_hashed_json(artifacts_dir / "trades.json", artifact_manifest["trades"]),
            allowed_symbols,
            curve_sessions,
        )
        positions = _validated_backtest_positions(
            _load_hashed_json(
                artifacts_dir / "positions.json", artifact_manifest["positions"]
            ),
            allowed_symbols,
        )
        _reconcile_backtest_trades_and_positions(trades, positions)
        return {
            "row": row,
            "record": record,
            "record_hash": record_hash,
            "artifact_hashes": {
                name: artifact_manifest[name]
                for name in ("equity", "drawdown", "trades", "positions")
            },
            "curve": curve,
            "trades": trades,
            "positions": positions,
            "monthly_returns": _monthly_returns(curve),
        }
    except (
        InvalidOperation,
        KeyError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


def _aligned_strategy_benchmark_curve(
    strategy: object, benchmark: object
) -> list[dict[str, str]]:
    if (
        not isinstance(strategy, list)
        or not isinstance(benchmark, list)
        or not strategy
        or len(strategy) != len(benchmark)
    ):
        raise ValueError("benchmark curve does not align")
    first_strategy = Decimal(strategy[0]["equity"])
    first_benchmark = Decimal(benchmark[0]["equity"])
    if first_strategy <= 0 or first_benchmark <= 0:
        raise ValueError("benchmark curve does not align")
    result: list[dict[str, str]] = []
    for strategy_point, benchmark_point in zip(strategy, benchmark, strict=True):
        if strategy_point["session"] != benchmark_point["session"]:
            raise ValueError("benchmark curve does not align")
        strategy_return = Decimal(strategy_point["equity"]) / first_strategy - 1
        benchmark_return = Decimal(benchmark_point["equity"]) / first_benchmark - 1
        result.append(
            {
                **strategy_point,
                "benchmark_equity": benchmark_point["equity"],
                "benchmark_drawdown": benchmark_point["drawdown"],
                "excess_return": str(strategy_return - benchmark_return),
            }
        )
    return result


def _is_safe_identifier(value: str) -> bool:
    return 1 <= len(value) <= 160 and all(
        character.isascii() and (character.isalnum() or character in "-_")
        for character in value
    )


def _validated_artifact_manifest(value: object) -> dict[str, str]:
    if not isinstance(value, list):
        raise ValueError("invalid experiment artifact manifest")
    result: dict[str, str] = {}
    for item in value:
        if not isinstance(item, str) or item.count(":") != 1:
            raise ValueError("invalid experiment artifact manifest")
        name, digest = item.split(":")
        if name in result or not _is_safe_identifier(name) or not _is_sha256(digest):
            raise ValueError("invalid experiment artifact manifest")
        result[name] = digest
    if not {"equity", "drawdown", "trades", "positions"}.issubset(result):
        raise ValueError("missing analytics artifacts")
    return result


def _load_hashed_json(path: Path, expected_hash: str) -> object:
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != expected_hash:
        raise ValueError("experiment artifact hash mismatch")
    return json.loads(raw.decode("utf-8"))


def _validated_equity_drawdown_curve(
    equity: object,
    drawdown: object,
) -> list[dict[str, str]]:
    if (
        not isinstance(equity, list)
        or not isinstance(drawdown, list)
        or not equity
        or len(equity) != len(drawdown)
        or len(equity) > 5000
    ):
        raise ValueError("invalid analytics curve")
    result: list[dict[str, str]] = []
    previous_session = ""
    peak = Decimal("0")
    for equity_item, drawdown_item in zip(equity, drawdown, strict=True):
        if not isinstance(equity_item, dict) or not isinstance(drawdown_item, dict):
            raise ValueError("invalid analytics curve")
        session = equity_item["session"]
        equity_value = Decimal(equity_item["equity"])
        drawdown_value = Decimal(drawdown_item["drawdown"])
        peak = max(peak, equity_value)
        expected_drawdown = equity_value / peak - 1
        if not isinstance(session, str):
            raise ValueError("invalid analytics curve")
        try:
            session_date = date.fromisoformat(session)
        except ValueError as exc:
            raise ValueError("invalid analytics curve") from exc
        if (
            session_date.isoformat() != session
            or session != drawdown_item.get("session")
            or session <= previous_session
            or not equity_value.is_finite()
            or equity_value <= 0
            or not drawdown_value.is_finite()
            or drawdown_value < -1
            or drawdown_value > 0
            or drawdown_value != expected_drawdown
        ):
            raise ValueError("invalid analytics curve")
        result.append(
            {
                "session": session,
                "equity": str(equity_value),
                "drawdown": str(drawdown_value),
            }
        )
        previous_session = session
    return result


def _validated_backtest_trades(
    value: object,
    allowed_symbols: frozenset[str],
    curve_sessions: frozenset[str],
) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > 10000:
        raise ValueError("invalid backtest trades")
    result: list[dict[str, object]] = []
    previous_day = ""
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("invalid backtest trades")
        symbol = item.get("symbol")
        side = item.get("side")
        trading_day = item.get("trading_day")
        quantity = item.get("quantity")
        price = Decimal(str(item.get("price")))
        cost = Decimal(str(item.get("cost")))
        if (
            not isinstance(symbol, str)
            or len(symbol) != 6
            or not symbol.isascii()
            or not symbol.isdigit()
            or symbol not in allowed_symbols
            or side not in {"BUY", "SELL"}
            or not isinstance(trading_day, str)
            or trading_day not in curve_sessions
            or not isinstance(quantity, int)
            or isinstance(quantity, bool)
            or quantity <= 0
            or not price.is_finite()
            or price <= 0
            or not cost.is_finite()
            or cost < 0
        ):
            raise ValueError("invalid backtest trades")
        date.fromisoformat(trading_day)
        if trading_day < previous_day:
            raise ValueError("invalid backtest trades")
        previous_day = trading_day
        result.append(
            {
                "symbol": symbol,
                "side": side,
                "trading_day": trading_day,
                "quantity": quantity,
                "price": str(price),
                "cost": str(cost),
                "notional": str(price * quantity),
            }
        )
    return result


def _reconcile_backtest_trades_and_positions(
    trades: list[dict[str, object]], positions: list[dict[str, object]]
) -> None:
    quantities: dict[str, int] = {}
    for trade in trades:
        symbol = trade["symbol"]
        quantity = trade["quantity"]
        if not isinstance(symbol, str) or not isinstance(quantity, int):
            raise ValueError("invalid backtest trades")
        updated = quantities.get(symbol, 0)
        updated += quantity if trade["side"] == "BUY" else -quantity
        if updated < 0:
            raise ValueError("backtest trades create a negative position")
        quantities[symbol] = updated
    expected: dict[str, int] = {}
    for position in positions:
        symbol = position["symbol"]
        quantity = position["quantity"]
        if not isinstance(symbol, str) or not isinstance(quantity, int):
            raise ValueError("invalid backtest positions")
        expected[symbol] = quantity
    observed = {symbol: quantity for symbol, quantity in quantities.items() if quantity}
    if observed != expected:
        raise ValueError("backtest trades do not reconcile to positions")


def _validated_backtest_positions(
    value: object, allowed_symbols: frozenset[str]
) -> list[dict[str, object]]:
    if not isinstance(value, dict) or len(value) > 1000:
        raise ValueError("invalid backtest positions")
    result: list[dict[str, object]] = []
    for symbol, quantity in sorted(value.items()):
        if (
            not isinstance(symbol, str)
            or len(symbol) != 6
            or not symbol.isascii()
            or not symbol.isdigit()
            or symbol not in allowed_symbols
            or not isinstance(quantity, int)
            or isinstance(quantity, bool)
            or quantity <= 0
        ):
            raise ValueError("invalid backtest positions")
        result.append({"symbol": symbol, "quantity": quantity})
    return result


def _monthly_returns(curve: object) -> list[dict[str, str]]:
    if not isinstance(curve, list) or not curve:
        raise ValueError("invalid analytics curve")
    first = curve[0]
    if not isinstance(first, dict):
        raise ValueError("invalid analytics curve")
    last_by_month: dict[str, Decimal] = {}
    initial = Decimal(str(first["equity"]))
    for point in curve:
        if not isinstance(point, dict):
            raise ValueError("invalid analytics curve")
        month = str(point["session"])[:7]
        equity = Decimal(str(point["equity"]))
        last_by_month[month] = equity
    result: list[dict[str, str]] = []
    previous = initial
    for month in sorted(last_by_month):
        result.append({"month": month, "return": str(last_by_month[month] / previous - 1)})
        previous = last_by_month[month]
    return result


def _load_verified_market_dataset(path: Path | None) -> dict[str, object] | None:
    if path is None or not path.is_file():
        return None
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(evidence, dict):
            return None
        evidence_digest = evidence.get("evidence_sha256")
        unsigned = {key: value for key, value in evidence.items() if key != "evidence_sha256"}
        canonical = json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        if (
            not isinstance(evidence_digest, str)
            or sha256(canonical.encode("utf-8")).hexdigest() != evidence_digest
        ):
            return None
        manifest = evidence.get("manifest")
        universe = evidence.get("universe")
        if not isinstance(manifest, dict) or not isinstance(universe, dict):
            return None
        checksum = manifest.get("checksum")
        data_version = manifest.get("data_version")
        symbols = universe.get("symbols")
        if (
            not isinstance(checksum, str)
            or not isinstance(data_version, str)
            or manifest.get("quality_status") != "PASSED"
            or not isinstance(symbols, list)
            or not all(isinstance(symbol, str) for symbol in symbols)
            or not isinstance(evidence.get("bar_count"), int)
            or evidence["bar_count"] <= 0
            or not isinstance(universe.get("point_in_time"), bool)
        ):
            return None
        bars_content = _read_bounded_file(
            path.parent / "bars.parquet", _MAX_MARKET_PARQUET_BYTES
        )
        if bars_content is None or sha256(bars_content).hexdigest() != checksum:
            return None
        return {
            "provider": universe.get("provider"),
            "provider_version": universe.get("provider_version"),
            "classification": evidence.get("classification"),
            "data_version": data_version,
            "quality_status": manifest.get("quality_status"),
            "checksum": checksum,
            "bar_count": evidence.get("bar_count"),
            "observed_start": evidence.get("observed_start"),
            "observed_end": evidence.get("observed_end"),
            "snapshot_readiness": evidence.get("snapshot_readiness") is True,
            "real_data_paper_readiness": evidence.get("real_data_paper_readiness") is True,
            "universe": universe,
        }
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _load_latest_historical_bars(
    manifest_path: Path | None,
    *,
    data_version: str,
    expected_checksum: str,
    expected_universe: object,
    expected_bar_count: object,
    expected_observed_end: object,
) -> list[dict[str, str]]:
    if manifest_path is None or not isinstance(expected_universe, dict):
        return []
    symbols = expected_universe.get("symbols")
    if (
        not isinstance(symbols, list)
        or not all(isinstance(value, str) for value in symbols)
        or len(set(symbols)) != len(symbols)
        or not isinstance(expected_bar_count, int)
        or expected_bar_count <= 0
        or not isinstance(expected_observed_end, str)
    ):
        return []
    try:
        observed_end = datetime.fromisoformat(expected_observed_end)
        if observed_end.tzinfo is None:
            return []
        expected_latest_day = observed_end.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        parquet = _open_verified_market_parquet(
            manifest_path,
            expected_checksum=expected_checksum,
            expected_bar_count=expected_bar_count,
        )
        if parquet is None:
            return []
        latest: dict[str, dict[str, str]] = {}
        latest_instants: dict[str, datetime] = {}
        allowed = set(symbols)
        columns = [
            "symbol",
            "exchange",
            "timestamp",
            "trading_day",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "data_version",
        ]
        rows = (
            raw
            for batch in parquet.iter_batches(batch_size=8192, columns=columns)
            for raw in batch.to_pylist()
        )
        for raw in rows:
            validated = _validated_historical_bar(raw, data_version, allowed)
            if validated is None:
                return []
            candidate, parsed_timestamp = validated
            symbol = candidate["symbol"]
            previous = latest.get(symbol)
            previous_instant = latest_instants.get(symbol)
            if previous is None or previous_instant is None or parsed_timestamp > previous_instant:
                latest[symbol] = candidate
                latest_instants[symbol] = parsed_timestamp
        if set(latest) != allowed:
            return []
        if any(row["trading_day"] != expected_latest_day for row in latest.values()):
            return []
        return [latest[symbol] for symbol in symbols]
    except (OSError, ValueError, TypeError, InvalidOperation, pa.ArrowException):
        return []


def _load_historical_bar_series(
    manifest_path: Path | None,
    *,
    symbol: str,
    data_version: str,
    expected_checksum: str,
    expected_universe: object,
    expected_bar_count: object,
) -> tuple[list[dict[str, str]], int] | None:
    if manifest_path is None or not isinstance(expected_universe, dict):
        return None
    symbols = expected_universe.get("symbols")
    if (
        not isinstance(symbols, list)
        or not all(isinstance(value, str) for value in symbols)
        or len(set(symbols)) != len(symbols)
        or symbol not in symbols
        or not isinstance(expected_bar_count, int)
        or expected_bar_count <= 0
    ):
        return None
    try:
        parquet = _open_verified_market_parquet(
            manifest_path,
            expected_checksum=expected_checksum,
            expected_bar_count=expected_bar_count,
        )
        if parquet is None:
            return None
        allowed = set(symbols)
        selected: deque[dict[str, str]] = deque(maxlen=_MAX_HISTORICAL_SERIES_ROWS)
        previous_instants: dict[str, datetime] = {}
        previous_days: dict[str, str] = {}
        total_count = 0
        columns = [
            "symbol",
            "exchange",
            "timestamp",
            "trading_day",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "data_version",
        ]
        for batch in parquet.iter_batches(batch_size=8192, columns=columns):
            for raw in batch.to_pylist():
                validated = _validated_historical_bar(raw, data_version, allowed)
                if validated is None:
                    return None
                candidate, instant = validated
                candidate_symbol = candidate["symbol"]
                previous_instant = previous_instants.get(candidate_symbol)
                previous_day = previous_days.get(candidate_symbol)
                if (
                    previous_instant is not None
                    and (
                        instant <= previous_instant
                        or candidate["trading_day"] <= str(previous_day)
                    )
                ):
                    return None
                previous_instants[candidate_symbol] = instant
                previous_days[candidate_symbol] = candidate["trading_day"]
                if candidate["symbol"] == symbol:
                    total_count += 1
                    selected.append(candidate)
        if total_count == 0:
            return None
        bars = [
            {**candidate, "observation_type": "HISTORICAL_DAILY_BAR"}
            for candidate in selected
        ]
        return bars, total_count
    except (OSError, ValueError, TypeError, InvalidOperation, pa.ArrowException):
        return None


def _open_verified_market_parquet(
    manifest_path: Path,
    *,
    expected_checksum: str,
    expected_bar_count: int,
) -> pq.ParquetFile | None:
    content = _read_bounded_file(
        manifest_path.parent / "bars.parquet", _MAX_MARKET_PARQUET_BYTES
    )
    if content is None or sha256(content).hexdigest() != expected_checksum:
        return None
    parquet = pq.ParquetFile(pa.BufferReader(content))
    metadata = parquet.metadata
    uncompressed_bytes = sum(
        metadata.row_group(index).total_byte_size for index in range(metadata.num_row_groups)
    )
    if (
        metadata.num_rows != expected_bar_count
        or metadata.num_rows > _MAX_MARKET_PARQUET_ROWS
        or metadata.num_row_groups > _MAX_MARKET_PARQUET_ROW_GROUPS
        or uncompressed_bytes > _MAX_MARKET_PARQUET_UNCOMPRESSED_BYTES
    ):
        return None
    return parquet


def _validated_historical_bar(
    raw: dict[str, object], data_version: str, allowed: set[str]
) -> tuple[dict[str, str], datetime] | None:
    symbol = raw.get("symbol")
    exchange = raw.get("exchange")
    timestamp = raw.get("timestamp")
    if (
        not isinstance(symbol, str)
        or symbol not in allowed
        or exchange not in {"SSE", "SZSE"}
        or not isinstance(timestamp, str)
        or raw.get("data_version") != data_version
    ):
        return None
    trading_day_value = raw.get("trading_day")
    trading_day = (
        trading_day_value.isoformat()
        if hasattr(trading_day_value, "isoformat")
        else str(trading_day_value)
    )
    parsed_timestamp = datetime.fromisoformat(timestamp)
    if parsed_timestamp.tzinfo is None:
        return None
    date.fromisoformat(trading_day)
    local_trading_day = (
        parsed_timestamp.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    )
    if local_trading_day != trading_day:
        return None
    values = {
        name: Decimal(str(raw.get(name)))
        for name in ("open", "high", "low", "close", "volume")
    }
    if (
        any(not value.is_finite() for value in values.values())
        or any(values[name] <= 0 for name in ("open", "high", "low", "close"))
        or values["volume"] < 0
        or values["low"] > min(values["open"], values["close"])
        or values["high"] < max(values["open"], values["close"])
    ):
        return None
    return (
        {
            "symbol": symbol,
            "exchange": str(exchange),
            "timestamp": timestamp,
            "trading_day": trading_day,
            "open": str(values["open"]),
            "high": str(values["high"]),
            "low": str(values["low"]),
            "close": str(values["close"]),
            "volume": str(values["volume"]),
            "data_version": data_version,
            "observation_type": "HISTORICAL_DAILY_CLOSE",
        },
        parsed_timestamp,
    )


def _read_bounded_file(path: Path, limit: int) -> bytes | None:
    try:
        with path.open("rb") as stream:
            content = stream.read(limit + 1)
        return content if len(content) <= limit else None
    except OSError:
        return None
