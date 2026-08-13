from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from secrets import compare_digest
from typing import Any

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
        bars = path.parent / "bars.parquet"
        if not bars.is_file() or sha256(bars.read_bytes()).hexdigest() != checksum:
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
