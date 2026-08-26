from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict, deque
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from datetime import time as datetime_time
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
from itertools import islice
from math import isclose, isfinite
from pathlib import Path
from secrets import compare_digest
from statistics import median
from typing import Any, Literal, Self, cast
from zoneinfo import ZoneInfo

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, StrictInt, model_validator

from qtrade.accounting import Ledger
from qtrade.audit import AuditEvent, AuditLog
from qtrade.control import PlatformRuntime
from qtrade.data import Bar
from qtrade.domain import CancelCommand, Order, OrderStatus, RuntimeMode
from qtrade.execution import FeeSchedule, PaperGateway
from qtrade.market_data import (
    SINA_COMPLETE_SESSION_TIMES,
    SINA_FIVE_MINUTE_SESSION_TIMES,
)
from qtrade.monitoring import MetricsRegistry, render_prometheus_metrics
from qtrade.oms import OrderManager
from qtrade.research import (
    BacktestPreviewSpec,
    ExperimentRunSpec,
    IntradayExitSpec,
    IntradayFactorDiscoveryResult,
    IntradayFactorGroup,
    IntradayFactorResearchResult,
    IntradayRuleCorrelationResult,
    IntradayRuleEffectiveness,
    MinuteBacktestResult,
    MinuteCurveMetrics,
    OpeningTripleBullishSpec,
    PerformanceMetrics,
    TimeSplit,
    discover_opening_direction_rules,
    evaluate_opening_rule_correlations,
    evaluate_opening_rule_effectiveness,
    experiment_run_spec_hash,
    paired_stationary_bootstrap_excess_returns,
    run_backtest_preview,
    run_minute_overlay_backtest,
    run_opening_triple_bullish_research,
)
from qtrade.risk import RiskEngine, RiskLimits
from qtrade.runtime import MinuteOverlayPolicy

_MAX_MARKET_PARQUET_BYTES = 64 * 1024 * 1024
_MAX_MARKET_PARQUET_ROWS = 2_000_000
_MAX_MARKET_PARQUET_ROW_GROUPS = 1024
_MAX_MARKET_PARQUET_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
_MAX_HISTORICAL_SERIES_ROWS = 2000
_MAX_BACKTEST_PREVIEW_SOURCE_ROWS = 250_000
_MAX_SINGLE_STOCK_BACKTEST_SESSIONS = 120
_MIN_INTRADAY_FACTOR_SESSIONS = 20
_MIN_INTRADAY_DISCOVERY_SESSIONS = 40
_MAX_INTRADAY_FACTOR_SESSIONS = 120
_MAX_MINUTE_EVIDENCE_JSON_BYTES = 2 * 1024 * 1024
_INTRADAY_FACTOR_LONG_WINDOW_PREVIEW_SHA256 = (
    "f720e483a74c747a10bc952c58d1933e50b6c9268dd9039ed92bd94c86752a2f"
)
_MARKET_PARQUET_SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        ("exchange", pa.string()),
        ("timestamp", pa.string()),
        ("trading_day", pa.date32()),
        ("open", pa.string()),
        ("high", pa.string()),
        ("low", pa.string()),
        ("close", pa.string()),
        ("volume", pa.string()),
        ("data_version", pa.string()),
    ]
)
_GIT_COMMIT_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
_GIT_COMMIT_REF_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}")
_DEPLOYMENT_ENVIRONMENT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_EXPERIMENT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")


class Role(StrEnum):
    READ = "read"
    CONTROL = "control"
    TRADE = "trade"


class ControlRequest(BaseModel):
    reason: str = "manual control"


class CancelRequest(BaseModel):
    internal_order_id: str
    idempotency_key: str


class BacktestPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date
    factor: Literal["momentum", "reversal", "low_volatility"] = "momentum"
    horizons: tuple[int, ...] = (20, 60, 120)
    top_k: int = 3
    rebalance_every: int = 20
    reserve: Decimal = Decimal("0.05")
    trend_window: int | None = 120
    volatility_window: int | None = 20
    target_annual_volatility: Decimal | None = Decimal("0.15")
    initial_cash: Decimal = Decimal("1000000")
    transaction_cost_rate: Decimal = Decimal("0.0015")
    slippage_rate: Decimal = Decimal("0.0005")


class ExperimentRunSplitDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    test_start: date
    test_end: date


class ExperimentRunSpecDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: Literal[
        "etf-momentum-rotation-v1",
        "etf-momentum-trend-volatility-v1",
        "cross-sectional-factor-ranking-v1",
    ]
    experiment_id: str
    git_commit: str
    split: ExperimentRunSplitDraft
    backtest: BacktestPreviewRequest
    seed: StrictInt = 7

    @model_validator(mode="after")
    def validate_draft_identity(self) -> Self:
        if _EXPERIMENT_ID_PATTERN.fullmatch(self.experiment_id) is None:
            raise ValueError("experiment id must be 1-80 safe characters")
        if _GIT_COMMIT_SHA_PATTERN.fullmatch(self.git_commit) is None:
            raise ValueError("experiment git commit must be a full SHA-1")
        if not 0 <= self.seed <= 2_147_483_647:
            raise ValueError("experiment seed must be in [0, 2147483647]")
        return self


class SingleStockFiveMinuteBacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    start: date
    end: date
    cost_bps: Decimal = Decimal("5")
    short_window: StrictInt = 12
    long_window: StrictInt = 48
    volatility_window: StrictInt = 24
    maximum_minute_volatility: Decimal = Decimal("0.02")
    maximum_intraday_drawdown: Decimal = Decimal("0.04")
    reduction_multiplier: Decimal = Decimal("0.98")
    cooldown_bars: StrictInt = 6


class IntradayFactorBacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factor: Literal[
        "opening_three_ten_minute_bullish",
        "custom_opening_ten_minute_direction_rule",
    ] = "opening_three_ten_minute_bullish"
    start: date
    end: date
    cost_bps: Decimal = Decimal("5")
    minimum_body_return_bps: Decimal = Decimal("0")
    exit_rule: Literal[
        "NEXT_SESSION_OPEN",
        "NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP",
    ] = "NEXT_SESSION_OPEN"
    exit_minimum_body_return_bps: Decimal = Decimal("0")
    directions: tuple[
        Literal["BULLISH", "BEARISH", "IGNORE"],
        Literal["BULLISH", "BEARISH", "IGNORE"],
        Literal["BULLISH", "BEARISH", "IGNORE"],
    ] = ("BULLISH", "BULLISH", "BULLISH")
    combination: Literal["ALL", "ANY"] = "ALL"

    @model_validator(mode="after")
    def validate_factor_contract(self) -> Self:
        if self.factor == "opening_three_ten_minute_bullish" and (
            self.directions != ("BULLISH", "BULLISH", "BULLISH")
            or self.combination != "ALL"
        ):
            raise ValueError(
                "fixed three-bullish factor cannot override directions or combination"
            )
        if not any(direction != "IGNORE" for direction in self.directions):
            raise ValueError(
                "opening rule requires at least one completed ten-minute bar"
            )
        return self


class IntradayFactorDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date
    cost_bps: Decimal = Decimal("5")


class IntradayFactorCorrelationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directions: tuple[
        Literal["BULLISH", "BEARISH", "IGNORE"],
        Literal["BULLISH", "BEARISH", "IGNORE"],
        Literal["BULLISH", "BEARISH", "IGNORE"],
    ]
    combination: Literal["ALL", "ANY"]
    minimum_body_return_bps: Decimal = Decimal("0")

    @model_validator(mode="after")
    def validate_rule_contract(self) -> Self:
        if not any(direction != "IGNORE" for direction in self.directions):
            raise ValueError(
                "opening rule requires at least one completed ten-minute bar"
            )
        return self


class IntradayFactorCorrelationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date
    rules: tuple[IntradayFactorCorrelationRule, ...]

    @model_validator(mode="after")
    def validate_rule_count(self) -> Self:
        if not 2 <= len(self.rules) <= 8:
            raise ValueError(
                "factor correlation requires between two and eight rules"
            )
        return self


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
    factor_search_report_path: Path | None = None
    robust_factor_search_report_path: Path | None = None
    stock_sector_transfer_report_path: Path | None = None
    research_artifact_root: Path | None = None
    historical_manifest_path: Path | None = None
    minute_manifest_path: Path | None = None
    minute_research_report_path: Path | None = None
    five_minute_manifest_path: Path | None = None
    five_minute_research_report_path: Path | None = None
    long_five_minute_manifest_path: Path | None = None
    long_five_minute_research_report_path: Path | None = None
    intraday_factor_robustness_path: Path | None = None
    development_policy_search_path: Path | None = None
    forward_observation_preregistration_path: Path | None = None
    forward_observation_report_path: Path | None = None
    forward_source_coverage_path: Path | None = None
    forward_source_coverage_history_path: Path | None = None
    forward_source_coverage_release_root: Path | None = None
    reserved_tail_preregistration_path: Path | None = None
    reserved_tail_diagnostic_report_path: Path | None = None
    public_minute_audit_path: Path | None = None
    intraday_observation_path: Path | None = None
    intraday_breadth_path: Path | None = None
    intraday_continuity_path: Path | None = None
    minute_overlay_reader: Callable[[], dict[str, object]] | None = None
    applied_trade_reader: Callable[[], tuple[dict[str, object], ...]] | None = None
    sellable_quantity_reader: Callable[[str, date], int] | None = None
    operational_date: date | None = None
    operational_gate: Any = None
    audit_reader: Callable[[int], tuple[AuditEvent, ...]] | None = None
    reporting_status_reader: Callable[[], dict[str, object]] | None = None


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


def _deployment_provenance() -> dict[str, object]:
    raw_sha = os.getenv("VERCEL_GIT_COMMIT_SHA")
    raw_ref = os.getenv("VERCEL_GIT_COMMIT_REF")
    raw_environment = os.getenv("VERCEL_TARGET_ENV") or os.getenv("VERCEL_ENV")
    git_commit_sha = (
        raw_sha if raw_sha is not None and _GIT_COMMIT_SHA_PATTERN.fullmatch(raw_sha) else None
    )
    git_commit_ref = (
        raw_ref if raw_ref is not None and _GIT_COMMIT_REF_PATTERN.fullmatch(raw_ref) else None
    )
    target_environment = (
        raw_environment
        if raw_environment is not None
        and _DEPLOYMENT_ENVIRONMENT_PATTERN.fullmatch(raw_environment)
        else None
    )
    provider = "VERCEL" if os.getenv("VERCEL") == "1" else "LOCAL"
    return {
        "classification": "PUBLIC_BUILD_PROVENANCE",
        "provider": provider,
        "status": "AVAILABLE"
        if provider == "VERCEL" and git_commit_sha is not None
        else "UNAVAILABLE",
        "target_environment": target_environment,
        "git_commit_sha": git_commit_sha,
        "git_commit_ref": git_commit_ref,
        "external_order_transmission": False,
    }


def _strategy_template_catalog() -> dict[str, object]:
    validation = "IMPLEMENTED_NOT_PRODUCTION_VALIDATED"
    templates = [
        {
            "template_id": "equal-weight-buy-hold-benchmark-v1",
            "name": "等权买入并持有",
            "role": "BENCHMARK_ONLY",
            "family": "BUY_AND_HOLD_BENCHMARK",
            "implementation_ref": "qtrade.research.multi_asset.EqualWeightBuyHold",
            "description": "首个交易日等权建仓，之后保持仓位，用作主动策略比较基准。",
            "data_interval": "1d",
            "universe_scope": "多标的",
            "entry_rule": "首个可交易会话按等权目标计算，下一会话开盘执行。",
            "exit_rule": "基准期内不主动退出。",
            "parameter_defaults": {"reserve": "0"},
            "required_inputs": ["质量通过的日线开收盘", "冻结标的池"],
            "validation_status": validation,
            "known_limitations": ["只作为比较基准，不是主动选股或择时策略。"],
            "causal_preview_supported": False,
            "causal_preview_blocker": "COMPARISON_ONLY_NO_ACTIVE_TEMPLATE_PREVIEW",
        },
        {
            "template_id": "moving-average-trend-v1",
            "name": "移动平均趋势",
            "role": "ACTIVE_BASELINE",
            "family": "SINGLE_ASSET_TREND",
            "implementation_ref": (
                "qtrade.strategies.baselines.MovingAverageTrendStrategy"
            ),
            "description": "价格位于移动平均线上方时持仓，跌破时清仓。",
            "data_interval": "1d",
            "universe_scope": "单标的",
            "entry_rule": "收盘价大于等于窗口均线时生成目标持仓。",
            "exit_rule": "收盘价低于窗口均线时生成零仓位目标。",
            "parameter_defaults": {"window": "20", "quantity": "100"},
            "required_inputs": ["质量通过的单标的日线"],
            "validation_status": validation,
            "known_limitations": ["震荡市可能频繁换手；参数必须在测试前冻结。"],
            "causal_preview_supported": False,
            "causal_preview_blocker": "SINGLE_ASSET_DAILY_SELECTOR_NOT_CONNECTED",
        },
        {
            "template_id": "etf-momentum-rotation-v1",
            "name": "ETF 动量轮动",
            "role": "ACTIVE_BASELINE",
            "family": "ETF_MOMENTUM_ROTATION",
            "implementation_ref": "qtrade.research.multi_asset.MomentumAllocation",
            "description": "按多周期动量排序并定期持有排名靠前的 ETF。",
            "data_interval": "1d",
            "universe_scope": "多 ETF",
            "entry_rule": "再平衡日选择正动量且排名前 K 的 ETF。",
            "exit_rule": "再平衡时跌出前 K 或动量非正则退出。",
            "parameter_defaults": {
                "horizons": "20,60,120",
                "top_k": "3",
                "rebalance_every": "20",
                "reserve": "0.05",
            },
            "required_inputs": ["质量通过的日线", "冻结 ETF 标的池"],
            "validation_status": validation,
            "known_limitations": ["目录默认值不是已冻结实验参数。"],
            "causal_preview_supported": True,
            "causal_preview_blocker": None,
        },
        {
            "template_id": "etf-momentum-trend-volatility-v1",
            "name": "ETF 动量 + 趋势波动率约束",
            "role": "ACTIVE_BASELINE",
            "family": "ETF_MOMENTUM_TREND_VOLATILITY",
            "implementation_ref": "qtrade.research.multi_asset.MomentumAllocation",
            "description": "在动量轮动上增加趋势过滤与目标年化波动率缩放。",
            "data_interval": "1d",
            "universe_scope": "多 ETF",
            "entry_rule": "正动量、排名前 K 且价格不低于趋势均线时持有。",
            "exit_rule": "再平衡时任一资格条件失效则退出。",
            "parameter_defaults": {
                "horizons": "20,60,120",
                "top_k": "3",
                "rebalance_every": "20",
                "reserve": "0.05",
                "trend_window": "120",
                "volatility_window": "20",
                "target_annual_volatility": "0.15",
            },
            "required_inputs": ["质量通过的日线", "冻结 ETF 标的池"],
            "validation_status": validation,
            "known_limitations": ["波动率缩放只降低暴露，不使用杠杆。"],
            "causal_preview_supported": True,
            "causal_preview_blocker": None,
        },
        {
            "template_id": "cross-sectional-factor-ranking-v1",
            "name": "横截面因子排序",
            "role": "ACTIVE_BASELINE",
            "family": "CROSS_SECTIONAL_FACTOR_RANKING",
            "implementation_ref": (
                "qtrade.research.multi_asset.RankedFactorAllocation"
            ),
            "description": "对预声明价格因子做横截面百分位排序并组合选股。",
            "data_interval": "1d",
            "universe_scope": "多股票或 ETF",
            "entry_rule": "按加权因子得分选择排名前 K 的标的。",
            "exit_rule": "再平衡时跌出前 K 或趋势资格失效则退出。",
            "parameter_defaults": {
                "factors": "momentum_20",
                "top_k": "3",
                "rebalance_every": "20",
                "reserve": "0.05",
            },
            "required_inputs": [
                "质量通过的日线",
                "冻结标的池",
                "预注册因子定义",
            ],
            "validation_status": validation,
            "known_limitations": ["因子与参数必须在测试窗口之前冻结。"],
            "causal_preview_supported": True,
            "causal_preview_blocker": None,
        },
    ]
    return {
        "classification": "READ_ONLY_IMPLEMENTED_BASELINE_STRATEGY_CATALOG",
        "template_count": len(templates),
        "active_template_count": 4,
        "benchmark_template_count": 1,
        "causal_preview_template_count": 3,
        "templates": templates,
        "selection_creates_experiment": False,
        "selection_registers_strategy": False,
        "paper_runtime_start": False,
        "live_runtime_start": False,
        "arbitrary_code_execution": False,
        "external_order_transmission": False,
        "next_step": "FREEZE_EXPERIMENT_RUN_SPEC_THEN_CAUSAL_BACKTEST",
    }


def _backtest_preview_spec(payload: BacktestPreviewRequest) -> BacktestPreviewSpec:
    return BacktestPreviewSpec(
        start=payload.start,
        end=payload.end,
        factor=payload.factor,
        horizons=payload.horizons,
        top_k=payload.top_k,
        rebalance_every=payload.rebalance_every,
        reserve=payload.reserve,
        trend_window=payload.trend_window,
        volatility_window=payload.volatility_window,
        target_annual_volatility=payload.target_annual_volatility,
        initial_cash=payload.initial_cash,
        transaction_cost_rate=payload.transaction_cost_rate,
        slippage_rate=payload.slippage_rate,
    )


def _experiment_run_spec_draft(
    payload: ExperimentRunSpecDraftRequest,
    dataset: dict[str, object],
) -> dict[str, object]:
    preview_spec = _backtest_preview_spec(payload.backtest)
    split = TimeSplit(
        payload.split.train_start,
        payload.split.train_end,
        payload.split.validation_start,
        payload.split.validation_end,
        payload.split.test_start,
        payload.split.test_end,
    )
    if preview_spec.start != split.test_start or preview_spec.end != split.test_end:
        raise ValueError("backtest window must exactly match the declared test split")
    observed_start = date.fromisoformat(str(dataset["observed_start"])[:10])
    observed_end = date.fromisoformat(str(dataset["observed_end"])[:10])
    if split.train_start < observed_start or split.test_end > observed_end:
        raise ValueError("experiment split exceeds the verified dataset range")

    if payload.template_id == "etf-momentum-rotation-v1":
        if preview_spec.factor != "momentum" or any(
            value is not None
            for value in (
                preview_spec.trend_window,
                preview_spec.volatility_window,
                preview_spec.target_annual_volatility,
            )
        ):
            raise ValueError(
                "momentum rotation template requires momentum without trend/volatility filters"
            )
    elif payload.template_id == "etf-momentum-trend-volatility-v1":
        if preview_spec.factor != "momentum" or any(
            value is None
            for value in (
                preview_spec.trend_window,
                preview_spec.volatility_window,
                preview_spec.target_annual_volatility,
            )
        ):
            raise ValueError(
                "trend/volatility template requires momentum and both risk filters"
            )
    elif preview_spec.factor not in {"reversal", "low_volatility"}:
        raise ValueError(
            "cross-sectional preview requires reversal or low-volatility ranking"
        )

    templates = cast(list[dict[str, object]], _strategy_template_catalog()["templates"])
    template = next(
        item for item in templates if item["template_id"] == payload.template_id
    )
    universe_payload = cast(dict[str, object], dataset["universe"])
    universe = tuple(cast(list[str], universe_payload["symbols"]))
    parameters = (
        ("factor", preview_spec.factor),
        ("horizons", ",".join(str(value) for value in preview_spec.horizons)),
        ("top_k", str(preview_spec.top_k)),
        ("rebalance_every", str(preview_spec.rebalance_every)),
        ("reserve", str(preview_spec.reserve)),
        ("trend_window", str(preview_spec.trend_window)),
        ("volatility_window", str(preview_spec.volatility_window)),
        (
            "target_annual_volatility",
            str(preview_spec.target_annual_volatility),
        ),
        ("initial_cash", str(preview_spec.initial_cash)),
        ("slippage_rate", str(preview_spec.slippage_rate)),
    )
    spec = ExperimentRunSpec(
        experiment_id=payload.experiment_id,
        strategy_id=payload.template_id,
        git_commit=payload.git_commit,
        data_version=str(dataset["data_version"]),
        universe=universe,
        factor_set=tuple(
            f"{preview_spec.factor}_{horizon}" for horizon in preview_spec.horizons
        ),
        label="next_session_portfolio_return",
        split=split,
        model=str(template["implementation_ref"]),
        parameters=parameters,
        cost_model_id="explicit-rate-plus-slippage-v1",
        transaction_cost=preview_spec.transaction_cost_rate,
        seed=payload.seed,
    )
    spec_payload = json.loads(
        json.dumps(
            asdict(spec),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )
    return {
        "classification": (
            "READ_ONLY_COMPLETE_EXPERIMENT_RUN_SPEC_DRAFT_NOT_PERSISTED"
        ),
        "draft_status": "HASHED_COMPLETE_FIELDS_NOT_IMMUTABLY_STORED",
        "template_id": payload.template_id,
        "data_checksum": dataset["checksum"],
        "dataset_point_in_time": universe_payload["point_in_time"],
        "spec_hash": experiment_run_spec_hash(spec),
        "spec": spec_payload,
        "gates": {
            "complete_experiment_run_spec_fields": True,
            "verified_dataset_identity_bound": True,
            "implemented_template_bound": True,
            "chronological_non_overlapping_split": True,
            "immutable_artifact_persisted": False,
            "independent_leakage_review_passed": False,
            "fresh_unseen_test_evidence": False,
        },
        "historical_test_window_previously_available": True,
        "promotion_eligible": False,
        "next_action": (
            "PERSIST_AND_REVIEW_BEFORE_ACCESSING_NEW_DISJOINT_EVIDENCE"
        ),
        "creates_immutable_experiment": False,
        "registry_write": False,
        "paper_runtime_start": False,
        "live_runtime_start": False,
        "state_mutation": False,
        "external_order_transmission": False,
    }


def _hash_bound_experiment_preview(
    payload: ExperimentRunSpecDraftRequest,
    dataset: dict[str, object],
    bars: list[Bar],
) -> dict[str, object]:
    draft = _experiment_run_spec_draft(payload, dataset)
    result = run_backtest_preview(
        bars,
        data_version=str(dataset["data_version"]),
        data_checksum=str(dataset["checksum"]),
        spec=_backtest_preview_spec(payload.backtest),
    )
    binding_identity = {
        "data_checksum": dataset["checksum"],
        "data_version": dataset["data_version"],
        "preview_hash": result["preview_hash"],
        "spec_hash": draft["spec_hash"],
        "template_id": payload.template_id,
    }
    canonical = json.dumps(
        binding_identity,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "classification": "READ_ONLY_HASH_BOUND_EXPERIMENT_PREVIEW_NOT_PERSISTED",
        "template_id": payload.template_id,
        "data_checksum": dataset["checksum"],
        "dataset_point_in_time": draft["dataset_point_in_time"],
        "spec_hash": draft["spec_hash"],
        "preview_hash": result["preview_hash"],
        "binding_hash": sha256(canonical.encode("utf-8")).hexdigest(),
        "spec": draft["spec"],
        "result": result,
        "gates": {
            "single_request_spec_result_binding": True,
            "spec_hash_recomputed_before_run": True,
            "verified_dataset_reloaded_before_run": True,
            "preview_hash_bound_to_spec_hash": True,
            "immutable_artifact_persisted": False,
            "independent_leakage_review_passed": False,
            "fresh_unseen_test_evidence": False,
        },
        "creates_immutable_experiment": False,
        "registry_write": False,
        "paper_runtime_start": False,
        "live_runtime_start": False,
        "state_mutation": False,
        "external_order_transmission": False,
    }


def create_app(
    context: AppContext | None = None,
    auth: ApiAuthConfig | None = None,
    *,
    web_dist_path: Path | None = None,
) -> FastAPI:
    state = context or bootstrap_context()
    auth_config = auth or ApiAuthConfig.from_environment()
    app = FastAPI(title="Q Control Plane", version="0.1.0")

    @app.middleware("http")
    async def accept_built_web_api_prefix(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.scope.get("path", "")
        if path == "/api" or path.startswith("/api/"):
            normalized = path.removeprefix("/api") or "/"
            request.scope["path"] = normalized
            request.scope["raw_path"] = normalized.encode("utf-8")
        return await call_next(request)

    web_dist = web_dist_path or Path(__file__).parents[3] / "web" / "dist"
    if (web_dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="root-assets")
        app.mount("/web/assets", StaticFiles(directory=web_dist / "assets"), name="web-assets")

    @app.get("/health")
    def health(_: str = Depends(_require_role(Role.READ, auth_config))) -> dict[str, Any]:
        return asdict(state.runtime.health())

    @app.get("/deployment")
    def deployment(_: str = Depends(_require_role(Role.READ, auth_config))) -> dict[str, object]:
        return _deployment_provenance()

    @app.get("/system")
    def system(_: str = Depends(_require_role(Role.READ, auth_config))) -> dict[str, Any]:
        return asdict(state.runtime.status)

    @app.get("/risk")
    def risk(_: str = Depends(_require_role(Role.READ, auth_config))) -> dict[str, object]:
        return cast(dict[str, object], _paper_snapshot(state)["risk"])

    @app.get("/orders")
    def orders(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> list[dict[str, object]]:
        return cast(list[dict[str, object]], _paper_snapshot(state)["orders"])

    @app.get("/positions")
    def positions(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], _paper_snapshot(state)["positions"])

    @app.get("/account")
    def account(_: str = Depends(_require_role(Role.READ, auth_config))) -> dict[str, object]:
        return cast(dict[str, object], _paper_snapshot(state)["account"])

    @app.get("/paper-snapshot")
    def paper_snapshot(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        return _paper_snapshot(state)

    @app.get("/metrics")
    def metrics(_: str = Depends(_require_role(Role.READ, auth_config))) -> dict[str, object]:
        return state.metrics.snapshot()

    @app.get("/metrics/prometheus", response_class=Response)
    def prometheus_metrics(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> Response:
        status = state.runtime.status
        health_status = state.runtime.health()
        try:
            content = render_prometheus_metrics(
                state.metrics.snapshot(),
                runtime_mode=status.mode.value,
                risk_state=status.risk_state.value,
                reconciliation_status=health_status.reconciliation_status,
                runtime_running=status.running,
                healthy=health_status.healthy,
            )
        except ValueError as exc:
            raise HTTPException(status_code=503, detail="runtime metrics unavailable") from exc
        return Response(
            content=content,
            headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
        )

    @app.get("/minute-overlay")
    def minute_overlay(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        return _minute_overlay_status(state.minute_overlay_reader)

    @app.get("/minute-research")
    def minute_research(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_minute_dataset(state.minute_manifest_path)
        report = _load_verified_minute_report(
            state.minute_research_report_path, dataset
        )
        return report or {
            "classification": "NO_VERIFIED_MINUTE_RESEARCH_RESULT",
            "promotion": "NOT_EVALUATED",
            "promotion_reasons": ["MISSING_VERIFIED_MINUTE_RESEARCH_ARTIFACT"],
            "minute_dataset": None,
            "candidates": [],
            "cost_stress": [],
            "subgroups": [],
            "leave_one_sector_out": [],
            "daily": [],
            "curve": [],
        }

    @app.get("/minute-bars/{symbol}")
    def minute_bar_series(
        symbol: str,
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_minute_dataset(state.minute_manifest_path)
        empty: dict[str, object] = {
            "symbol": symbol,
            "data_version": None,
            "dataset_checksum": None,
            "observation_type": "HISTORICAL_MINUTE_BAR_SERIES",
            "interval": "1m",
            "total_count": 0,
            "truncated": False,
            "bars": [],
        }
        if dataset is None:
            return empty
        series = _load_minute_bar_series(
            state.minute_manifest_path,
            symbol=symbol,
            data_version=str(dataset["data_version"]),
            expected_checksum=str(dataset["checksum"]),
            expected_symbols=dataset["eligible_symbols"],
            expected_bar_count=dataset["bar_count"],
            expected_trading_days=dataset["trading_days"],
        )
        if series is None:
            return empty
        bars, total_count = series
        return {
            "symbol": symbol,
            "data_version": dataset["data_version"],
            "dataset_checksum": dataset["checksum"],
            "observation_type": "HISTORICAL_MINUTE_BAR_SERIES",
            "interval": "1m",
            "total_count": total_count,
            "truncated": total_count > len(bars),
            "bars": bars,
        }

    @app.get("/five-minute-research")
    def five_minute_research(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_minute_dataset(
            state.five_minute_manifest_path,
            expected_classification=(
                "noncommercial_recent_historical_5m_non_pit_not_eligible"
            ),
            expected_interval="5m",
            expected_session_count=40,
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        report = _load_verified_five_minute_report(
            state.five_minute_research_report_path, dataset
        )
        return report or {
            "classification": "NO_VERIFIED_FIVE_MINUTE_RESEARCH_RESULT",
            "promotion": "NOT_EVALUATED",
            "promotion_reasons": ["MISSING_VERIFIED_FIVE_MINUTE_RESEARCH_ARTIFACT"],
            "minute_dataset": None,
            "folds": [],
            "candidates": [],
            "cost_stress": [],
            "subgroups": [],
            "leave_one_sector_out": [],
            "daily": [],
            "curve": [],
        }

    @app.get("/long-five-minute-research")
    def long_five_minute_research(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_minute_dataset(
            state.long_five_minute_manifest_path,
            expected_classification=(
                "free_historical_qfq_5m_current_non_pit_not_eligible"
            ),
            expected_interval="5m",
            expected_session_count=368,
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        report = _load_verified_five_minute_report(
            state.long_five_minute_research_report_path,
            dataset,
            expected_report_id=(
                "baostock-five-minute-long-expanding-walk-forward-v1"
            ),
            initial_train_sessions=120,
            test_sessions_per_fold=40,
            fold_count=6,
            expected_promotion_reasons=(
                "FIVE_MINUTE_INTERVAL_DOES_NOT_PROVE_ONE_MINUTE_READINESS",
                "LONGER_PROVIDER_CROSSCHECK_REUSES_PREVIOUSLY_OBSERVED_CALENDAR",
                "CURRENT_NON_POINT_IN_TIME_UNIVERSE",
                "QFQ_MAY_USE_FUTURE_ADJUSTMENT_KNOWLEDGE",
                "FREE_HISTORICAL_SOURCE_NOT_AUTHORIZED_REALTIME_FEED",
                "NO_PAPER_OR_SHADOW_SESSION_EVIDENCE",
            ),
            require_execution_diagnostics=True,
        )
        return report or {
            "classification": "NO_VERIFIED_LONG_FIVE_MINUTE_RESEARCH_RESULT",
            "promotion": "NOT_EVALUATED",
            "promotion_reasons": [
                "MISSING_VERIFIED_LONG_FIVE_MINUTE_RESEARCH_ARTIFACT"
            ],
            "minute_dataset": None,
            "folds": [],
            "candidates": [],
            "cost_stress": [],
            "subgroups": [],
            "leave_one_sector_out": [],
            "daily": [],
            "curve": [],
        }

    @app.get("/intraday-factor-robustness")
    def intraday_factor_robustness(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_minute_dataset(
            state.long_five_minute_manifest_path,
            expected_classification=(
                "free_historical_qfq_5m_current_non_pit_not_eligible"
            ),
            expected_interval="5m",
            expected_session_count=368,
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        report = _load_verified_intraday_factor_robustness(
            state.intraday_factor_robustness_path,
            dataset,
        )
        return report or {
            "classification": "NO_VERIFIED_INTRADAY_FACTOR_LONG_WINDOW_EVIDENCE",
            "report_id": None,
            "summary": None,
            "result": None,
            "promotion": "NOT_EVALUATED",
            "promotion_reasons": [
                "MISSING_VERIFIED_INTRADAY_FACTOR_LONG_WINDOW_ARTIFACT"
            ],
            "paper_readiness": "NOT_ELIGIBLE",
            "live_readiness": "DISABLED_REQUIRES_SEPARATE_AUTHORIZATION",
            "state_mutation": False,
            "external_order_transmission": False,
        }

    @app.get("/reserved-tail-diagnostic")
    def reserved_tail_diagnostic(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_minute_dataset(
            state.long_five_minute_manifest_path,
            expected_classification=(
                "free_historical_qfq_5m_current_non_pit_not_eligible"
            ),
            expected_interval="5m",
            expected_session_count=368,
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        report = _load_verified_reserved_tail_diagnostic(
            state.reserved_tail_diagnostic_report_path,
            state.reserved_tail_preregistration_path,
            state.long_five_minute_research_report_path,
            dataset,
        )
        return report or {
            "classification": "NO_VERIFIED_RESERVED_TAIL_DIAGNOSTIC",
            "promotion": "NOT_EVALUATED",
            "promotion_reasons": ["MISSING_VERIFIED_RESERVED_TAIL_ARTIFACT"],
            "gates": [],
            "cost_stress": [],
            "daily": [],
            "leave_one_sector_out": [],
            "curve": [],
            "external_order_transmission": False,
        }

    @app.get("/development-policy-search")
    def development_policy_search(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_minute_dataset(
            state.long_five_minute_manifest_path,
            expected_classification=(
                "free_historical_qfq_5m_current_non_pit_not_eligible"
            ),
            expected_interval="5m",
            expected_session_count=368,
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        report = _load_verified_development_policy_search(
            state.development_policy_search_path,
            dataset,
        )
        return report or {
            "classification": "NO_VERIFIED_DEVELOPMENT_POLICY_SEARCH",
            "candidate_count": 0,
            "candidates": [],
            "promotion": "NOT_EVALUATED",
            "external_order_transmission": False,
        }

    @app.get("/forward-observation")
    def forward_observation(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_minute_dataset(
            state.long_five_minute_manifest_path,
            expected_classification=(
                "free_historical_qfq_5m_current_non_pit_not_eligible"
            ),
            expected_interval="5m",
            expected_session_count=368,
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        development = _load_verified_development_policy_search(
            state.development_policy_search_path,
            dataset,
        )
        preregistration = _load_verified_forward_preregistration(
            state.forward_observation_preregistration_path,
            dataset,
            development,
        )
        report = _load_verified_forward_observation_report(
            state.forward_observation_report_path,
            preregistration,
        )
        if report is not None and preregistration is not None:
            return {**preregistration, **report}
        return report or preregistration or {
            "classification": "NO_VERIFIED_FORWARD_OBSERVATION_LOCK",
            "observation_status": "UNAVAILABLE",
            "future_session_count": 0,
            "minimum_complete_future_sessions": 20,
            "gates": [],
            "cost_stress": [],
            "leave_one_sector_out": [],
            "promotion": "NOT_EVALUATED",
            "realtime_readiness": False,
            "real_data_paper_readiness": False,
            "external_order_transmission": False,
        }

    @app.get("/forward-source-coverage")
    def forward_source_coverage(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_minute_dataset(
            state.long_five_minute_manifest_path,
            expected_classification=(
                "free_historical_qfq_5m_current_non_pit_not_eligible"
            ),
            expected_interval="5m",
            expected_session_count=368,
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        development = _load_verified_development_policy_search(
            state.development_policy_search_path,
            dataset,
        )
        preregistration = _load_verified_forward_preregistration(
            state.forward_observation_preregistration_path,
            dataset,
            development,
        )
        report = _load_verified_forward_source_coverage(
            state.forward_source_coverage_path,
            dataset,
            preregistration,
        )
        return report or {
            "classification": "NO_VERIFIED_FORWARD_SOURCE_COVERAGE",
            "status": "UNAVAILABLE",
            "required_symbol_count": 75,
            "required_bars_per_symbol_session": 48,
            "target_complete_symbol_count": 0,
            "ready_symbol_count": 0,
            "incomplete_symbol_count": 75,
            "incomplete_symbols": [],
            "target_incomplete_symbols": [],
            "fetch_error_symbols": [],
            "coverage": [],
            "ready_for_formal_collection": False,
            "enters_forward_metrics": False,
            "promotion": "NOT_EVALUATED",
            "realtime_readiness": False,
            "real_data_paper_readiness": False,
            "external_order_transmission": False,
        }

    @app.get("/forward-source-coverage-history")
    def forward_source_coverage_history(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_minute_dataset(
            state.long_five_minute_manifest_path,
            expected_classification=(
                "free_historical_qfq_5m_current_non_pit_not_eligible"
            ),
            expected_interval="5m",
            expected_session_count=368,
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        development = _load_verified_development_policy_search(
            state.development_policy_search_path,
            dataset,
        )
        preregistration = _load_verified_forward_preregistration(
            state.forward_observation_preregistration_path,
            dataset,
            development,
        )
        coverage = _load_verified_forward_source_coverage(
            state.forward_source_coverage_path,
            dataset,
            preregistration,
        )
        report = _load_verified_forward_source_coverage_history(
            state.forward_source_coverage_history_path,
            coverage,
            state.forward_source_coverage_release_root,
            dataset,
            preregistration,
        )
        return report or {
            "classification": "NO_VERIFIED_FORWARD_SOURCE_COVERAGE_HISTORY",
            "checkpoint_count": 0,
            "all_checkpoints_monotonic": False,
            "checkpoints": [],
            "enters_forward_metrics": False,
            "promotion": "NOT_EVALUATED",
            "realtime_readiness": False,
            "real_data_paper_readiness": False,
            "external_order_transmission": False,
        }

    @app.get("/minute-research-snapshot")
    def minute_research_snapshot(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        return _load_verified_minute_research_snapshot(state)

    @app.get("/public-minute-audit")
    def public_minute_audit(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_minute_dataset(
            state.long_five_minute_manifest_path,
            expected_classification=(
                "free_historical_qfq_5m_current_non_pit_not_eligible"
            ),
            expected_interval="5m",
            expected_session_count=368,
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        report = _load_verified_public_minute_audit(
            state.public_minute_audit_path,
            dataset,
        )
        return report or {
            "classification": "NO_VERIFIED_PUBLIC_MINUTE_AUDIT",
            "all_valid": False,
            "promotion": "NOT_EVALUATED",
            "first_round": None,
            "repeat_round": None,
            "external_order_transmission": False,
        }

    @app.get("/intraday-observation")
    def intraday_observation(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_minute_dataset(
            state.long_five_minute_manifest_path,
            expected_classification=(
                "free_historical_qfq_5m_current_non_pit_not_eligible"
            ),
            expected_interval="5m",
            expected_session_count=368,
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        report = _load_verified_intraday_observation(
            state.intraday_observation_path,
            dataset,
        )
        return report or {
            "classification": "NO_VERIFIED_INTRADAY_OBSERVATION",
            "session_phase": "UNAVAILABLE",
            "symbol_count": 0,
            "sector_count": 0,
            "same_day_symbol_count": 0,
            "synchronized_finalized_timestamp_count": 0,
            "latest_synchronized_finalized_timestamp": None,
            "observations": [],
            "realtime_readiness": False,
            "real_data_paper_readiness": False,
            "promotion": "NOT_EVALUATED",
            "external_order_transmission": False,
        }

    @app.get("/intraday-breadth-observation")
    def intraday_breadth_observation(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_minute_dataset(
            state.long_five_minute_manifest_path,
            expected_classification=(
                "free_historical_qfq_5m_current_non_pit_not_eligible"
            ),
            expected_interval="5m",
            expected_session_count=368,
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        report = _load_verified_intraday_breadth(
            state.intraday_breadth_path,
            dataset,
        )
        return report or {
            "classification": "NO_VERIFIED_INTRADAY_BREADTH_OBSERVATION",
            "coverage_scope": "UNAVAILABLE",
            "session_phase": "UNAVAILABLE",
            "symbol_count": 0,
            "sector_count": 0,
            "symbols_per_sector": 0,
            "same_day_symbol_count": 0,
            "synchronized_finalized_timestamp_count": 0,
            "latest_synchronized_finalized_timestamp": None,
            "observations": [],
            "sector_summaries": [],
            "realtime_readiness": False,
            "real_data_paper_readiness": False,
            "promotion": "NOT_EVALUATED",
            "external_order_transmission": False,
        }

    @app.get("/intraday-continuity")
    def intraday_continuity(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_minute_dataset(
            state.long_five_minute_manifest_path,
            expected_classification=(
                "free_historical_qfq_5m_current_non_pit_not_eligible"
            ),
            expected_interval="5m",
            expected_session_count=368,
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        detail = _load_verified_intraday_observation(
            state.intraday_observation_path,
            dataset,
        )
        breadth = _load_verified_intraday_breadth(
            state.intraday_breadth_path,
            dataset,
        )
        report = _load_verified_intraday_continuity(
            state.intraday_continuity_path,
            detail,
            breadth,
        )
        return report or {
            "classification": "NO_VERIFIED_INTRADAY_CONTINUITY",
            "trading_day": None,
            "checkpoint_count": 0,
            "all_checkpoints_monotonic": False,
            "checkpoints": [],
            "realtime_readiness": False,
            "real_data_paper_readiness": False,
            "promotion": "NOT_EVALUATED",
            "external_order_transmission": False,
        }

    @app.get("/five-minute-bars/{symbol}")
    def five_minute_bar_series(
        symbol: str,
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_minute_dataset(
            state.five_minute_manifest_path,
            expected_classification=(
                "noncommercial_recent_historical_5m_non_pit_not_eligible"
            ),
            expected_interval="5m",
            expected_session_count=40,
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        empty: dict[str, object] = {
            "symbol": symbol,
            "data_version": None,
            "dataset_checksum": None,
            "observation_type": "HISTORICAL_FIVE_MINUTE_BAR_SERIES",
            "interval": "5m",
            "total_count": 0,
            "truncated": False,
            "bars": [],
        }
        if dataset is None:
            return empty
        series = _load_minute_bar_series(
            state.five_minute_manifest_path,
            symbol=symbol,
            data_version=str(dataset["data_version"]),
            expected_checksum=str(dataset["checksum"]),
            expected_symbols=dataset["eligible_symbols"],
            expected_bar_count=dataset["bar_count"],
            expected_trading_days=dataset["trading_days"],
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
            observation_type="HISTORICAL_FIVE_MINUTE_BAR",
        )
        if series is None:
            return empty
        bars, total_count = series
        return {
            "symbol": symbol,
            "data_version": dataset["data_version"],
            "dataset_checksum": dataset["checksum"],
            "observation_type": "HISTORICAL_FIVE_MINUTE_BAR_SERIES",
            "interval": "5m",
            "total_count": total_count,
            "truncated": total_count > len(bars),
            "bars": bars,
        }

    @app.get("/long-five-minute-bars/{symbol}")
    def long_five_minute_bar_series(
        symbol: str,
        trading_day: date | None = None,
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_minute_dataset(
            state.long_five_minute_manifest_path,
            expected_classification=(
                "free_historical_qfq_5m_current_non_pit_not_eligible"
            ),
            expected_interval="5m",
            expected_session_count=368,
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        available_days = (
            cast(list[str], dataset["trading_days"]) if dataset is not None else []
        )
        selected_day = (
            trading_day.isoformat()
            if trading_day is not None
            else available_days[-1]
            if available_days
            else None
        )
        empty: dict[str, object] = {
            "symbol": symbol,
            "data_version": None,
            "dataset_checksum": None,
            "observation_type": "HISTORICAL_FIVE_MINUTE_BAR_SERIES",
            "interval": "5m",
            "selected_trading_day": selected_day,
            "total_count": 0,
            "truncated": False,
            "bars": [],
        }
        if dataset is None or selected_day is None:
            return empty
        bars = _load_minute_bar_day_series(
            state.long_five_minute_manifest_path,
            symbol=symbol,
            trading_day=selected_day,
            data_version=str(dataset["data_version"]),
            expected_checksum=str(dataset["checksum"]),
            expected_symbols=dataset["eligible_symbols"],
            expected_bar_count=dataset["bar_count"],
            expected_trading_days=available_days,
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        if bars is None:
            return empty
        return {
            "symbol": symbol,
            "data_version": dataset["data_version"],
            "dataset_checksum": dataset["checksum"],
            "observation_type": "HISTORICAL_FIVE_MINUTE_BAR_SERIES",
            "interval": "5m",
            "selected_trading_day": selected_day,
            "total_count": len(bars),
            "truncated": False,
            "bars": bars,
        }

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
        catalog = _load_verified_experiment_catalog(
            state.research_comparison_path, state.research_artifact_root
        )
        if catalog is None or not isinstance(catalog["strategies"], list):
            return []
        return catalog["strategies"]

    @app.get("/strategy-templates")
    def strategy_templates(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        return _strategy_template_catalog()

    @app.get("/experiments")
    def experiments(_: str = Depends(_require_role(Role.READ, auth_config))) -> dict[str, object]:
        catalog = _load_verified_experiment_catalog(
            state.research_comparison_path, state.research_artifact_root
        )
        if catalog is not None and isinstance(catalog["report"], dict):
            return catalog["report"]
        return {
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

    @app.get("/factor-search")
    def factor_search(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        report = _load_verified_factor_search_report(state.factor_search_report_path)
        return report or {
            "classification": "NO_VERIFIED_FACTOR_SEARCH_RESULT",
            "promotion": "NOT_EVALUATED",
            "promotion_reasons": ["MISSING_VERIFIED_FACTOR_SEARCH_ARTIFACT"],
            "factor_diagnostics": [],
            "validation": {"scores": []},
            "test": {},
        }

    @app.get("/factor-robustness")
    def factor_robustness(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        report = _load_verified_robust_factor_search_report(
            state.robust_factor_search_report_path
        )
        return report or {
            "classification": "NO_VERIFIED_ROBUST_FACTOR_RESULT",
            "promotion": "NOT_EVALUATED",
            "promotion_reasons": ["MISSING_VERIFIED_ROBUST_FACTOR_ARTIFACT"],
            "candidate_summaries": [],
            "stress_tests": {"evaluations": []},
            "bootstrap": {"evaluations": []},
            "polluted_audit": {},
        }

    @app.get("/stock-sector-transfer")
    def stock_sector_transfer(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        report = _load_verified_stock_sector_transfer_report(
            state.stock_sector_transfer_report_path
        )
        return report or {
            "classification": "NO_VERIFIED_STOCK_SECTOR_TRANSFER_RESULT",
            "promotion": "NOT_EVALUATED",
            "promotion_reasons": ["MISSING_VERIFIED_STOCK_SECTOR_TRANSFER_ARTIFACT"],
            "yearly": [],
            "sector_only": [],
            "leave_one_sector_out": [],
            "cost_stress": {},
            "bootstrap": {},
            "polluted_audit": {},
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

    @app.post("/experiment-run-spec-draft")
    def experiment_run_spec_draft(
        payload: ExperimentRunSpecDraftRequest,
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_market_dataset(state.historical_manifest_path)
        if dataset is None:
            raise HTTPException(
                status_code=409,
                detail="verified historical dataset unavailable",
            )
        try:
            return _experiment_run_spec_draft(payload, dataset)
        except (ArithmeticError, KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/experiment-run-spec-backtest")
    def experiment_run_spec_backtest(
        payload: ExperimentRunSpecDraftRequest,
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_market_dataset(state.historical_manifest_path)
        if dataset is None:
            raise HTTPException(
                status_code=409,
                detail="verified historical dataset unavailable",
            )
        bars = _load_backtest_preview_bars(
            state.historical_manifest_path,
            data_version=str(dataset["data_version"]),
            expected_checksum=str(dataset["checksum"]),
            expected_universe=dataset["universe"],
            expected_bar_count=dataset["bar_count"],
        )
        if bars is None:
            raise HTTPException(
                status_code=409,
                detail="historical dataset validation failed",
            )
        try:
            return _hash_bound_experiment_preview(payload, dataset, bars)
        except (ArithmeticError, KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/backtest-preview")
    def backtest_preview(
        payload: BacktestPreviewRequest,
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset = _load_verified_market_dataset(state.historical_manifest_path)
        if dataset is None:
            raise HTTPException(status_code=409, detail="verified historical dataset unavailable")
        bars = _load_backtest_preview_bars(
            state.historical_manifest_path,
            data_version=str(dataset["data_version"]),
            expected_checksum=str(dataset["checksum"]),
            expected_universe=dataset["universe"],
            expected_bar_count=dataset["bar_count"],
        )
        if bars is None:
            raise HTTPException(status_code=409, detail="historical dataset validation failed")
        try:
            spec = _backtest_preview_spec(payload)
            return run_backtest_preview(
                bars,
                data_version=str(dataset["data_version"]),
                data_checksum=str(dataset["checksum"]),
                spec=spec,
            )
        except (ArithmeticError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/single-stock-five-minute-backtest")
    def single_stock_five_minute_backtest(
        payload: SingleStockFiveMinuteBacktestRequest,
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        try:
            policy = _single_stock_five_minute_policy(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        dataset = _load_verified_minute_dataset(
            state.long_five_minute_manifest_path,
            expected_classification=(
                "free_historical_qfq_5m_current_non_pit_not_eligible"
            ),
            expected_interval="5m",
            expected_session_count=368,
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        if dataset is None:
            raise HTTPException(
                status_code=409,
                detail="verified long five-minute dataset unavailable",
            )
        trading_days = cast(list[str], dataset["trading_days"])
        eligible_symbols = cast(list[str], dataset["eligible_symbols"])
        instruments = cast(list[dict[str, object]], dataset["instruments"])
        instrument = next(
            (
                row
                for row in instruments
                if row.get("symbol") == payload.symbol
                and row.get("instrument_type") == "STOCK"
            ),
            None,
        )
        if payload.symbol not in eligible_symbols or instrument is None:
            raise HTTPException(
                status_code=422,
                detail="symbol is outside the verified 75-stock universe",
            )
        start_text = payload.start.isoformat()
        end_text = payload.end.isoformat()
        if start_text not in trading_days or end_text not in trading_days:
            raise HTTPException(
                status_code=422,
                detail="backtest boundaries must be verified trading days",
            )
        start_index = trading_days.index(start_text)
        end_index = trading_days.index(end_text)
        selected_session_count = end_index - start_index + 1
        if selected_session_count < 2:
            raise HTTPException(
                status_code=422,
                detail="single-stock backtest requires at least two sessions",
            )
        if selected_session_count > _MAX_SINGLE_STOCK_BACKTEST_SESSIONS:
            raise HTTPException(
                status_code=422,
                detail=(
                    "single-stock backtest is limited to "
                    f"{_MAX_SINGLE_STOCK_BACKTEST_SESSIONS} sessions"
                ),
            )
        bars = _load_single_stock_five_minute_backtest_bars(
            state.long_five_minute_manifest_path,
            symbol=payload.symbol,
            through_day=payload.end,
            data_version=str(dataset["data_version"]),
            expected_checksum=str(dataset["checksum"]),
            expected_bar_count=cast(int, dataset["bar_count"]),
            expected_trading_days=trading_days,
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        if bars is None:
            raise HTTPException(
                status_code=409,
                detail="verified single-stock five-minute bars failed validation",
            )
        selected_days = {
            date.fromisoformat(value)
            for value in trading_days[start_index : end_index + 1]
        }
        try:
            result = run_minute_overlay_backtest(
                bars,
                policy_id="adhoc-single-stock-five-minute-v1",
                policy=policy,
                cost_bps=float(payload.cost_bps),
            )
            return _single_stock_five_minute_preview(
                payload=payload,
                policy=policy,
                result=result,
                selected_days=selected_days,
                selected_trading_days=trading_days[start_index : end_index + 1],
                warmup_session_count=start_index,
                data_version=str(dataset["data_version"]),
                dataset_checksum=str(dataset["checksum"]),
                available_trading_days=trading_days,
                verified_universe_symbol_count=len(eligible_symbols),
                instrument=instrument,
            )
        except (ArithmeticError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def _load_intraday_factor_source(
        start: date,
        end: date,
    ) -> tuple[
        dict[str, object],
        dict[str, tuple[str, str]],
        list[str],
        list[Bar],
    ]:
        dataset = _load_verified_minute_dataset(
            state.long_five_minute_manifest_path,
            expected_classification=(
                "free_historical_qfq_5m_current_non_pit_not_eligible"
            ),
            expected_interval="5m",
            expected_session_count=368,
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        if dataset is None:
            raise HTTPException(
                status_code=409,
                detail="verified long five-minute dataset unavailable",
            )
        trading_days = cast(list[str], dataset["trading_days"])
        start_text = start.isoformat()
        end_text = end.isoformat()
        if start_text not in trading_days or end_text not in trading_days:
            raise HTTPException(
                status_code=422,
                detail="factor boundaries must be verified trading sessions",
            )
        start_index = trading_days.index(start_text)
        end_index = trading_days.index(end_text)
        if start_index > end_index:
            raise HTTPException(
                status_code=422,
                detail="factor research start cannot follow end",
            )
        if end_index + 1 >= len(trading_days):
            raise HTTPException(
                status_code=422,
                detail="factor research requires a next verified trading session",
            )
        selected_session_count = end_index - start_index + 1
        if selected_session_count < _MIN_INTRADAY_FACTOR_SESSIONS:
            raise HTTPException(
                status_code=422,
                detail=(
                    "intraday factor research requires at least "
                    f"{_MIN_INTRADAY_FACTOR_SESSIONS} sessions"
                ),
            )
        if selected_session_count > _MAX_INTRADAY_FACTOR_SESSIONS:
            raise HTTPException(
                status_code=422,
                detail=(
                    "intraday factor research is limited to "
                    f"{_MAX_INTRADAY_FACTOR_SESSIONS} sessions"
                ),
            )
        instruments = cast(list[dict[str, object]], dataset["instruments"])
        stock_instruments = [
            item for item in instruments if item.get("instrument_type") == "STOCK"
        ]
        sectors = {
            cast(str, item["symbol"]): (
                cast(str, item["sector_code"]),
                cast(str, item["sector_name"]),
            )
            for item in stock_instruments
        }
        required_trading_days = trading_days[start_index : end_index + 2]
        bars = _load_intraday_factor_bars(
            state.long_five_minute_manifest_path,
            trading_days=required_trading_days,
            symbols=sorted(sectors),
            data_version=str(dataset["data_version"]),
            expected_checksum=str(dataset["checksum"]),
            expected_bar_count=cast(int, dataset["bar_count"]),
            expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
        )
        if bars is None:
            raise HTTPException(
                status_code=409,
                detail="verified intraday factor source bars failed validation",
            )
        return dataset, sectors, required_trading_days, bars

    @app.post("/intraday-factor-backtest")
    def intraday_factor_backtest(
        payload: IntradayFactorBacktestRequest,
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        try:
            spec = OpeningTripleBullishSpec(
                cost_bps=payload.cost_bps,
                minimum_body_return_bps=payload.minimum_body_return_bps,
                directions=payload.directions,
                combination=payload.combination,
            )
            exit_spec = IntradayExitSpec(
                rule=payload.exit_rule,
                minimum_body_return_bps=(
                    payload.exit_minimum_body_return_bps
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        dataset, sectors, required_trading_days, bars = (
            _load_intraday_factor_source(payload.start, payload.end)
        )
        try:
            result = run_opening_triple_bullish_research(
                bars,
                sectors=sectors,
                start=payload.start,
                end=payload.end,
                spec=spec,
                exit_spec=exit_spec,
            )
            return _intraday_factor_preview(
                payload=payload,
                result=result,
                dataset=dataset,
                required_trading_days=required_trading_days,
                source_bar_count=len(bars),
            )
        except (ArithmeticError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/intraday-factor-discovery")
    def intraday_factor_discovery(
        payload: IntradayFactorDiscoveryRequest,
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        dataset, sectors, required_trading_days, bars = (
            _load_intraday_factor_source(payload.start, payload.end)
        )
        selected_trading_days = required_trading_days[:-1]
        if len(selected_trading_days) < _MIN_INTRADAY_DISCOVERY_SESSIONS:
            raise HTTPException(
                status_code=422,
                detail=(
                    "intraday factor discovery requires at least "
                    f"{_MIN_INTRADAY_DISCOVERY_SESSIONS} sessions"
                ),
            )
        try:
            observations = run_opening_triple_bullish_research(
                bars,
                sectors=sectors,
                start=payload.start,
                end=payload.end,
                spec=OpeningTripleBullishSpec(cost_bps=payload.cost_bps),
            ).observations
            result = discover_opening_direction_rules(
                observations,
                trading_days=tuple(
                    date.fromisoformat(value)
                    for value in selected_trading_days
                ),
                cost_bps=payload.cost_bps,
            )
            return _intraday_factor_discovery_preview(
                payload=payload,
                result=result,
                dataset=dataset,
                required_trading_days=required_trading_days,
                source_bar_count=len(bars),
                symbol_count=len(sectors),
                sector_count=len(set(sectors.values())),
            )
        except (ArithmeticError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/intraday-factor-correlation")
    def intraday_factor_correlation(
        payload: IntradayFactorCorrelationRequest,
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        try:
            specs = tuple(
                OpeningTripleBullishSpec(
                    cost_bps=Decimal("0"),
                    minimum_body_return_bps=rule.minimum_body_return_bps,
                    directions=rule.directions,
                    combination=rule.combination,
                )
                for rule in payload.rules
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        dataset, sectors, required_trading_days, bars = (
            _load_intraday_factor_source(payload.start, payload.end)
        )
        try:
            observations = run_opening_triple_bullish_research(
                bars,
                sectors=sectors,
                start=payload.start,
                end=payload.end,
                spec=OpeningTripleBullishSpec(cost_bps=Decimal("0")),
            ).observations
            result = evaluate_opening_rule_correlations(observations, specs)
            effectiveness = evaluate_opening_rule_effectiveness(
                observations,
                specs,
            )
            return _intraday_factor_correlation_preview(
                payload=payload,
                specs=specs,
                result=result,
                effectiveness=effectiveness,
                dataset=dataset,
                required_trading_days=required_trading_days,
                source_bar_count=len(bars),
            )
        except (ArithmeticError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/portfolio")
    def portfolio(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        return {"positions": len(state.ledger.account.positions)}

    @app.get("/trades")
    def trades(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> list[dict[str, object]]:
        return cast(list[dict[str, object]], _paper_snapshot(state)["trades"])

    @app.get("/pnl")
    def pnl(_: str = Depends(_require_role(Role.READ, auth_config))) -> dict[str, str]:
        return cast(dict[str, str], _paper_snapshot(state)["pnl"])

    @app.get("/audit")
    def audit(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> list[dict[str, object]]:
        try:
            events = (
                state.audit.events[-500:]
                if state.audit_reader is None
                else state.audit_reader(500)
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=503, detail="durable audit history unavailable"
            ) from exc
        return [
            {
                "timestamp": event.timestamp.isoformat(),
                "event_type": event.event_type,
                "severity": event.severity,
                "message": event.message,
                "order_id": event.order_id,
            }
            for event in events
        ]

    @app.get("/reports")
    def reports(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> list[dict[str, object]]:
        if state.daily_report_root is None or not state.daily_report_root.is_dir():
            return []
        paths = list(islice(state.daily_report_root.glob("*.json"), 367))
        if len(paths) > 366:
            return []
        paths.sort(reverse=True)
        summaries: list[dict[str, object]] = []
        for path in paths:
            summary = _verified_daily_report_summary(path)
            if summary is not None:
                summaries.append(summary)
        return summaries

    @app.get("/reporting-status")
    def reporting_status(
        _: str = Depends(_require_role(Role.READ, auth_config)),
    ) -> dict[str, object]:
        if state.reporting_status_reader is None:
            return {
                "classification": "TRUSTED_PAPER_EOD_REPORTING",
                "readiness": "UNCONFIGURED",
                "automatic": False,
                "state": "UNCONFIGURED",
                "venue": None,
                "timezone": "Asia/Shanghai",
                "cutoff": None,
                "data_version": None,
                "strategy_versions": [],
                "last_attempt_day": None,
                "last_generated_day": None,
                "last_report_id": None,
                "last_sha256": None,
                "error_code": "TRUSTED_RUNTIME_IDENTITY_NOT_CONFIGURED",
                "caller_supplied_identity": False,
                "external_order_transmission": False,
            }
        try:
            return state.reporting_status_reader()
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="daily report scheduler status unavailable"
            ) from exc

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


def context_from_runtime(
    bundle: object, *, historical_manifest_path: Path | None = None
) -> AppContext:
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
        factor_search_report_path=Path(
            "runtime/research/akshare-factor-search-v3/report.json"
        ),
        robust_factor_search_report_path=Path(
            "runtime/research/akshare-robust-factor-search-v4/report.json"
        ),
        stock_sector_transfer_report_path=Path(
            "runtime/research/akshare-stock-sector-transfer-v3/report.json"
        ),
        research_artifact_root=Path("runtime/research/akshare-v4"),
        historical_manifest_path=(
            historical_manifest_path
            or Path(
                "runtime/data/akshare/"
                "akshare-sina-etf30-current-20260812-v1-raw-daily-20240101-20260812/"
                "manifest.json"
            )
        ),
        minute_manifest_path=Path(
            "runtime/data/sina-minute/"
            "sina-minute-multiasset-20260825-v2/manifest.json"
        ),
        minute_research_report_path=Path(
            "runtime/research/sina-minute-overlay-v1/report.json"
        ),
        five_minute_manifest_path=Path(
            "runtime/data/sina-minute/"
            "sina-five-minute-multiasset-sw15x5-20260825-v1/manifest.json"
        ),
        five_minute_research_report_path=Path(
            "runtime/research/sina-five-minute-walk-forward-v1/report.json"
        ),
        long_five_minute_manifest_path=Path(
            "runtime/data/baostock-five-minute/"
            "baostock-five-minute-stock-sw15x5-qfq-20250102-20260825-v1/"
            "manifest.json"
        ),
        long_five_minute_research_report_path=Path(
            "runtime/research/baostock-five-minute-long-walk-forward-v1/"
            "report.json"
        ),
        intraday_factor_robustness_path=Path(
            "runtime/research/intraday-factor-long-window-v1/report.json"
        ),
        development_policy_search_path=Path(
            "runtime/research/baostock-development-policy-search-v1/report.json"
        ),
        forward_observation_preregistration_path=Path(
            "docs/preregistrations/baostock-locked-forward-observation-v1.json"
        ),
        forward_observation_report_path=Path(
            "runtime/research/baostock-locked-forward-observation-v1/report.json"
        ),
        forward_source_coverage_path=Path(
            "runtime/research/baostock-forward-source-coverage-v1/report.json"
        ),
        forward_source_coverage_history_path=Path(
            "runtime/research/baostock-forward-source-coverage-v1/history.json"
        ),
        forward_source_coverage_release_root=Path(
            "runtime/research/baostock-forward-source-coverage-v1/releases"
        ),
        reserved_tail_preregistration_path=Path(
            "docs/preregistrations/"
            "baostock-reserved-tail-high-exposure-v1.json"
        ),
        reserved_tail_diagnostic_report_path=Path(
            "runtime/research/baostock-reserved-tail-high-exposure-v1/"
            "report.json"
        ),
        public_minute_audit_path=Path(
            "deployment/evidence/public-minute-audit/report.json"
        ),
        intraday_observation_path=Path(
            "runtime/research/sina-intraday-observation/latest.json"
        ),
        intraday_breadth_path=Path(
            "runtime/research/sina-intraday-breadth/latest.json"
        ),
        intraday_continuity_path=Path(
            "runtime/research/sina-intraday-continuity/latest.json"
        ),
        applied_trade_reader=bundle.journal.applied_trade_payloads,
        sellable_quantity_reader=bundle.sellability.sellable_quantity,
        operational_gate=bundle.risk.dispatch_gate,
        audit_reader=bundle.journal.recent_audit_events,
    )


def _minute_overlay_status(
    reader: Callable[[], dict[str, object]] | None,
) -> dict[str, object]:
    unavailable: dict[str, object] = {
        "classification": "CAUSAL_MINUTE_RISK_OVERLAY_PAPER_SHADOW_ONLY",
        "readiness": "UNCONFIGURED",
        "provider": None,
        "provider_connected": False,
        "bar_interval": "1m",
        "symbol_count": 0,
        "ready_symbol_count": 0,
        "external_order_transmission": False,
        "promotion": "NOT_ELIGIBLE",
        "reasons": [
            "NO_AUTHORIZED_REALTIME_TICK_PROVIDER",
            "NO_ACTIVE_MINUTE_PAPER_SESSION",
        ],
        "symbols": [],
    }
    if reader is None:
        return unavailable
    try:
        value = reader()
        symbols = value.get("symbols")
        reasons = value.get("reasons")
        symbol_count = value.get("symbol_count")
        ready_count = value.get("ready_symbol_count")
        if (
            value.get("classification")
            != "CAUSAL_MINUTE_RISK_OVERLAY_PAPER_SHADOW_ONLY"
            or value.get("bar_interval") != "1m"
            or value.get("promotion") != "NOT_ELIGIBLE"
            or value.get("external_order_transmission") is not False
            or value.get("readiness") not in {"READY", "NOT_READY"}
            or not isinstance(value.get("provider"), str)
            or not isinstance(value.get("provider_connected"), bool)
            or isinstance(symbol_count, bool)
            or not isinstance(symbol_count, int)
            or isinstance(ready_count, bool)
            or not isinstance(ready_count, int)
            or not isinstance(symbols, list)
            or not isinstance(reasons, list)
            or any(not isinstance(reason, str) for reason in reasons)
            or symbol_count != len(symbols)
            or not 0 <= ready_count <= symbol_count
        ):
            raise ValueError("minute overlay status envelope is invalid")
        observed_ready = 0
        for row in symbols:
            if not isinstance(row, dict):
                raise ValueError("minute overlay symbol status is invalid")
            multiplier = Decimal(str(row.get("multiplier")))
            base_quantity = row.get("base_target_quantity")
            corrected_quantity = row.get("corrected_target_quantity")
            timestamp = datetime.fromisoformat(str(row.get("last_bar_timestamp")))
            row_reasons = row.get("reasons")
            if (
                not isinstance(row.get("symbol"), str)
                or not isinstance(row.get("ready"), bool)
                or timestamp.tzinfo is None
                or not multiplier.is_finite()
                or not Decimal("0") <= multiplier <= Decimal("1")
                or isinstance(base_quantity, bool)
                or not isinstance(base_quantity, int)
                or isinstance(corrected_quantity, bool)
                or not isinstance(corrected_quantity, int)
                or not 0 <= corrected_quantity <= base_quantity
                or not isinstance(row_reasons, list)
                or any(not isinstance(reason, str) for reason in row_reasons)
            ):
                raise ValueError("minute overlay symbol arithmetic is invalid")
            observed_ready += int(row["ready"])
        if observed_ready != ready_count:
            raise ValueError("minute overlay ready count is inconsistent")
        expected_ready = (
            value["provider_connected"] is True
            and symbol_count > 0
            and ready_count == symbol_count
        )
        if (value["readiness"] == "READY") != expected_ready:
            raise ValueError("minute overlay readiness is inconsistent")
        return value
    except Exception:
        return {
            **unavailable,
            "readiness": "STATUS_UNAVAILABLE",
            "reasons": ["MINUTE_OVERLAY_STATUS_INVALID_OR_UNAVAILABLE"],
        }


def _paper_trade_rows(state: AppContext) -> list[dict[str, object]]:
    if state.applied_trade_reader is None:
        return []
    orders = {order.internal_order_id: order for order in state.oms.orders}
    rows: list[dict[str, object]] = []
    filled_by_order: dict[str, int] = {}
    trade_ids: set[str] = set()
    try:
        for payload in state.applied_trade_reader():
            order_id = payload["order_id"]
            if not isinstance(order_id, str):
                return []
            order = orders.get(order_id)
            if order is None:
                return []
            trade_id = payload["trade_id"]
            symbol = payload["instrument"]
            side = payload["side"]
            if (
                not isinstance(trade_id, str)
                or not trade_id
                or trade_id in trade_ids
                or symbol != order.request.symbol
                or side != order.request.side.value
            ):
                return []
            price = Decimal(str(payload["price"]))
            fee = Decimal(str(payload["fee"]))
            raw_quantity = payload["quantity"]
            if not isinstance(raw_quantity, int) or isinstance(raw_quantity, bool):
                return []
            quantity = raw_quantity
            timestamp = datetime.fromisoformat(str(payload["timestamp"]))
            if (
                not price.is_finite()
                or price <= 0
                or not fee.is_finite()
                or fee < 0
                or quantity <= 0
                or timestamp.tzinfo is None
                or timestamp < order.created_at
                or timestamp > order.updated_at
            ):
                return []
            trade_ids.add(trade_id)
            filled_by_order[order_id] = filled_by_order.get(order_id, 0) + quantity
            if filled_by_order[order_id] > order.request.quantity:
                return []
            rows.append(
                {
                    "trade_id": trade_id,
                    "internal_order_id": order_id,
                    "strategy_id": order.request.strategy_id,
                    "portfolio_id": order.request.portfolio_id,
                    "symbol": symbol,
                    "side": side,
                    "price": str(price),
                    "quantity": quantity,
                    "fee": str(fee),
                    "timestamp": timestamp.isoformat(),
                    "execution_type": "PAPER_SIMULATED_FILL",
                }
            )
        filled_statuses = {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCEL_PENDING,
            OrderStatus.CANCELLED,
        }
        for order in orders.values():
            applied_quantity = filled_by_order.get(order.internal_order_id, 0)
            if order.filled_quantity != applied_quantity:
                return []
            if applied_quantity > 0 and order.status not in filled_statuses:
                return []
            if order.status is OrderStatus.FILLED and applied_quantity != order.request.quantity:
                return []
        return sorted(rows, key=lambda item: (str(item["timestamp"]), str(item["trade_id"])))
    except (ArithmeticError, KeyError, TypeError, ValueError):
        return []


def _paper_risk_summary(state: AppContext) -> dict[str, object]:
    positions = tuple(state.ledger.account.positions.values())
    position_values = tuple(
        position.average_cost * position.quantity for position in positions
    )
    position_cost_basis = sum(position_values, start=Decimal("0"))
    terminal = {
        OrderStatus.RISK_REJECTED,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
    }
    open_orders = tuple(order for order in state.oms.orders if order.status not in terminal)
    open_buy_notional = sum(
        (
            order.request.price * order.remaining_quantity
            for order in open_orders
            if order.request.side.value == "BUY"
        ),
        start=Decimal("0"),
    )
    concentration = (
        None
        if position_cost_basis <= 0
        else str(max(position_values, default=Decimal("0")) / position_cost_basis)
    )
    realized = sum(
        (position.realized_pnl for position in positions), start=Decimal("0")
    )
    return {
        "state": state.runtime.status.risk_state.value,
        "cash": str(state.ledger.account.cash),
        "position_cost_basis": str(position_cost_basis),
        "open_buy_notional": str(open_buy_notional),
        "gross_exposure": str(position_cost_basis + open_buy_notional),
        "exposure_basis": "COST_BASIS_PLUS_OPEN_BUY_LIMIT_PRICE",
        "largest_position_concentration": concentration,
        "open_order_count": len(open_orders),
        "realized_pnl": str(realized),
        "drawdown": None,
        "valuation_status": "MARKET_PRICE_UNAVAILABLE",
        "gateway_connected": state.metrics.gateway_connected,
        "reconciliation_status": state.metrics.reconciliation_status,
    }


def _paper_snapshot(state: AppContext) -> dict[str, Any]:
    gate = state.operational_gate if state.operational_gate is not None else nullcontext()
    with gate:
        as_of = state.operational_date or datetime.now(ZoneInfo("Asia/Shanghai")).date()
        positions = [
            {
                **asdict(position),
                "sellable_quantity": (
                    None
                    if state.sellable_quantity_reader is None
                    else state.sellable_quantity_reader(position.symbol, as_of)
                ),
                "as_of_date": as_of.isoformat(),
                "cost_basis_value": str(position.average_cost * position.quantity),
                "market_price": None,
                "market_value": None,
                "unrealized_pnl": None,
                "valuation_status": "MARKET_PRICE_UNAVAILABLE",
            }
            for position in sorted(
                state.ledger.account.positions.values(), key=lambda item: item.symbol
            )
        ]
        orders = [
            {
                "internal_order_id": order.internal_order_id,
                "broker_order_id": order.broker_order_id,
                "strategy_id": order.request.strategy_id,
                "portfolio_id": order.request.portfolio_id,
                "symbol": order.request.symbol,
                "side": order.request.side.value,
                "price": str(order.request.price),
                "status": order.status.value,
                "quantity": order.request.quantity,
                "filled_quantity": order.filled_quantity,
                "rejection_reason": order.rejection_reason,
                "created_at": order.created_at.isoformat(),
                "updated_at": order.updated_at.isoformat(),
            }
            for order in state.oms.orders
        ]
        realized = sum(
            (position.realized_pnl for position in state.ledger.account.positions.values()),
            start=Decimal("0"),
        )
        return {
            "account": {
                "cash": str(state.ledger.account.cash),
                "equity": None,
                "valuation_status": "MARKET_PRICE_UNAVAILABLE",
            },
            "pnl": {"realized": str(realized)},
            "positions": positions,
            "orders": orders,
            "trades": _paper_trade_rows(state),
            "risk": _paper_risk_summary(state),
        }


def _verified_daily_report_summary(path: Path) -> dict[str, object] | None:
    try:
        with path.open("rb") as handle:
            raw = handle.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            return None
        envelope = json.loads(raw.decode("utf-8"))
        if not isinstance(envelope, dict):
            return None
        report = envelope.get("report")
        digest = envelope.get("sha256")
        if not isinstance(report, dict) or not _is_sha256(digest):
            return None
        canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), default=str)
        if sha256(canonical.encode()).hexdigest() != digest:
            return None
        report_id = report.get("report_id")
        trading_day = report.get("trading_day")
        generated_at = report.get("generated_at")
        data_version = report.get("data_version")
        versions = report.get("strategy_versions")
        if (
            not isinstance(report_id, str)
            or not report_id
            or len(report_id) > 128
            or path.stem != report_id
            or not isinstance(trading_day, str)
            or date.fromisoformat(trading_day).isoformat() != trading_day
            or not isinstance(generated_at, str)
            or len(generated_at) > 64
            or datetime.fromisoformat(generated_at).tzinfo is None
            or not isinstance(data_version, str)
            or not data_version
            or len(data_version) > 256
            or not isinstance(versions, list)
            or not versions
            or len(versions) > 128
            or any(
                not isinstance(version, str) or not version or len(version) > 256
                for version in versions
            )
        ):
            return None
        return {
            "report_id": report_id,
            "trading_day": trading_day,
            "generated_at": generated_at,
            "data_version": data_version,
            "strategy_versions": versions,
            "sha256": digest,
        }
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


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


def _load_verified_factor_search_report(path: Path | None) -> dict[str, object] | None:
    report = _load_verified_research_report(path)
    if report is None or path is None:
        return None
    try:
        if (
            report.get("classification")
            != "bounded_factor_search_current_universe_non_pit"
            or report.get("promotion") != "NOT_ELIGIBLE"
        ):
            return None
        reasons = report["promotion_reasons"]
        artifacts = report["artifacts"]
        budget = report["research_budget"]
        portfolio = report["portfolio_selection"]
        if (
            not isinstance(reasons, list)
            or not reasons
            or not all(isinstance(reason, str) and reason for reason in reasons)
            or not isinstance(artifacts, dict)
            or set(artifacts)
            != {"factor_diagnostics.json", "validation_scores.json", "test_metrics.json"}
            or not isinstance(budget, dict)
            or not isinstance(portfolio, dict)
        ):
            return None
        loaded: dict[str, object] = {}
        for filename, expected_hash in artifacts.items():
            if not isinstance(filename, str) or not _is_sha256(expected_hash):
                return None
            artifact_path = path.parent / filename
            with artifact_path.open("rb") as handle:
                raw = handle.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024 or sha256(raw).hexdigest() != expected_hash:
                return None
            loaded[filename] = json.loads(raw.decode("utf-8"))

        diagnostics = loaded["factor_diagnostics.json"]
        validation = loaded["validation_scores.json"]
        test = loaded["test_metrics.json"]
        if (
            not isinstance(diagnostics, list)
            or not 1 <= len(diagnostics) <= 5
            or not isinstance(validation, dict)
            or not isinstance(test, dict)
        ):
            return None
        factor_ids: list[str] = []
        for diagnostic in diagnostics:
            if not isinstance(diagnostic, dict) or not isinstance(diagnostic.get("metrics"), dict):
                return None
            factor_id = diagnostic.get("factor_id")
            periods = diagnostic.get("periods")
            minimum = diagnostic.get("instruments_minimum")
            metrics = diagnostic["metrics"]
            if (
                not isinstance(factor_id, str)
                or not factor_id
                or factor_id in factor_ids
                or not isinstance(periods, int)
                or isinstance(periods, bool)
                or periods < 2
                or not isinstance(minimum, int)
                or isinstance(minimum, bool)
                or minimum < 5
                or not _finite_metric_fields(
                    metrics,
                    ("rank_ic", "icir", "long_short_spread", "factor_turnover"),
                )
            ):
                return None
            factor_ids.append(factor_id)

        candidates = validation.get("candidates")
        scores = validation.get("scores")
        selected = validation.get("selected_candidate_id")
        candidate_count = validation.get("candidate_count")
        if (
            not isinstance(candidates, list)
            or not isinstance(scores, list)
            or not isinstance(selected, str)
            or not isinstance(candidate_count, int)
            or isinstance(candidate_count, bool)
            or not 1 <= candidate_count <= 15
            or len(candidates) != candidate_count
            or len(scores) != candidate_count
        ):
            return None
        candidate_ids = []
        for candidate in candidates:
            if not isinstance(candidate, dict) or not isinstance(candidate.get("parameters"), list):
                return None
            candidate_id = candidate.get("candidate_id")
            if (
                not isinstance(candidate_id, str)
                or not candidate_id
                or candidate_id in candidate_ids
                or candidate_id == "buy-hold"
            ):
                return None
            candidate_ids.append(candidate_id)
        score_values: list[tuple[Decimal, str]] = []
        for score in scores:
            if (
                not isinstance(score, list)
                or len(score) != 2
                or not isinstance(score[0], str)
                or score[0] not in candidate_ids
                or not isinstance(score[1], str)
            ):
                return None
            value = Decimal(score[1])
            if not value.is_finite():
                return None
            score_values.append((value, score[0]))
        if len({identity for _, identity in score_values}) != candidate_count:
            return None
        if selected != max(score_values)[1] or portfolio.get("selected_candidate_id") != selected:
            return None
        if (
            budget.get("validation_candidates_run") != candidate_count
            or budget.get("validation_candidate_cap") != 15
            or portfolio.get("benchmark_excluded_from_active_selection") is not True
        ):
            return None

        selected_metrics = test.get("selected_metrics")
        benchmark_metrics = test.get("buy_hold_metrics")
        if (
            test.get("selected_candidate_id") != selected
            or not isinstance(selected_metrics, dict)
            or not isinstance(benchmark_metrics, dict)
            or not _finite_metric_fields(
                selected_metrics,
                (
                    "total_return",
                    "sharpe",
                    "maximum_drawdown",
                    "turnover",
                    "transaction_costs",
                ),
            )
            or not _finite_metric_fields(
                benchmark_metrics,
                ("total_return", "sharpe", "maximum_drawdown"),
            )
            or not _is_sha256(test.get("walk_forward_result_hash"))
        ):
            return None
        excess = Decimal(str(test.get("terminal_excess_return")))
        if (
            not excess.is_finite()
            or excess
            != Decimal(str(selected_metrics["total_return"]))
            - Decimal(str(benchmark_metrics["total_return"]))
        ):
            return None
        return {
            **report,
            "factor_diagnostics": diagnostics,
            "validation": validation,
            "test": test,
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


def _as_finite_decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric research metric")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("research metric must be finite")
    return result


def _load_verified_robust_factor_search_report(
    path: Path | None,
) -> dict[str, object] | None:
    report = _load_verified_research_report(path)
    if report is None or path is None:
        return None
    try:
        if (
            report.get("classification")
            != "post_selection_robust_factor_search_current_universe_non_pit"
            or report.get("promotion") != "NOT_ELIGIBLE"
        ):
            return None
        reasons = report.get("promotion_reasons")
        artifacts = report.get("artifacts")
        budget = report.get("research_budget")
        selection = report.get("selection")
        gate_report = report.get("robustness_gate")
        expected_artifacts = {
            "candidate_windows.json",
            "candidate_summaries.json",
            "stress_tests.json",
            "bootstrap.json",
            "polluted_audit_2026.json",
        }
        if (
            not isinstance(reasons, list)
            or not reasons
            or not all(isinstance(reason, str) and reason for reason in reasons)
            or not isinstance(artifacts, dict)
            or set(artifacts) != expected_artifacts
            or not isinstance(budget, dict)
            or not isinstance(selection, dict)
            or not isinstance(gate_report, dict)
        ):
            return None
        loaded: dict[str, object] = {}
        for filename, expected_hash in artifacts.items():
            if not isinstance(filename, str) or not _is_sha256(expected_hash):
                return None
            artifact_path = path.parent / filename
            with artifact_path.open("rb") as handle:
                raw = handle.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024 or sha256(raw).hexdigest() != expected_hash:
                return None
            loaded[filename] = json.loads(raw.decode("utf-8"))

        windows_payload = loaded["candidate_windows.json"]
        summaries = loaded["candidate_summaries.json"]
        stress = loaded["stress_tests.json"]
        bootstrap = loaded["bootstrap.json"]
        audit = loaded["polluted_audit_2026.json"]
        window_ids = [
            "2024Q1",
            "2024Q2",
            "2024Q3",
            "2024Q4",
            "2025Q1",
            "2025Q2",
            "2025Q3",
            "2025Q4",
        ]
        window_ranges = {
            "2024Q1": ("2024-01-01", "2024-03-31"),
            "2024Q2": ("2024-04-01", "2024-06-30"),
            "2024Q3": ("2024-07-01", "2024-09-30"),
            "2024Q4": ("2024-10-01", "2024-12-31"),
            "2025Q1": ("2025-01-01", "2025-03-31"),
            "2025Q2": ("2025-04-01", "2025-06-30"),
            "2025Q3": ("2025-07-01", "2025-09-30"),
            "2025Q4": ("2025-10-01", "2025-12-31"),
        }
        if (
            not isinstance(windows_payload, list)
            or len(windows_payload) != 36
            or not isinstance(summaries, list)
            or len(summaries) != 36
            or not isinstance(stress, dict)
            or not isinstance(bootstrap, dict)
            or not isinstance(audit, dict)
            or budget.get("frozen_candidates_run") != 36
            or budget.get("validation_windows_per_candidate") != 8
            or selection.get("candidate_count") != 36
            or selection.get("window_ids") != window_ids
            or selection.get("start") != "2024-01-01"
            or selection.get("end") != "2025-12-31"
            or selection.get("benchmark_excluded_from_selection") is not True
            or selection.get("polluted_2026_audit_excluded_from_selection") is not True
            or selection.get("window_gate")
            != {
                "positive_window_ratio_minimum": "0.75",
                "median_window_sharpe_strictly_above": "0",
                "worst_window_sharpe_minimum": "-1",
            }
        ):
            return None

        windows_by_candidate: dict[str, list[dict[str, object]]] = {}
        for candidate in windows_payload:
            if not isinstance(candidate, dict) or not isinstance(
                candidate.get("parameters"), list
            ):
                return None
            candidate_id = candidate.get("candidate_id")
            candidate_windows = candidate.get("windows")
            if (
                not isinstance(candidate_id, str)
                or not candidate_id
                or candidate_id in windows_by_candidate
                or candidate_id == "buy-hold"
                or not isinstance(candidate_windows, list)
                or len(candidate_windows) != 8
            ):
                return None
            parsed_windows: list[dict[str, object]] = []
            for index, window in enumerate(candidate_windows):
                if not isinstance(window, dict):
                    return None
                window_id = window.get("window_id")
                if (
                    window.get("candidate_id") != candidate_id
                    or window_id != window_ids[index]
                    or not isinstance(window_id, str)
                    or (window.get("start"), window.get("end"))
                    != window_ranges[window_id]
                    or not _finite_metric_fields(
                        window,
                        (
                            "total_return",
                            "sharpe",
                            "maximum_drawdown",
                            "turnover",
                            "transaction_costs",
                        ),
                    )
                    or not isinstance(window.get("number_of_trades"), int)
                    or isinstance(window.get("number_of_trades"), bool)
                ):
                    return None
                parsed_windows.append(window)
            windows_by_candidate[candidate_id] = parsed_windows

        summary_by_id: dict[str, dict[str, object]] = {}
        numeric_summary_fields = (
            "positive_window_ratio",
            "median_window_sharpe",
            "worst_window_sharpe",
            "sharpe_dispersion",
            "median_window_return",
            "full_period_return",
            "full_period_sharpe",
            "full_period_turnover",
            "full_period_transaction_costs",
        )
        for rank, summary in enumerate(summaries, start=1):
            if not isinstance(summary, dict):
                return None
            candidate_id = summary.get("candidate_id")
            if (
                not isinstance(candidate_id, str)
                or candidate_id not in windows_by_candidate
                or candidate_id in summary_by_id
                or summary.get("selection_rank") != rank
                or summary.get("window_count") != 8
                or not _finite_metric_fields(summary, numeric_summary_fields)
            ):
                return None
            candidate_windows = windows_by_candidate[candidate_id]
            returns = [_as_finite_decimal(window["total_return"]) for window in candidate_windows]
            sharpes = [_as_finite_decimal(window["sharpe"]) for window in candidate_windows]
            if (
                _as_finite_decimal(summary["positive_window_ratio"])
                != Decimal(sum(value > 0 for value in returns)) / 8
                or _as_finite_decimal(summary["median_window_sharpe"]) != median(sharpes)
                or _as_finite_decimal(summary["worst_window_sharpe"]) != min(sharpes)
                or _as_finite_decimal(summary["sharpe_dispersion"])
                != max(sharpes) - min(sharpes)
                or _as_finite_decimal(summary["median_window_return"]) != median(returns)
            ):
                return None
            summary_by_id[candidate_id] = summary

        ranked_ids = [
            str(summary["candidate_id"])
            for summary in sorted(
                summaries,
                key=lambda item: (
                    -_as_finite_decimal(cast(dict[str, object], item)["positive_window_ratio"]),
                    -_as_finite_decimal(cast(dict[str, object], item)["median_window_sharpe"]),
                    -_as_finite_decimal(cast(dict[str, object], item)["worst_window_sharpe"]),
                    _as_finite_decimal(cast(dict[str, object], item)["sharpe_dispersion"]),
                    -_as_finite_decimal(cast(dict[str, object], item)["median_window_return"]),
                    _as_finite_decimal(cast(dict[str, object], item)["full_period_turnover"]),
                    str(cast(dict[str, object], item)["candidate_id"]),
                ),
            )
        ]
        if ranked_ids != [str(summary["candidate_id"]) for summary in summaries]:
            return None
        eligible_ids = [
            candidate_id
            for candidate_id in ranked_ids
            if _as_finite_decimal(summary_by_id[candidate_id]["positive_window_ratio"])
            >= Decimal("0.75")
            and _as_finite_decimal(summary_by_id[candidate_id]["median_window_sharpe"])
            > 0
            and _as_finite_decimal(summary_by_id[candidate_id]["worst_window_sharpe"])
            >= Decimal("-1")
        ]
        expected_best_summary = {
            key: value for key, value in summaries[0].items() if key != "selection_rank"
        }
        if (
            selection.get("best_observed_candidate_id") != ranked_ids[0]
            or selection.get("best_observed_summary") != expected_best_summary
            or selection.get("window_gate_eligible_candidate_ids") != eligible_ids
            or budget.get("window_eligible_candidates") != len(eligible_ids)
            or stress.get("window_eligible_candidate_ids") != eligible_ids
        ):
            return None

        stress_evaluations = stress.get("evaluations")
        bootstrap_evaluations = bootstrap.get("evaluations")
        if (
            not isinstance(stress_evaluations, list)
            or not isinstance(bootstrap_evaluations, list)
            or len(stress_evaluations) != len(eligible_ids)
            or len(bootstrap_evaluations) != len(eligible_ids)
            or budget.get("cost_scenarios_per_eligible_candidate") != 4
            or budget.get("universe_subsets_per_eligible_candidate") != 6
            or budget.get("bootstrap_replications_per_eligible_candidate") != 1000
        ):
            return None
        bootstrap_by_id = {
            item.get("candidate_id"): item
            for item in bootstrap_evaluations
            if isinstance(item, dict) and isinstance(item.get("candidate_id"), str)
        }
        if set(bootstrap_by_id) != set(eligible_ids):
            return None

        passed_ids: list[str] = []
        gate_results: list[dict[str, object]] = []
        for index, evaluation in enumerate(stress_evaluations):
            if not isinstance(evaluation, dict):
                return None
            candidate_id = evaluation.get("candidate_id")
            if candidate_id != eligible_ids[index] or not isinstance(candidate_id, str):
                return None
            costs = evaluation.get("cost_stresses")
            neighbors = evaluation.get("parameter_neighbors")
            universes = evaluation.get("universe_subsets")
            if (
                not isinstance(costs, list)
                or not isinstance(neighbors, dict)
                or not isinstance(universes, dict)
                or [item.get("scenario_id") for item in costs if isinstance(item, dict)]
                != ["half_cost", "base_cost", "double_cost", "triple_cost"]
            ):
                return None
            double_return: Decimal | None = None
            for cost in costs:
                if not isinstance(cost, dict) or not isinstance(cost.get("metrics"), dict):
                    return None
                if not _finite_metric_fields(
                    cost["metrics"], ("total_return", "sharpe", "maximum_drawdown")
                ):
                    return None
                if cost.get("scenario_id") == "double_cost":
                    double_return = _as_finite_decimal(cost["metrics"]["total_return"])
            neighbor_rows = neighbors.get("results")
            universe_rows = universes.get("results")
            if (
                double_return is None
                or not isinstance(neighbor_rows, list)
                or not neighbor_rows
                or not isinstance(universe_rows, list)
                or len(universe_rows) != 6
            ):
                return None
            neighbor_returns = []
            for row in neighbor_rows:
                if not isinstance(row, dict):
                    return None
                neighbor_returns.append(_as_finite_decimal(row.get("full_period_return")))
            universe_returns = []
            for row in universe_rows:
                metrics = row.get("metrics") if isinstance(row, dict) else None
                if not isinstance(metrics, dict) or not _finite_metric_fields(
                    metrics, ("total_return", "sharpe", "maximum_drawdown")
                ):
                    return None
                universe_returns.append(_as_finite_decimal(metrics["total_return"]))
            neighbor_ratio = Decimal(sum(value > 0 for value in neighbor_returns)) / len(
                neighbor_returns
            )
            universe_ratio = Decimal(sum(value > 0 for value in universe_returns)) / 6
            if (
                _as_finite_decimal(neighbors.get("positive_ratio")) != neighbor_ratio
                or _as_finite_decimal(universes.get("positive_ratio")) != universe_ratio
            ):
                return None
            bootstrap_item = bootstrap_by_id[candidate_id]
            if (
                bootstrap_item.get("method") != "stationary_bootstrap"
                or bootstrap_item.get("replications") != 1000
                or bootstrap_item.get("expected_block_length") != 20
                or bootstrap_item.get("seed") != 20260825
            ):
                return None
            probability = _as_finite_decimal(bootstrap_item.get("probability_positive"))
            p05 = _as_finite_decimal(bootstrap_item.get("p05_total_return"))
            p50 = _as_finite_decimal(bootstrap_item.get("median_total_return"))
            p95 = _as_finite_decimal(bootstrap_item.get("p95_total_return"))
            if not Decimal("0") <= probability <= Decimal("1") or not p05 <= p50 <= p95:
                return None
            summary = summary_by_id[candidate_id]
            expected_criteria = [
                [
                    "positive_window_ratio_at_least_0.75",
                    _as_finite_decimal(summary["positive_window_ratio"])
                    >= Decimal("0.75"),
                ],
                [
                    "median_window_sharpe_above_zero",
                    _as_finite_decimal(summary["median_window_sharpe"]) > 0,
                ],
                [
                    "worst_window_sharpe_at_least_minus_1",
                    _as_finite_decimal(summary["worst_window_sharpe"]) >= Decimal("-1"),
                ],
                ["doubled_cost_total_return_above_zero", double_return > 0],
                [
                    "parameter_neighbor_positive_ratio_at_least_0.5",
                    neighbor_ratio >= Decimal("0.5"),
                ],
                [
                    "universe_subset_positive_ratio_at_least_two_thirds",
                    universe_ratio >= Decimal(2) / Decimal(3),
                ],
                ["bootstrap_probability_positive_at_least_0.75", probability >= Decimal("0.75")],
            ]
            passed = all(bool(item[1]) for item in expected_criteria)
            if evaluation.get("gate") != {"passed": passed, "criteria": expected_criteria}:
                return None
            gate_result = {
                "candidate_id": candidate_id,
                "passed": passed,
                "criteria": expected_criteria,
            }
            gate_results.append(gate_result)
            if passed:
                passed_ids.append(candidate_id)

        robust_candidate_id = passed_ids[0] if passed_ids else None
        audited_candidate_id = robust_candidate_id or (
            eligible_ids[0] if eligible_ids else ranked_ids[0]
        )
        expected_conclusion = (
            "ROBUST_CANDIDATE_PASSED_PREDECLARED_GATE"
            if robust_candidate_id
            else "NO_CANDIDATE_PASSED_PREDECLARED_ROBUSTNESS_GATE"
        )
        if (
            selection.get("robust_candidate_id") != robust_candidate_id
            or selection.get("audited_candidate_id") != audited_candidate_id
            or gate_report.get("passed") is not (robust_candidate_id is not None)
            or gate_report.get("candidate_results") != gate_results
            or gate_report.get("conclusion") != expected_conclusion
            or audit.get("classification") != "POLLUTED_AUDIT_ONLY"
            or audit.get("excluded_from_selection") is not True
            or audit.get("period_previously_viewed_by_project") is not True
            or audit.get("audited_candidate_id") != audited_candidate_id
            or audit.get("robust_candidate_id") != robust_candidate_id
            or audit.get("start") != "2026-01-01"
            or audit.get("end") != "2026-08-21"
        ):
            return None
        selected_metrics = audit.get("selected_metrics")
        benchmark_metrics = audit.get("buy_hold_metrics")
        if (
            not isinstance(selected_metrics, dict)
            or not isinstance(benchmark_metrics, dict)
            or not _finite_metric_fields(
                selected_metrics,
                ("total_return", "sharpe", "maximum_drawdown", "turnover", "transaction_costs"),
            )
            or not _finite_metric_fields(
                benchmark_metrics, ("total_return", "sharpe", "maximum_drawdown")
            )
            or _as_finite_decimal(audit.get("terminal_excess_return"))
            != _as_finite_decimal(selected_metrics["total_return"])
            - _as_finite_decimal(benchmark_metrics["total_return"])
        ):
            return None
        return {
            **report,
            "candidate_summaries": summaries,
            "stress_tests": stress,
            "bootstrap": bootstrap,
            "polluted_audit": audit,
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


def _finite_metric_fields(value: dict[str, object], fields: tuple[str, ...]) -> bool:
    try:
        return all(Decimal(str(value[field])).is_finite() for field in fields)
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return False


def _load_verified_stock_sector_transfer_report(
    path: Path | None,
) -> dict[str, object] | None:
    report = _load_verified_research_report(path)
    if report is None or path is None:
        return None
    try:
        artifacts = report.get("artifacts")
        data = report.get("data")
        strategy = report.get("locked_strategy")
        formal = report.get("formal_period")
        gate = report.get("transfer_gate")
        execution = report.get("execution_contract")
        reasons = report.get("promotion_reasons")
        expected_artifacts = {
            "yearly.json",
            "sector_only.json",
            "leave_one_sector_out.json",
            "cost_stress.json",
            "bootstrap.json",
            "polluted_audit_2026.json",
        }
        if (
            report.get("classification")
            != "locked_etf_factor_stock_sector_transfer_non_pit"
            or report.get("promotion") != "NOT_ELIGIBLE"
            or not isinstance(reasons, list)
            or not reasons
            or not all(isinstance(reason, str) and reason for reason in reasons)
            or not isinstance(artifacts, dict)
            or set(artifacts) != expected_artifacts
            or not isinstance(data, dict)
            or not isinstance(strategy, dict)
            or not isinstance(formal, dict)
            or not isinstance(gate, dict)
            or not isinstance(execution, dict)
        ):
            return None
        if (
            data.get("symbol_count") != 75
            or data.get("sector_count") != 15
            or data.get("stocks_per_sector") != 5
            or data.get("adjustment") != "hfq"
            or data.get("point_in_time_universe") is not False
            or strategy
            != {
                "candidate_id": "reversal-10-top3-20",
                "factor": "reversal",
                "lookback_sessions": 10,
                "top_k": 3,
                "rebalance_every_sessions": 20,
                "reserve": "0.05",
                "chosen_before_stock_history_outcomes": True,
                "parameter_tuning_on_stock_results": False,
            }
            or formal.get("start") != "2019-01-01"
            or formal.get("end") != "2025-12-31"
            or execution.get("maximum_stale_sessions") != 40
            or execution.get("limit_up_down_execution_model") is not False
        ):
            return None

        loaded: dict[str, object] = {}
        for filename, expected_hash in artifacts.items():
            if not isinstance(filename, str) or not _is_sha256(expected_hash):
                return None
            target = path.parent / filename
            with target.open("rb") as handle:
                raw = handle.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024 or sha256(raw).hexdigest() != expected_hash:
                return None
            loaded[filename] = json.loads(raw.decode("utf-8"))

        yearly = loaded["yearly.json"]
        sector_only = loaded["sector_only.json"]
        leave_one = loaded["leave_one_sector_out.json"]
        cost = loaded["cost_stress.json"]
        bootstrap = loaded["bootstrap.json"]
        audit = loaded["polluted_audit_2026.json"]
        if (
            not isinstance(yearly, list)
            or len(yearly) != 7
            or not isinstance(sector_only, list)
            or len(sector_only) != 15
            or not isinstance(leave_one, list)
            or len(leave_one) != 15
            or not isinstance(cost, dict)
            or not isinstance(bootstrap, dict)
            or not isinstance(audit, dict)
        ):
            return None

        yearly_returns: list[Decimal] = []
        if [item.get("year") for item in yearly if isinstance(item, dict)] != list(
            range(2019, 2026)
        ):
            return None
        for item in yearly:
            metrics = item.get("metrics") if isinstance(item, dict) else None
            if not isinstance(metrics, dict) or not _finite_metric_fields(
                metrics, ("total_return", "sharpe", "maximum_drawdown")
            ):
                return None
            yearly_returns.append(_as_finite_decimal(metrics["total_return"]))

        sector_returns: list[Decimal] = []
        sector_codes: list[str] = []
        sector_symbols: set[str] = set()
        for item in sector_only:
            if not isinstance(item, dict):
                return None
            code = item.get("sector_code")
            name = item.get("sector_name")
            symbols = item.get("symbols")
            metrics = item.get("metrics")
            if (
                not isinstance(code, str)
                or not code
                or not isinstance(name, str)
                or not name
                or not isinstance(symbols, list)
                or len(symbols) != 5
                or len(set(symbols)) != 5
                or not all(isinstance(symbol, str) for symbol in symbols)
                or sector_symbols.intersection(symbols)
                or not isinstance(metrics, dict)
                or not _finite_metric_fields(
                    metrics, ("total_return", "sharpe", "maximum_drawdown")
                )
            ):
                return None
            sector_codes.append(code)
            sector_symbols.update(symbols)
            sector_returns.append(_as_finite_decimal(metrics["total_return"]))
        if len(set(sector_codes)) != 15 or len(sector_symbols) != 75:
            return None

        leave_returns: list[Decimal] = []
        excluded_codes: list[str] = []
        for item in leave_one:
            metrics = item.get("metrics") if isinstance(item, dict) else None
            code = item.get("excluded_sector_code") if isinstance(item, dict) else None
            if (
                not isinstance(item, dict)
                or not isinstance(code, str)
                or item.get("retained_symbol_count") != 70
                or not isinstance(metrics, dict)
                or not _finite_metric_fields(
                    metrics, ("total_return", "sharpe", "maximum_drawdown")
                )
            ):
                return None
            excluded_codes.append(code)
            leave_returns.append(_as_finite_decimal(metrics["total_return"]))
        if excluded_codes != sector_codes:
            return None

        base = cost.get("base")
        doubled = cost.get("double")
        if (
            not isinstance(base, dict)
            or not isinstance(doubled, dict)
            or not _finite_metric_fields(
                base, ("total_return", "sharpe", "maximum_drawdown")
            )
            or not _finite_metric_fields(
                doubled, ("total_return", "sharpe", "maximum_drawdown")
            )
            or formal.get("full_metrics") != base
            or formal.get("doubled_cost_metrics") != doubled
            or formal.get("bootstrap") != bootstrap
        ):
            return None
        if (
            bootstrap.get("method") != "stationary_bootstrap"
            or bootstrap.get("replications") != 1000
            or bootstrap.get("expected_block_length") != 20
            or bootstrap.get("seed") != 20260825
        ):
            return None
        probability = _as_finite_decimal(bootstrap.get("probability_positive"))
        p05 = _as_finite_decimal(bootstrap.get("p05_total_return"))
        p50 = _as_finite_decimal(bootstrap.get("median_total_return"))
        p95 = _as_finite_decimal(bootstrap.get("p95_total_return"))
        if not Decimal("0") <= probability <= Decimal("1") or not p05 <= p50 <= p95:
            return None

        expected_counts = (
            sum(value > 0 for value in yearly_returns),
            sum(value > 0 for value in sector_returns),
            sum(value > 0 for value in leave_returns),
        )
        if (
            formal.get("positive_year_count") != expected_counts[0]
            or formal.get("positive_sector_only_count") != expected_counts[1]
            or formal.get("positive_leave_one_sector_out_count") != expected_counts[2]
        ):
            return None
        expected_criteria = {
            "positive_years_at_least_5_of_7": expected_counts[0] >= 5,
            "positive_sector_only_at_least_9_of_15": expected_counts[1] >= 9,
            "positive_leave_one_sector_out_at_least_12_of_15": expected_counts[2]
            >= 12,
            "doubled_cost_total_return_above_zero": (
                _as_finite_decimal(doubled["total_return"]) > 0
            ),
            "bootstrap_probability_positive_at_least_0.75": probability
            >= Decimal("0.75"),
            "maximum_drawdown_at_least_minus_0.35": (
                _as_finite_decimal(base["maximum_drawdown"]) >= Decimal("-0.35")
            ),
        }
        passed = all(expected_criteria.values())
        if gate != {"passed": passed, "criteria": expected_criteria}:
            return None

        strategy_metrics = audit.get("strategy_metrics")
        benchmark_metrics = audit.get("equal_weight_metrics")
        report_audit = report.get("polluted_2026_audit")
        if (
            audit.get("classification") != "POLLUTED_AUDIT_ONLY"
            or audit.get("excluded_from_gate") is not True
            or not isinstance(strategy_metrics, dict)
            or not isinstance(benchmark_metrics, dict)
            or not _finite_metric_fields(
                strategy_metrics, ("total_return", "sharpe", "maximum_drawdown")
            )
            or not _finite_metric_fields(
                benchmark_metrics, ("total_return", "sharpe", "maximum_drawdown")
            )
            or _as_finite_decimal(audit.get("terminal_excess_return"))
            != _as_finite_decimal(strategy_metrics["total_return"])
            - _as_finite_decimal(benchmark_metrics["total_return"])
            or not isinstance(report_audit, dict)
            or report_audit.get("strategy_metrics") != strategy_metrics
            or report_audit.get("equal_weight_metrics") != benchmark_metrics
            or report_audit.get("excluded_from_gate") is not True
        ):
            return None
        return {
            **report,
            "yearly": yearly,
            "sector_only": sector_only,
            "leave_one_sector_out": leave_one,
            "cost_stress": cost,
            "bootstrap": bootstrap,
            "polluted_audit": audit,
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
            "risk_summary": leader_artifacts["risk_summary"],
            "run_metadata": leader_artifacts["run_metadata"],
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


def _load_verified_experiment_catalog(
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
        rows = report["experiments"]
        reasons = report["promotion_reasons"]
        report_split = _validated_experiment_split(report["split"])
        if (
            not isinstance(rows, list)
            or not rows
            or not isinstance(reasons, list)
            or not reasons
            or not all(isinstance(reason, str) and reason for reason in reasons)
        ):
            return None
        root = artifact_root.resolve()
        experiments: list[dict[str, object]] = []
        strategies: list[dict[str, object]] = []
        experiment_ids: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                return None
            experiment_id = row.get("experiment_id")
            scenario_type = row.get("scenario_type")
            if (
                not isinstance(experiment_id, str)
                or experiment_id in experiment_ids
                or not _is_sha256(row.get("spec_hash"))
                or scenario_type not in {"PRIMARY", "SENSITIVITY", "COST_STRESS"}
            ):
                return None
            experiment_ids.add(experiment_id)
            verified = _load_verified_experiment_analytics(row, report, root)
            if verified is None:
                return None
            record = verified["record"]
            if not isinstance(record, dict):
                return None
            model = record["model"]
            parameters = record["hyperparameters"]
            split = _validated_experiment_split(record["split"])
            factors = record["factor_set"]
            universe = record["universe"]
            cost = Decimal(str(record["transaction_cost"]))
            if (
                not isinstance(model, str)
                or not model
                or not isinstance(parameters, dict)
                or not all(
                    isinstance(key, str) and isinstance(value, str)
                    for key, value in parameters.items()
                )
                or not isinstance(factors, list)
                or not all(isinstance(value, str) and value for value in factors)
                or not isinstance(universe, list)
                or not cost.is_finite()
                or cost < 0
                or split != report_split
            ):
                return None
            curve = verified["curve"]
            if (
                not isinstance(curve, list)
                or not curve
                or not (
                    split["train_start"]
                    <= curve[0].get("session", "")
                    <= split["train_end"]
                )
                or curve[-1].get("session") != split["test_end"]
            ):
                return None
            experiment = {
                **row,
                "model": model,
                "parameters": parameters,
                "split": split,
                "factor_set": factors,
                "transaction_cost": str(cost),
                "universe_size": len(universe),
                "git_commit": record["git_commit"],
                "data_version": record["data_version"],
                "record_hash": verified["record_hash"],
                "artifact_hashes": verified["artifact_hashes"],
                "evidence_status": "VERIFIED_NOT_ELIGIBLE",
            }
            experiments.append(experiment)
            strategies.append(
                {
                    "strategy_id": record["experiment_id"],
                    "name": model,
                    "version": str(verified["record_hash"])[:12],
                    "family": _research_family(model),
                    "status": "NOT_ELIGIBLE",
                    "role": "NONE",
                    "metrics": record["metrics"],
                    "scenario_type": row["scenario_type"],
                    "data_version": record["data_version"],
                    "git_commit": record["git_commit"],
                    "evidence_status": "VERIFIED_NOT_ELIGIBLE",
                    "lineage_status": "NOT_REGISTERED",
                }
            )
        enriched = dict(report)
        enriched["experiments"] = experiments
        enriched["lineage"] = []
        enriched["lineage_status"] = "NOT_REGISTERED"
        return {"report": enriched, "strategies": strategies}
    except (InvalidOperation, KeyError, OSError, TypeError, ValueError):
        return None


def _research_family(model: str) -> str:
    if model == "buy-hold":
        return "BUY_AND_HOLD_BENCHMARK"
    if "trend" in model:
        return "ETF_MOMENTUM_TREND"
    if "vol" in model:
        return "ETF_MOMENTUM_VOLATILITY"
    return "ETF_MOMENTUM_ROTATION"


def _validated_experiment_split(value: object) -> dict[str, str]:
    keys = (
        "train_start",
        "train_end",
        "validation_start",
        "validation_end",
        "test_start",
        "test_end",
    )
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("invalid experiment split")
    raw = [value[key] for key in keys]
    if not all(isinstance(item, str) for item in raw):
        raise ValueError("invalid experiment split")
    boundaries = [date.fromisoformat(item) for item in raw]
    if not (
        boundaries[0] <= boundaries[1]
        < boundaries[2]
        <= boundaries[3]
        < boundaries[4]
        <= boundaries[5]
    ):
        raise ValueError("invalid experiment split")
    return {key: item for key, item in zip(keys, raw, strict=True)}


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
        risk_summary = None
        run_metadata = None
        if "risk" in artifact_manifest or "logs" in artifact_manifest:
            if "risk" not in artifact_manifest or "logs" not in artifact_manifest:
                raise ValueError("incomplete backtest diagnostics")
            risk_summary = _validated_backtest_risk(
                _load_hashed_json(artifacts_dir / "risk.json", artifact_manifest["risk"]),
                curve,
                trades,
                Decimal(str(record.get("transaction_cost"))),
            )
            run_metadata = _validated_backtest_run_metadata(
                _load_hashed_json(artifacts_dir / "logs.json", artifact_manifest["logs"]),
                len(curve),
            )
        return {
            "row": row,
            "record": record,
            "record_hash": record_hash,
            "artifact_hashes": {
                name: artifact_manifest[name]
                for name in ("equity", "drawdown", "trades", "positions", "risk", "logs")
                if name in artifact_manifest
            },
            "curve": curve,
            "trades": trades,
            "positions": positions,
            "monthly_returns": _monthly_returns(curve),
            "risk_summary": risk_summary,
            "run_metadata": run_metadata,
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
    core = {"equity", "drawdown", "trades", "positions"}
    if set(result) not in (core, core | {"risk", "logs"}):
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


def _validated_backtest_risk(
    value: object,
    curve: list[dict[str, str]],
    trades: list[dict[str, object]],
    transaction_cost_rate: Decimal,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("invalid backtest risk artifact")
    drawdown = Decimal(str(value.get("maximum_drawdown")))
    costs = Decimal(str(value.get("transaction_costs")))
    turnover = Decimal(str(value.get("turnover")))
    observed_drawdown = min(Decimal(point["drawdown"]) for point in curve)
    observed_costs = sum((Decimal(str(trade["cost"])) for trade in trades), Decimal("0"))
    observed_notional = sum(
        (Decimal(str(trade["notional"])) for trade in trades), Decimal("0")
    )
    initial_equity = Decimal(curve[0]["equity"])
    if (
        not transaction_cost_rate.is_finite()
        or transaction_cost_rate < 0
        or any(
            Decimal(str(trade["cost"]))
            != Decimal(str(trade["notional"])) * transaction_cost_rate
            for trade in trades
        )
    ):
        raise ValueError("invalid backtest cost policy")
    if (
        not drawdown.is_finite()
        or abs(drawdown - observed_drawdown) > Decimal("1e-24")
        or not costs.is_finite()
        or costs < 0
        or costs != observed_costs
        or not turnover.is_finite()
        or turnover < 0
        or turnover != observed_notional / initial_equity
    ):
        raise ValueError("invalid backtest risk artifact")
    return {
        "maximum_drawdown": str(drawdown),
        "transaction_costs": str(costs),
        "turnover": str(turnover),
    }


def _validated_backtest_run_metadata(value: object, session_count: int) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or value.get("engine") != "DailyMultiAssetBacktester"
        or value.get("timing") != "close decision, next open execution"
        or value.get("session_count") != session_count
    ):
        raise ValueError("invalid backtest run metadata")
    return {
        "engine": value["engine"],
        "timing": value["timing"],
        "session_count": session_count,
    }


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


def _load_verified_minute_dataset(
    path: Path | None,
    *,
    expected_classification: str = (
        "noncommercial_recent_historical_1m_non_pit_not_eligible"
    ),
    expected_interval: str = "1m",
    expected_session_count: int = 5,
    expected_times: tuple[datetime_time, ...] = SINA_COMPLETE_SESSION_TIMES,
) -> dict[str, object] | None:
    if path is None or not path.is_file():
        return None
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(evidence, dict):
            return None
        digest = evidence.get("evidence_sha256")
        unsigned = {key: value for key, value in evidence.items() if key != "evidence_sha256"}
        canonical = json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        manifest = evidence.get("manifest")
        universe = evidence.get("universe")
        eligible = evidence.get("eligible_symbols")
        trading_days = evidence.get("trading_days")
        if (
            not _is_sha256(digest)
            or sha256(canonical.encode("utf-8")).hexdigest() != digest
            or not isinstance(manifest, dict)
            or not isinstance(universe, dict)
            or not isinstance(eligible, list)
            or not eligible
            or not all(isinstance(symbol, str) for symbol in eligible)
            or len(set(eligible)) != len(eligible)
            or not isinstance(trading_days, list)
            or len(trading_days) != expected_session_count
            or sorted(trading_days) != trading_days
            or not all(isinstance(value, str) for value in trading_days)
            or evidence.get("classification") != expected_classification
            or (evidence.get("bar_interval") or "1m") != expected_interval
            or evidence.get("realtime_readiness") is not False
            or evidence.get("real_data_paper_readiness") is not False
            or evidence.get("external_order_transmission") is not False
            or universe.get("point_in_time") is not False
        ):
            return None
        parsed_days = [date.fromisoformat(value) for value in trading_days]
        bar_count = evidence.get("bar_count")
        symbol_count = evidence.get("symbol_count")
        session_count = evidence.get("session_count")
        session_bars = evidence.get("session_bar_count_per_symbol")
        checksum = manifest.get("checksum")
        data_version = manifest.get("data_version")
        if (
            manifest.get("quality_status") != "PASSED"
            or not _is_sha256(checksum)
            or not isinstance(data_version, str)
            or not isinstance(bar_count, int)
            or not isinstance(symbol_count, int)
            or symbol_count != len(eligible)
            or session_count != len(parsed_days)
            or session_bars != len(expected_times)
            or bar_count != symbol_count * session_count * session_bars
        ):
            return None
        etfs = universe.get("etfs", [])
        stocks = universe.get("stocks")
        if (
            not isinstance(etfs, list)
            or not isinstance(stocks, list)
            or not all(isinstance(symbol, str) for symbol in etfs)
            or not all(isinstance(row, dict) for row in stocks)
        ):
            return None
        stock_metadata: dict[str, dict[str, object]] = {}
        for row in stocks:
            symbol = row.get("symbol")
            if (
                not isinstance(symbol, str)
                or not isinstance(row.get("name"), str)
                or not isinstance(row.get("sector_code"), str)
                or not isinstance(row.get("sector_name"), str)
                or symbol in stock_metadata
            ):
                return None
            stock_metadata[symbol] = row
        stock_count_by_sector = Counter(
            str(row["sector_code"]) for row in stock_metadata.values()
        )
        if (
            evidence.get("etf_count")
            != len(set(eligible) & set(cast(list[str], etfs)))
            or evidence.get("stock_count") != len(stock_metadata)
            or evidence.get("sector_count")
            != len(stock_count_by_sector)
            or evidence.get("stock_count_by_sector")
            != dict(sorted(stock_count_by_sector.items()))
            or not set(stock_metadata).issubset(eligible)
        ):
            return None
        parquet = _open_verified_market_parquet(
            path,
            expected_checksum=cast(str, checksum),
            expected_bar_count=bar_count,
        )
        if parquet is None:
            return None
        instruments = [
            (
                {
                    "symbol": symbol,
                    "instrument_type": "STOCK",
                    "name": stock_metadata[symbol]["name"],
                    "sector_code": stock_metadata[symbol]["sector_code"],
                    "sector_name": stock_metadata[symbol]["sector_name"],
                }
                if symbol in stock_metadata
                else {
                    "symbol": symbol,
                    "instrument_type": "ETF",
                    "name": symbol,
                    "sector_code": None,
                    "sector_name": None,
                }
            )
            for symbol in eligible
        ]
        return {
            "provider": evidence.get("provider", "Sina public web historical endpoint"),
            "provider_version": evidence.get("provider_version"),
            "classification": evidence["classification"],
            "bar_interval": expected_interval,
            "data_version": data_version,
            "quality_status": manifest["quality_status"],
            "checksum": checksum,
            "evidence_sha256": digest,
            "bar_count": bar_count,
            "symbol_count": symbol_count,
            "etf_count": evidence["etf_count"],
            "stock_count": evidence["stock_count"],
            "sector_count": evidence["sector_count"],
            "session_count": session_count,
            "session_bar_count_per_symbol": session_bars,
            "trading_days": trading_days,
            "observed_start": evidence.get("observed_start"),
            "observed_end": evidence.get("observed_end"),
            "eligible_symbols": eligible,
            "instruments": instruments,
            "point_in_time": False,
            "future_adjustment_knowledge_possible": evidence.get(
                "future_adjustment_knowledge_possible", False
            ),
            "realtime_readiness": False,
            "real_data_paper_readiness": False,
            "external_order_transmission": False,
        }
    except (
        KeyError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
        pa.ArrowException,
    ):
        return None


def _load_verified_minute_report(
    path: Path | None, dataset: dict[str, object] | None
) -> dict[str, object] | None:
    if path is None or dataset is None or not path.is_file():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            return None
        digest = report.get("evidence_sha256")
        unsigned = {key: value for key, value in report.items() if key != "evidence_sha256"}
        canonical = json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        report_dataset = report.get("dataset")
        causal = report.get("causal_contract")
        selected = report.get("selected")
        split = report.get("split")
        artifacts = report.get("artifacts")
        expected_artifacts = {
            "candidates.json",
            "cost_stress.json",
            "subgroups.json",
            "leave_one_sector_out.json",
            "daily.json",
            "curve.json",
        }
        if (
            not _is_sha256(digest)
            or sha256(canonical.encode("utf-8")).hexdigest() != digest
            or report.get("schema_version") != 1
            or report.get("promotion") != "NOT_ELIGIBLE"
            or report.get("realtime_readiness") is not False
            or report.get("real_data_paper_readiness") is not False
            or report.get("external_order_transmission") is not False
            or not isinstance(report_dataset, dict)
            or report_dataset.get("data_version") != dataset["data_version"]
            or report_dataset.get("checksum") != dataset["checksum"]
            or report_dataset.get("evidence_sha256") != dataset["evidence_sha256"]
            or report_dataset.get("bar_count") != dataset["bar_count"]
            or report_dataset.get("symbol_count") != dataset["symbol_count"]
            or report_dataset.get("etf_count") != dataset["etf_count"]
            or report_dataset.get("stock_count") != dataset["stock_count"]
            or report_dataset.get("sector_count") != dataset["sector_count"]
            or not isinstance(causal, dict)
            or causal.get("signal_observed_at") != "BAR_CLOSE_T"
            or causal.get("exposure_change_at") != "BAR_OPEN_T_PLUS_1"
            or causal.get("same_bar_signal_execution") is not False
            or causal.get("external_order_transmission") is not False
            or not isinstance(selected, dict)
            or not isinstance(selected.get("policy_id"), str)
            or not _is_sha256(selected.get("policy_hash"))
            or not isinstance(split, dict)
            or split.get("candidate_count") != 7
            or split.get("train_days") != cast(list[str], dataset["trading_days"])[:3]
            or split.get("test_days") != cast(list[str], dataset["trading_days"])[3:]
            or not isinstance(artifacts, dict)
            or set(artifacts) != expected_artifacts
            or not all(_is_sha256(value) for value in artifacts.values())
        ):
            return None
        loaded: dict[str, object] = {}
        for name in expected_artifacts:
            content = _read_bounded_file(
                path.parent / name, _MAX_MINUTE_EVIDENCE_JSON_BYTES
            )
            if content is None or sha256(content).hexdigest() != artifacts[name]:
                return None
            loaded[name] = json.loads(content)
        candidates = loaded["candidates.json"]
        cost_stress = loaded["cost_stress.json"]
        subgroups = loaded["subgroups.json"]
        sector_rows = loaded["leave_one_sector_out.json"]
        daily = loaded["daily.json"]
        curve = loaded["curve.json"]
        if (
            not isinstance(candidates, list)
            or len(candidates) != 7
            or not all(isinstance(row, dict) for row in candidates)
            or len({row.get("policy_id") for row in candidates}) != 7
            or candidates[0].get("policy_id") != selected["policy_id"]
            or not isinstance(cost_stress, list)
            or [Decimal(str(row.get("cost_bps"))) for row in cost_stress]
            != [Decimal("0"), Decimal("5"), Decimal("10"), Decimal("20")]
            or not isinstance(subgroups, list)
            or {row.get("group") for row in subgroups} != {"ETF", "STOCK"}
            or not isinstance(sector_rows, list)
            or len(sector_rows) != dataset["sector_count"]
            or len({row.get("excluded_sector_code") for row in sector_rows})
            != dataset["sector_count"]
            or not isinstance(daily, list)
            or [row.get("trading_day") for row in daily] != dataset["trading_days"]
            or [row.get("partition") for row in daily] != [
                "TRAIN",
                "TRAIN",
                "TRAIN",
                "TEST",
                "TEST",
            ]
            or not isinstance(curve, list)
            or len(curve)
            != int(cast(int, dataset["session_count"]))
            * len(SINA_COMPLETE_SESSION_TIMES)
        ):
            return None
        stocks_by_sector: dict[str, list[str]] = defaultdict(list)
        for instrument in cast(list[dict[str, object]], dataset["instruments"]):
            if instrument.get("instrument_type") == "STOCK":
                code = instrument.get("sector_code")
                symbol = instrument.get("symbol")
                if not isinstance(code, str) or not isinstance(symbol, str):
                    return None
                stocks_by_sector[code].append(symbol)
        sector_row_by_code = {
            row.get("excluded_sector_code"): row for row in sector_rows
        }
        if set(sector_row_by_code) != set(stocks_by_sector):
            return None
        for code, symbols in stocks_by_sector.items():
            row = sector_row_by_code[code]
            if (
                row.get("excluded_symbols") != symbols
                or row.get("excluded_symbol_count") != len(symbols)
                or row.get("retained_symbol_count")
                != cast(int, dataset["symbol_count"]) - len(symbols)
            ):
                return None
        previous: datetime | None = None
        allowed_days = set(cast(list[str], dataset["trading_days"]))
        allowed_times = set(SINA_COMPLETE_SESSION_TIMES)
        for row in curve:
            if not isinstance(row, dict):
                return None
            instant = datetime.fromisoformat(str(row.get("timestamp")))
            local = instant.astimezone(ZoneInfo("Asia/Shanghai"))
            base = Decimal(str(row.get("base_equity")))
            overlay = Decimal(str(row.get("overlay_equity")))
            if (
                instant.tzinfo is None
                or previous is not None
                and instant <= previous
                or local.date().isoformat() not in allowed_days
                or local.time() not in allowed_times
                or not base.is_finite()
                or not overlay.is_finite()
                or base <= 0
                or overlay <= 0
            ):
                return None
            previous = instant
        return {
            **report,
            "minute_dataset": dataset,
            "candidates": candidates,
            "cost_stress": cost_stress,
            "subgroups": subgroups,
            "leave_one_sector_out": sector_rows,
            "daily": daily,
            "curve": curve,
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


def _load_verified_five_minute_report(
    path: Path | None,
    dataset: dict[str, object] | None,
    *,
    expected_report_id: str = "sina-five-minute-expanding-walk-forward-v1",
    initial_train_sessions: int = 16,
    test_sessions_per_fold: int = 6,
    fold_count: int = 4,
    expected_promotion_reasons: tuple[str, ...] | None = None,
    require_execution_diagnostics: bool = False,
) -> dict[str, object] | None:
    if path is None or dataset is None or not path.is_file():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            return None
        digest = report.get("evidence_sha256")
        unsigned = {key: value for key, value in report.items() if key != "evidence_sha256"}
        canonical = json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        report_dataset = report.get("dataset")
        causal = report.get("causal_contract")
        walk_forward = report.get("walk_forward")
        aggregate = report.get("aggregate")
        artifacts = report.get("artifacts")
        expected_artifacts = {
            "candidates.json",
            "folds.json",
            "cost_stress.json",
            "subgroups.json",
            "leave_one_sector_out.json",
            "daily.json",
            "curve.json",
        }
        if (
            not _is_sha256(digest)
            or sha256(canonical.encode("utf-8")).hexdigest() != digest
            or report.get("schema_version") != 1
            or report.get("report_id") != expected_report_id
            or report.get("promotion") != "NOT_ELIGIBLE"
            or expected_promotion_reasons is not None
            and report.get("promotion_reasons") != list(expected_promotion_reasons)
            or report.get("realtime_readiness") is not False
            or report.get("real_data_paper_readiness") is not False
            or report.get("external_order_transmission") is not False
            or not isinstance(report_dataset, dict)
            or report_dataset.get("data_version") != dataset["data_version"]
            or report_dataset.get("checksum") != dataset["checksum"]
            or report_dataset.get("evidence_sha256") != dataset["evidence_sha256"]
            or report_dataset.get("bar_interval") != "5m"
            or report_dataset.get("bar_count") != dataset["bar_count"]
            or report_dataset.get("symbol_count") != dataset["symbol_count"]
            or report_dataset.get("etf_count") != dataset["etf_count"]
            or report_dataset.get("stock_count") != dataset["stock_count"]
            or report_dataset.get("sector_count") != dataset["sector_count"]
            or report_dataset.get("session_count") != dataset["session_count"]
            or not isinstance(causal, dict)
            or causal.get("signal_observed_at") != "FIVE_MINUTE_BAR_CLOSE_T"
            or causal.get("exposure_change_at") != "NEXT_FIVE_MINUTE_BAR_OPEN"
            or causal.get("same_bar_signal_execution") is not False
            or causal.get("candidate_state")
            != "ALL_CANDIDATES_CAUSALLY_SHADOW_WARMED"
            or causal.get("external_order_transmission") is not False
            or not isinstance(walk_forward, dict)
            or walk_forward.get("initial_train_sessions") != initial_train_sessions
            or walk_forward.get("test_sessions_per_fold") != test_sessions_per_fold
            or walk_forward.get("fold_count") != fold_count
            or walk_forward.get("candidate_count") != 7
            or walk_forward.get("test_windows_disjoint") is not True
            or not isinstance(aggregate, dict)
            or aggregate.get("cost_bps") != 5
            or require_execution_diagnostics
            and not isinstance(aggregate.get("execution"), dict)
            or not isinstance(artifacts, dict)
            or set(artifacts) != expected_artifacts
            or not all(_is_sha256(value) for value in artifacts.values())
        ):
            return None

        loaded: dict[str, object] = {}
        for name in expected_artifacts:
            content = _read_bounded_file(
                path.parent / name, _MAX_MINUTE_EVIDENCE_JSON_BYTES
            )
            if content is None or sha256(content).hexdigest() != artifacts[name]:
                return None
            loaded[name] = json.loads(content)
        candidates = loaded["candidates.json"]
        folds = loaded["folds.json"]
        cost_stress = loaded["cost_stress.json"]
        subgroups = loaded["subgroups.json"]
        sector_rows = loaded["leave_one_sector_out.json"]
        daily = loaded["daily.json"]
        curve = loaded["curve.json"]
        trading_days = cast(list[str], dataset["trading_days"])
        expected_used_session_count = (
            initial_train_sessions + test_sessions_per_fold * fold_count
        )
        expected_unused_session_count = (
            len(trading_days) - expected_used_session_count
        )
        if (
            not isinstance(candidates, list)
            or len(candidates) != 7 * fold_count
            or not all(isinstance(row, dict) for row in candidates)
            or not isinstance(folds, list)
            or len(folds) != fold_count
            or not all(isinstance(row, dict) for row in folds)
            or not isinstance(cost_stress, list)
            or [Decimal(str(row.get("cost_bps"))) for row in cost_stress]
            != [Decimal("0"), Decimal("5"), Decimal("10"), Decimal("20")]
            or not isinstance(subgroups, list)
            or {row.get("group") for row in subgroups}
            != {
                name
                for name, count in (
                    ("ETF", dataset["etf_count"]),
                    ("STOCK", dataset["stock_count"]),
                )
                if count
            }
            or {row.get("symbol_count") for row in subgroups}
            != {
                count
                for count in (dataset["etf_count"], dataset["stock_count"])
                if count
            }
            or not isinstance(sector_rows, list)
            or len(sector_rows) != dataset["sector_count"]
            or len({row.get("excluded_sector_code") for row in sector_rows})
            != dataset["sector_count"]
            or not isinstance(daily, list)
            or len(daily) != test_sessions_per_fold * fold_count
            or not isinstance(curve, list)
            or len(curve)
            != test_sessions_per_fold
            * fold_count
            * len(SINA_FIVE_MINUTE_SESSION_TIMES)
        ):
            return None

        candidates_by_fold: dict[int, list[dict[str, object]]] = defaultdict(list)
        for row in candidates:
            fold_number = row.get("fold")
            if not isinstance(fold_number, int):
                return None
            candidates_by_fold[fold_number].append(row)
        selected_sequence = []
        expected_test_days: list[str] = []
        fold_execution_rows: list[dict[str, object]] = []
        for index, fold in enumerate(folds):
            fold_number = index + 1
            train_end = initial_train_sessions + index * test_sessions_per_fold
            test_end = train_end + test_sessions_per_fold
            rows = candidates_by_fold.get(fold_number, [])
            ranks = [row.get("rank") for row in rows]
            if (
                fold.get("fold") != fold_number
                or fold.get("train_days") != trading_days[:train_end]
                or fold.get("test_days") != trading_days[train_end:test_end]
                or len(rows) != 7
                or not all(isinstance(rank, int) for rank in ranks)
            ):
                return None
            ranked = sorted(rows, key=lambda row: cast(int, row["rank"]))
            if (
                [row["rank"] for row in ranked] != list(range(1, 8))
                or len({row.get("policy_id") for row in rows}) != 7
                or fold.get("selected_policy_id") != ranked[0].get("policy_id")
                or fold.get("selected_policy_hash") != ranked[0].get("policy_hash")
            ):
                return None
            selected_sequence.append(fold["selected_policy_id"])
            expected_test_days.extend(cast(list[str], fold["test_days"]))
            if require_execution_diagnostics:
                execution = fold.get("execution")
                expected_observations = (
                    test_sessions_per_fold
                    * len(SINA_FIVE_MINUTE_SESSION_TIMES)
                    * cast(int, dataset["symbol_count"])
                )
                if not _valid_minute_execution_diagnostics(
                    execution,
                    expected_symbol_count=cast(int, dataset["symbol_count"]),
                    expected_observation_count=expected_observations,
                ):
                    return None
                fold_execution_rows.append(cast(dict[str, object], execution))
        if (
            walk_forward.get("selected_policy_sequence") != selected_sequence
            or expected_test_days
            != trading_days[
                initial_train_sessions : initial_train_sessions
                + test_sessions_per_fold * fold_count
            ]
            or walk_forward.get(
                "used_session_count",
                expected_used_session_count,
            )
            != expected_used_session_count
            or walk_forward.get(
                "unused_trailing_sessions",
                expected_unused_session_count,
            )
            != expected_unused_session_count
            or expected_unused_session_count > 0
            and (
                walk_forward.get("processed_session_count")
                != expected_used_session_count
                or walk_forward.get("processed_bar_count")
                != expected_used_session_count
                * cast(int, dataset["symbol_count"])
                * len(SINA_FIVE_MINUTE_SESSION_TIMES)
                or walk_forward.get("trailing_sessions_entered_backtest") is not False
            )
            or [row.get("trading_day") for row in daily] != expected_test_days
            or [row.get("fold") for row in daily]
            != [
                fold
                for fold in range(1, fold_count + 1)
                for _ in range(test_sessions_per_fold)
            ]
        ):
            return None

        stocks_by_sector: dict[str, list[str]] = defaultdict(list)
        for instrument in cast(list[dict[str, object]], dataset["instruments"]):
            if instrument.get("instrument_type") == "STOCK":
                code = instrument.get("sector_code")
                symbol = instrument.get("symbol")
                if not isinstance(code, str) or not isinstance(symbol, str):
                    return None
                stocks_by_sector[code].append(symbol)
        sector_row_by_code = {
            row.get("excluded_sector_code"): row for row in sector_rows
        }
        if set(sector_row_by_code) != set(stocks_by_sector):
            return None
        for code, symbols in stocks_by_sector.items():
            row = sector_row_by_code[code]
            if (
                row.get("excluded_symbols") != symbols
                or row.get("excluded_symbol_count") != len(symbols)
                or row.get("retained_symbol_count")
                != cast(int, dataset["symbol_count"]) - len(symbols)
            ):
                return None

        previous: datetime | None = None
        base_values = []
        overlay_values = []
        allowed_times = set(SINA_FIVE_MINUTE_SESSION_TIMES)
        allowed_days = set(expected_test_days)
        for row in curve:
            if not isinstance(row, dict):
                return None
            instant = datetime.fromisoformat(str(row.get("timestamp")))
            local = instant.astimezone(ZoneInfo("Asia/Shanghai"))
            base = float(str(row.get("base_equity")))
            overlay = float(str(row.get("overlay_equity")))
            if (
                instant.tzinfo is None
                or previous is not None
                and instant <= previous
                or local.date().isoformat() not in allowed_days
                or local.time() not in allowed_times
                or base <= 0
                or overlay <= 0
            ):
                return None
            previous = instant
            base_values.append(base)
            overlay_values.append(overlay)
        aggregate_base = aggregate.get("base")
        aggregate_overlay = aggregate.get("overlay")
        aggregate_execution = aggregate.get("execution")
        if (
            not isinstance(aggregate_base, dict)
            or not isinstance(aggregate_overlay, dict)
            or aggregate_base.get("point_count") != len(curve)
            or aggregate_overlay.get("point_count") != len(curve)
            or not isclose(
                float(str(aggregate_base.get("total_return"))), base_values[-1] - 1
            )
            or not isclose(
                float(str(aggregate_overlay.get("total_return"))),
                overlay_values[-1] - 1,
            )
            or not isclose(
                float(str(aggregate.get("excess_return"))),
                float(aggregate_overlay["total_return"])
                - float(aggregate_base["total_return"]),
            )
            or aggregate.get("positive_test_fold_count")
            != sum(float(row["test_overlay"]["total_return"]) > 0 for row in folds)
            or require_execution_diagnostics
            and (
                not _valid_minute_execution_diagnostics(
                    aggregate_execution,
                    expected_symbol_count=cast(int, dataset["symbol_count"]),
                    expected_observation_count=(
                        test_sessions_per_fold
                        * fold_count
                        * len(SINA_FIVE_MINUTE_SESSION_TIMES)
                        * cast(int, dataset["symbol_count"])
                    ),
                )
                or not _execution_diagnostics_match_folds(
                    cast(dict[str, object], aggregate_execution),
                    fold_execution_rows,
                )
            )
        ):
            return None
        return {
            **report,
            "minute_dataset": dataset,
            "candidates": candidates,
            "folds": folds,
            "cost_stress": cost_stress,
            "subgroups": subgroups,
            "leave_one_sector_out": sector_rows,
            "daily": daily,
            "curve": curve,
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


def _valid_minute_execution_diagnostics(
    value: object,
    *,
    expected_symbol_count: int,
    expected_observation_count: int,
) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "symbol_count",
        "observation_count",
        "reduced_exposure_observation_count",
        "exposure_sum",
        "mean_exposure",
        "reduced_exposure_ratio",
        "turnover",
        "mean_turnover_per_symbol",
        "transaction_cost",
        "mean_transaction_cost_per_symbol",
        "reason_counts",
    }:
        return False
    symbol_count = value.get("symbol_count")
    observation_count = value.get("observation_count")
    reduced_count = value.get("reduced_exposure_observation_count")
    reasons = value.get("reason_counts")
    if (
        symbol_count != expected_symbol_count
        or observation_count != expected_observation_count
        or not isinstance(reduced_count, int)
        or isinstance(reduced_count, bool)
        or not 0 <= reduced_count <= expected_observation_count
        or not isinstance(reasons, dict)
        or not all(
            isinstance(reason, str)
            and reason
            and isinstance(count, int)
            and not isinstance(count, bool)
            and 0 <= count <= expected_observation_count
            for reason, count in reasons.items()
        )
    ):
        return False
    exposure_sum = float(str(value.get("exposure_sum")))
    mean_exposure = float(str(value.get("mean_exposure")))
    reduced_ratio = float(str(value.get("reduced_exposure_ratio")))
    turnover = float(str(value.get("turnover")))
    mean_turnover = float(str(value.get("mean_turnover_per_symbol")))
    transaction_cost = float(str(value.get("transaction_cost")))
    mean_transaction_cost = float(
        str(value.get("mean_transaction_cost_per_symbol"))
    )
    return (
        all(
            isfinite(metric)
            for metric in (
                exposure_sum,
                mean_exposure,
                reduced_ratio,
                turnover,
                mean_turnover,
                transaction_cost,
                mean_transaction_cost,
            )
        )
        and 0 <= exposure_sum <= expected_observation_count
        and 0 <= mean_exposure <= 1
        and 0 <= reduced_ratio <= 1
        and turnover >= 0
        and transaction_cost >= 0
        and isclose(mean_exposure, exposure_sum / expected_observation_count)
        and isclose(reduced_ratio, reduced_count / expected_observation_count)
        and isclose(mean_turnover, turnover / expected_symbol_count)
        and isclose(
            mean_transaction_cost,
            transaction_cost / expected_symbol_count,
        )
    )


def _execution_diagnostics_match_folds(
    aggregate: dict[str, object], folds: list[dict[str, object]]
) -> bool:
    if not folds:
        return False
    summed_reasons: Counter[str] = Counter()
    for fold in folds:
        summed_reasons.update(cast(dict[str, int], fold["reason_counts"]))
    return (
        aggregate["observation_count"]
        == sum(cast(int, fold["observation_count"]) for fold in folds)
        and aggregate["reduced_exposure_observation_count"]
        == sum(
            cast(int, fold["reduced_exposure_observation_count"])
            for fold in folds
        )
        and isclose(
            float(str(aggregate["exposure_sum"])),
            sum(float(str(fold["exposure_sum"])) for fold in folds),
        )
        and isclose(
            float(str(aggregate["turnover"])),
            sum(float(str(fold["turnover"])) for fold in folds),
        )
        and isclose(
            float(str(aggregate["transaction_cost"])),
            sum(float(str(fold["transaction_cost"])) for fold in folds),
        )
        and aggregate["reason_counts"] == dict(sorted(summed_reasons.items()))
    )


def _load_verified_reserved_tail_diagnostic(
    path: Path | None,
    preregistration_path: Path | None,
    prior_report_path: Path | None,
    dataset: dict[str, object] | None,
) -> dict[str, object] | None:
    if (
        path is None
        or preregistration_path is None
        or dataset is None
        or not path.is_file()
        or not preregistration_path.is_file()
    ):
        return None
    try:
        preregistration = json.loads(preregistration_path.read_text(encoding="utf-8"))
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(preregistration, dict) or not isinstance(report, dict):
            return None
        preregistration_digest = preregistration.get("preregistration_sha256")
        preregistration_unsigned = {
            key: value
            for key, value in preregistration.items()
            if key != "preregistration_sha256"
        }
        report_digest = report.get("evidence_sha256")
        report_unsigned = {
            key: value for key, value in report.items() if key != "evidence_sha256"
        }
        preregistration_dataset = preregistration.get("dataset")
        evaluation = preregistration.get("evaluation_lock")
        interpretation = preregistration.get("interpretation_lock")
        candidate_lock = preregistration.get("candidate")
        comparator_lock = preregistration.get("comparator")
        prior_lock = preregistration.get("prior_evidence")
        prior_report = _load_verified_five_minute_report(
            prior_report_path,
            dataset,
            expected_report_id=(
                "baostock-five-minute-long-expanding-walk-forward-v1"
            ),
            initial_train_sessions=120,
            test_sessions_per_fold=40,
            fold_count=6,
            expected_promotion_reasons=(
                "FIVE_MINUTE_INTERVAL_DOES_NOT_PROVE_ONE_MINUTE_READINESS",
                "LONGER_PROVIDER_CROSSCHECK_REUSES_PREVIOUSLY_OBSERVED_CALENDAR",
                "CURRENT_NON_POINT_IN_TIME_UNIVERSE",
                "QFQ_MAY_USE_FUTURE_ADJUSTMENT_KNOWLEDGE",
                "FREE_HISTORICAL_SOURCE_NOT_AUTHORIZED_REALTIME_FEED",
                "NO_PAPER_OR_SHADOW_SESSION_EVIDENCE",
            ),
            require_execution_diagnostics=True,
        )
        expected_reasons = [
            "ONLY_EIGHT_RESERVED_SESSIONS",
            "TAIL_PRICES_PARTIALLY_OBSERVED_DURING_FRONTEND_QA",
            "CURRENT_NON_POINT_IN_TIME_UNIVERSE",
            "QFQ_MAY_USE_FUTURE_ADJUSTMENT_KNOWLEDGE",
            "NO_PAPER_OR_SHADOW_SESSION_EVIDENCE",
        ]
        if (
            not _is_sha256(preregistration_digest)
            or _canonical_dict_sha256(preregistration_unsigned)
            != preregistration_digest
            or preregistration.get("status")
            != "LOCKED_BEFORE_RESERVED_TAIL_ENGINE_READ"
            or not isinstance(preregistration_dataset, dict)
            or preregistration_dataset.get("data_version") != dataset["data_version"]
            or preregistration_dataset.get("checksum") != dataset["checksum"]
            or preregistration_dataset.get("evidence_sha256")
            != dataset["evidence_sha256"]
            or not isinstance(evaluation, dict)
            or evaluation.get("selection_after_tail_read") is not False
            or evaluation.get("parameter_changes_after_tail_read") is not False
            or not isinstance(interpretation, dict)
            or interpretation.get("promotion") != "NOT_ELIGIBLE"
            or interpretation.get("no_second_tail_tuning_iteration") is not True
            or not isinstance(candidate_lock, dict)
            or not isinstance(comparator_lock, dict)
            or not isinstance(prior_lock, dict)
            or prior_report is None
            or prior_lock.get("evidence_sha256")
            != prior_report.get("evidence_sha256")
            or not _is_sha256(report_digest)
            or _canonical_dict_sha256(report_unsigned) != report_digest
            or report.get("schema_version") != 1
            or report.get("report_id")
            != "baostock-reserved-tail-high-exposure-v1"
            or report.get("classification")
            != "PREREGISTERED_PARTIALLY_OBSERVED_RESERVED_TAIL_DIAGNOSTIC"
            or report.get("preregistration_sha256") != preregistration_digest
            or report.get("dataset") != preregistration_dataset
            or report.get("candidate")
            != {
                "policy_id": candidate_lock.get("policy_id"),
                "policy_hash": candidate_lock.get("policy_hash"),
            }
            or report.get("comparator") != comparator_lock
            or report.get("warmup_session_count") != 360
            or report.get("test_session_count") != 8
            or report.get("processed_bar_count") != dataset["bar_count"]
            or report.get("test_days") != evaluation.get("reserved_tail_days")
            or report.get("test_days") != cast(list[str], dataset["trading_days"])[-8:]
            or report.get("promotion") != "NOT_ELIGIBLE"
            or report.get("promotion_reasons") != expected_reasons
            or report.get("external_order_transmission") is not False
        ):
            return None
        artifacts = report.get("artifacts")
        expected_artifacts = {
            "cost_stress.json",
            "daily.json",
            "leave_one_sector_out.json",
            "curve.json",
        }
        if (
            not isinstance(artifacts, dict)
            or set(artifacts) != expected_artifacts
            or not all(_is_sha256(value) for value in artifacts.values())
        ):
            return None
        loaded: dict[str, object] = {}
        for name in expected_artifacts:
            content = _read_bounded_file(path.parent / name, _MAX_MINUTE_EVIDENCE_JSON_BYTES)
            if content is None or sha256(content).hexdigest() != artifacts[name]:
                return None
            loaded[name] = json.loads(content)
        costs = loaded["cost_stress.json"]
        daily = loaded["daily.json"]
        sectors = loaded["leave_one_sector_out.json"]
        curve = loaded["curve.json"]
        if (
            not isinstance(costs, list)
            or [float(row.get("cost_bps")) for row in costs]
            != [0.0, 5.0, 10.0, 20.0]
            or not isinstance(daily, list)
            or len(daily) != 8
            or [row.get("trading_day") for row in daily] != report["test_days"]
            or not isinstance(sectors, list)
            or len(sectors) != dataset["sector_count"]
            or not isinstance(curve, list)
            or len(curve) != 8 * len(SINA_FIVE_MINUTE_SESSION_TIMES)
        ):
            return None
        expected_observations = 8 * len(SINA_FIVE_MINUTE_SESSION_TIMES) * cast(
            int, dataset["symbol_count"]
        )
        for row in costs:
            if (
                not isinstance(row, dict)
                or not _valid_minute_execution_diagnostics(
                    row.get("candidate_execution"),
                    expected_symbol_count=cast(int, dataset["symbol_count"]),
                    expected_observation_count=expected_observations,
                )
                or not _valid_minute_execution_diagnostics(
                    row.get("comparator_execution"),
                    expected_symbol_count=cast(int, dataset["symbol_count"]),
                    expected_observation_count=expected_observations,
                )
            ):
                return None
        five = next(row for row in costs if row["cost_bps"] == 5)
        zero = next(row for row in costs if row["cost_bps"] == 0)
        sector_wins = sum(bool(row.get("candidate_beats_comparator")) for row in sectors)
        expected_gates = [
            {
                "gate_id": "CANDIDATE_BEATS_COMPARATOR_AT_5_BPS",
                "passed": float(five["candidate"]["total_return"])
                > float(five["comparator"]["total_return"]),
            },
            {
                "gate_id": "CANDIDATE_DRAWDOWN_NOT_WORSE_THAN_BASELINE_AT_5_BPS",
                "passed": float(five["candidate"]["maximum_drawdown"])
                >= float(five["base"]["maximum_drawdown"]),
            },
            {
                "gate_id": "MEAN_EXPOSURE_AT_LEAST_85_PERCENT",
                "passed": float(five["candidate_execution"]["mean_exposure"])
                >= 0.85,
            },
            {
                "gate_id": "TURNOVER_AT_MOST_HALF_COMPARATOR",
                "passed": float(
                    five["candidate_execution"]["mean_turnover_per_symbol"]
                )
                <= 0.5
                * float(five["comparator_execution"]["mean_turnover_per_symbol"]),
            },
            {
                "gate_id": "POSITIVE_AT_ZERO_AND_FIVE_BPS",
                "passed": float(zero["candidate"]["total_return"]) > 0
                and float(five["candidate"]["total_return"]) > 0,
            },
            {
                "gate_id": "LEAVE_ONE_SECTOR_OUT_MAJORITY",
                "passed": sector_wins >= 12 and len(sectors) == 15,
            },
        ]
        if (
            report.get("five_bps") != five
            or report.get("sector_win_count") != sector_wins
            or report.get("gates") != expected_gates
            or report.get("all_required_gates_passed")
            is not all(bool(row["passed"]) for row in expected_gates)
        ):
            return None
        previous: datetime | None = None
        base_values: list[float] = []
        candidate_values: list[float] = []
        comparator_values: list[float] = []
        allowed_days = set(cast(list[str], report["test_days"]))
        allowed_times = set(SINA_FIVE_MINUTE_SESSION_TIMES)
        for row in curve:
            instant = datetime.fromisoformat(str(row.get("timestamp")))
            local = instant.astimezone(ZoneInfo("Asia/Shanghai"))
            values = [
                float(row.get("base_equity")),
                float(row.get("candidate_equity")),
                float(row.get("comparator_equity")),
            ]
            if (
                instant.tzinfo is None
                or previous is not None
                and instant <= previous
                or local.date().isoformat() not in allowed_days
                or local.time() not in allowed_times
                or not all(isfinite(value) and value > 0 for value in values)
            ):
                return None
            previous = instant
            base_values.append(values[0])
            candidate_values.append(values[1])
            comparator_values.append(values[2])
        if not (
            isclose(base_values[-1] - 1, float(five["base"]["total_return"]))
            and isclose(
                candidate_values[-1] - 1,
                float(five["candidate"]["total_return"]),
            )
            and isclose(
                comparator_values[-1] - 1,
                float(five["comparator"]["total_return"]),
            )
        ):
            return None
        return {
            **report,
            "minute_dataset": dataset,
            "cost_stress": costs,
            "daily": daily,
            "leave_one_sector_out": sectors,
            "curve": curve,
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


def _canonical_dict_sha256(value: dict[str, object]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _load_verified_intraday_factor_robustness(
    path: Path | None,
    dataset: dict[str, object] | None,
) -> dict[str, object] | None:
    if (
        path is None
        or dataset is None
        or not path.is_file()
        or path.is_symlink()
    ):
        return None
    try:
        content = _read_bounded_file(path, _MAX_MINUTE_EVIDENCE_JSON_BYTES)
        if content is None:
            return None
        report = json.loads(content)
        if not isinstance(report, dict):
            return None
        digest = report.get("evidence_sha256")
        unsigned = {
            key: value for key, value in report.items() if key != "evidence_sha256"
        }
        result = report.get("result")
        summary = report.get("summary")
        implementation = report.get("implementation")
        dataset_claim = report.get("dataset")
        contract = report.get("evaluation_contract")
        parameters = report.get("parameters")
        generated_at = datetime.fromisoformat(str(report.get("generated_at")))
        if not all(
            isinstance(value, dict)
            for value in (
                result,
                summary,
                implementation,
                dataset_claim,
                contract,
                parameters,
            )
        ):
            return None
        result = cast(dict[str, object], result)
        summary = cast(dict[str, object], summary)
        implementation = cast(dict[str, object], implementation)
        dataset_claim = cast(dict[str, object], dataset_claim)
        contract = cast(dict[str, object], contract)
        parameters = cast(dict[str, object], parameters)
        result_digest = result.get("preview_hash")
        result_unsigned = {
            key: value for key, value in result.items() if key != "preview_hash"
        }
        dataset_days = cast(list[str], dataset["trading_days"])
        selected_days = dataset_days[-121:-1]
        expected_parameters = {
            "factor": "opening_three_ten_minute_bullish",
            "start": selected_days[0],
            "end": selected_days[-1],
            "cost_bps": "5",
            "minimum_body_return_bps": "0",
            "directions": ["BULLISH", "BULLISH", "BULLISH"],
            "combination": "ALL",
        }
        expected_dataset_claim = {
            "data_version": dataset["data_version"],
            "checksum": dataset["checksum"],
            "evidence_sha256": dataset["evidence_sha256"],
            "bar_interval": "5m",
            "session_count": 368,
            "symbol_count": 75,
            "sector_count": 15,
            "point_in_time": False,
            "future_adjustment_knowledge_possible": True,
        }
        expected_contract = {
            "selected_session_count": 120,
            "selected_start": selected_days[0],
            "selected_end": selected_days[-1],
            "exit_completion_day": dataset_days[-1],
            "candidate_thresholds_bps": ["0", "5", "10", "20"],
            "initial_training_session_count": 60,
            "fold_count": 6,
            "test_sessions_per_fold": 10,
            "test_returns_enter_same_fold_selection": False,
            "reused_history_not_independent_confirmation": True,
        }
        factor_source = (
            Path(__file__).resolve().parents[1]
            / "research"
            / "intraday_factor.py"
        )
        expected_implementation = {
            "factor_module_sha256": sha256(factor_source.read_bytes()).hexdigest(),
            "api_module_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        }
        backtest = result.get("backtest")
        robustness = result.get("robustness_validation")
        research_gate = result.get("research_gate")
        event = result.get("event_summary")
        factor_validation = result.get("factor_validation")
        if not all(
            isinstance(value, dict)
            for value in (
                backtest,
                robustness,
                research_gate,
                event,
                factor_validation,
            )
        ):
            return None
        backtest = cast(dict[str, object], backtest)
        robustness = cast(dict[str, object], robustness)
        research_gate = cast(dict[str, object], research_gate)
        event = cast(dict[str, object], event)
        factor_validation = cast(dict[str, object], factor_validation)
        folds = robustness.get("folds")
        sensitivity = robustness.get("threshold_sensitivity")
        stitched_curve = robustness.get("stitched_curve")
        bootstrap_claim = summary.get("stitched_excess_bootstrap")
        if (
            not isinstance(folds, list)
            or not isinstance(sensitivity, list)
            or not isinstance(stitched_curve, list)
            or not isinstance(bootstrap_claim, dict)
            or len(stitched_curve) != 60
            or not all(isinstance(row, dict) for row in stitched_curve)
        ):
            return None
        stitched_strategy_returns = tuple(
            Decimal(str(row.get("strategy_return"))) for row in stitched_curve
        )
        stitched_benchmark_returns = tuple(
            Decimal(str(row.get("benchmark_return"))) for row in stitched_curve
        )
        recomputed_bootstrap = paired_stationary_bootstrap_excess_returns(
            stitched_strategy_returns,
            stitched_benchmark_returns,
        )
        expected_bootstrap = {
            "method": recomputed_bootstrap.method,
            "observation_count": recomputed_bootstrap.observation_count,
            "replications": recomputed_bootstrap.replications,
            "expected_block_length": (
                recomputed_bootstrap.expected_block_length
            ),
            "seed": recomputed_bootstrap.seed,
            "observed_excess_total_return": str(
                recomputed_bootstrap.observed_excess_total_return
            ),
            "p05_excess_total_return": str(
                recomputed_bootstrap.p05_excess_total_return
            ),
            "median_excess_total_return": str(
                recomputed_bootstrap.median_excess_total_return
            ),
            "p95_excess_total_return": str(
                recomputed_bootstrap.p95_excess_total_return
            ),
            "probability_positive_excess": str(
                recomputed_bootstrap.probability_positive_excess
            ),
        }
        expected_training_counts = [60, 70, 80, 90, 100, 110]
        fold_identities_valid = len(folds) == 6
        selected_thresholds: list[str] = []
        positive_fold_count = 0
        for index, fold in enumerate(folds):
            if not isinstance(fold, dict):
                fold_identities_valid = False
                break
            known_at = datetime.fromisoformat(str(fold.get("selection_known_at")))
            test_metrics = fold.get("test_strategy_metrics")
            threshold = fold.get("selected_minimum_body_return_bps")
            fold_valid = (
                known_at.tzinfo is not None
                and known_at.date().isoformat() == selected_days[60 + index * 10]
                and known_at.astimezone(ZoneInfo("Asia/Shanghai")).time()
                == datetime_time(9, 35)
                and fold.get("fold_id") == f"WF-{index + 1:02d}"
                and fold.get("training_start") == selected_days[0]
                and fold.get("training_end")
                == selected_days[59 + index * 10]
                and fold.get("test_start") == selected_days[60 + index * 10]
                and fold.get("test_end") == selected_days[69 + index * 10]
                and fold.get("training_session_count")
                == expected_training_counts[index]
                and fold.get("test_session_count") == 10
                and threshold in {"0", "5", "10", "20"}
                and isinstance(fold.get("training_signal_count"), int)
                and isinstance(fold.get("test_signal_count"), int)
                and isinstance(test_metrics, dict)
            )
            if not fold_valid:
                fold_identities_valid = False
                break
            selected_thresholds.append(cast(str, threshold))
            positive_fold_count += Decimal(
                str(cast(dict[str, object], test_metrics).get("total_return"))
            ) > 0
        threshold_rows = [
            {
                "minimum_body_return_bps": row.get("minimum_body_return_bps"),
                "signal_count": row.get("signal_count"),
                "total_return": cast(dict[str, object], row.get("strategy_metrics"))[
                    "total_return"
                ],
                "excess_return": cast(dict[str, object], row.get("strategy_metrics"))[
                    "excess_return"
                ],
            }
            for row in sensitivity
            if isinstance(row, dict) and isinstance(row.get("strategy_metrics"), dict)
        ]
        expected_reasons = [
            "REUSED_HISTORICAL_WINDOW_NOT_INDEPENDENT_CONFIRMATION",
            "CURRENT_UNIVERSE_NON_PIT",
            "QFQ_MAY_CONTAIN_FUTURE_ADJUSTMENT_KNOWLEDGE",
            "NO_AUTHORIZED_FORWARD_PAPER_EVIDENCE",
            "NO_AUTHORIZED_REALTIME_OR_L2_PROVIDER",
        ]
        if (
            not _is_sha256(digest)
            or _canonical_dict_sha256(cast(dict[str, object], unsigned)) != digest
            or report.get("schema_version") != 1
            or report.get("report_id")
            != "opening-triple-bullish-long-window-robustness-v1"
            or report.get("classification")
            != "SIGNED_REUSED_LONG_WINDOW_INTRADAY_FACTOR_DIAGNOSTIC"
            or generated_at.tzinfo is None
            or implementation != expected_implementation
            or dataset_claim != expected_dataset_claim
            or contract != expected_contract
            or parameters != expected_parameters
            or result.get("parameters") != expected_parameters
            or not _is_sha256(result_digest)
            or _canonical_dict_sha256(result_unsigned) != result_digest
            or result_digest != _INTRADAY_FACTOR_LONG_WINDOW_PREVIEW_SHA256
            or report.get("result_preview_hash") != result_digest
            or result.get("classification")
            != "AD_HOC_INTRADAY_FACTOR_RESEARCH_CURRENT_NON_PIT"
            or result.get("data_version") != dataset["data_version"]
            or result.get("dataset_checksum") != dataset["checksum"]
            or result.get("selected_trading_days") != selected_days
            or result.get("exit_completion_day") != dataset_days[-1]
            or result.get("selected_session_count") != 120
            or result.get("source_bar_count") != 121 * 75 * 48
            or result.get("symbol_count") != 75
            or result.get("sector_count") != 15
            or robustness.get("classification")
            != "REUSED_CHRONOLOGICAL_WALK_FORWARD_DIAGNOSTIC_NOT_INDEPENDENT"
            or robustness.get("candidate_thresholds_bps") != ["0", "5", "10", "20"]
            or robustness.get("initial_training_session_count") != 60
            or robustness.get("stitched_test_session_count") != 60
            or [row.get("trading_day") for row in stitched_curve]
            != selected_days[60:]
            or robustness.get("passed") is not False
            or not fold_identities_valid
            or [row.get("minimum_body_return_bps") for row in sensitivity]
            != ["0", "5", "10", "20"]
            or summary.get("observation_count") != event.get("observation_count")
            or summary.get("signal_count") != event.get("signal_count")
            or summary.get("rank_ic") != factor_validation.get("rank_ic")
            or summary.get("full_window_strategy_metrics")
            != backtest.get("strategy_metrics")
            or summary.get("fold_count") != 6
            or summary.get("positive_fold_count") != positive_fold_count
            or summary.get("selected_thresholds_bps") != selected_thresholds
            or summary.get("stitched_strategy_metrics")
            != robustness.get("stitched_strategy_metrics")
            or bootstrap_claim != expected_bootstrap
            or expected_bootstrap["observed_excess_total_return"]
            != cast(dict[str, object], robustness.get("stitched_strategy_metrics"))[
                "excess_return"
            ]
            or summary.get("threshold_sensitivity") != threshold_rows
            or summary.get("robustness_passed") is not False
            or summary.get("research_gate_passed") is not False
            or research_gate.get("passed") is not False
            or report.get("promotion") != "NOT_ELIGIBLE"
            or report.get("promotion_reasons") != expected_reasons
            or report.get("paper_readiness") != "NOT_ELIGIBLE"
            or report.get("live_readiness")
            != "DISABLED_REQUIRES_SEPARATE_AUTHORIZATION"
            or report.get("state_mutation") is not False
            or report.get("external_order_transmission") is not False
            or result.get("promotion") != "NOT_ELIGIBLE"
            or result.get("state_mutation") is not False
            or result.get("external_order_transmission") is not False
        ):
            return None
        return cast(dict[str, object], report)
    except (
        InvalidOperation,
        IndexError,
        KeyError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


def _load_verified_public_minute_audit(
    path: Path | None,
    dataset: dict[str, object] | None,
) -> dict[str, object] | None:
    if path is None or dataset is None or not path.is_file():
        return None
    try:
        content = _read_bounded_file(path, _MAX_MINUTE_EVIDENCE_JSON_BYTES)
        if content is None:
            return None
        report = json.loads(content)
        if not isinstance(report, dict):
            return None
        digest = report.get("evidence_sha256")
        unsigned = {
            key: value for key, value in report.items() if key != "evidence_sha256"
        }
        instruments = report.get("instruments")
        sampled_days = report.get("sampled_trading_days")
        dataset_days = cast(list[str], dataset["trading_days"])
        expected_days = [
            dataset_days[0],
            dataset_days[len(dataset_days) // 2],
            dataset_days[-1],
        ]
        eligible_symbols = set(cast(list[str], dataset["eligible_symbols"]))
        generated_at = datetime.fromisoformat(str(report.get("generated_at")))
        if (
            not _is_sha256(digest)
            or _canonical_dict_sha256(cast(dict[str, object], unsigned)) != digest
            or report.get("schema_version") != 1
            or report.get("classification")
            != "PUBLIC_READ_ONLY_MINUTE_BAR_AVAILABILITY_AUDIT"
            or report.get("base_url") != "https://qtrade.erapi0neer.xyz"
            or report.get("data_version") != dataset["data_version"]
            or report.get("dataset_checksum") != dataset["checksum"]
            or report.get("sector_count") != dataset["sector_count"]
            or not isinstance(instruments, list)
            or len(instruments) != dataset["sector_count"]
            or not all(isinstance(row, dict) for row in instruments)
            or len({str(row.get("symbol")) for row in instruments})
            != len(instruments)
            or len({str(row.get("sector_code")) for row in instruments})
            != len(instruments)
            or not all(
                str(row.get("symbol")) in eligible_symbols for row in instruments
            )
            or sampled_days != expected_days
            or generated_at.tzinfo is None
            or report.get("all_valid") is not True
            or report.get("promotion") != "NOT_ELIGIBLE"
            or report.get("external_order_transmission") is not False
        ):
            return None
        for key, expected_cache in (
            ("first_round", {"MISS": 45}),
            ("repeat_round", {"HIT": 45}),
        ):
            round_result = report.get(key)
            if (
                not isinstance(round_result, dict)
                or round_result.get("request_count") != 45
                or round_result.get("all_valid") is not True
                or round_result.get("failures") != []
                or round_result.get("vercel_cache_counts") != expected_cache
            ):
                return None
            latency = round_result.get("latency_seconds")
            if not isinstance(latency, dict) or set(latency) != {
                "minimum",
                "median",
                "p95",
                "maximum",
            }:
                return None
            values = [
                float(str(latency[name]))
                for name in ("minimum", "median", "p95", "maximum")
            ]
            if (
                not all(isfinite(value) and value >= 0 for value in values)
                or values != sorted(values)
            ):
                return None
        write_boundary = report.get("public_write_boundary")
        expected_paths = [
            "/system/start",
            "/system/stop",
            "/system/pause",
            "/system/resume",
            "/system/kill",
            "/system/recover",
            "/orders/cancel",
        ]
        if (
            not isinstance(write_boundary, dict)
            or write_boundary.get("request_count") != len(expected_paths)
            or write_boundary.get("all_blocked") is not True
            or not isinstance(write_boundary.get("checks"), list)
            or [row.get("path") for row in write_boundary["checks"]]
            != expected_paths
            or not all(
                row.get("status") == 503 and row.get("blocked") is True
                for row in write_boundary["checks"]
            )
        ):
            return None
        return {**report, "minute_dataset": dataset}
    except (
        KeyError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


def _load_verified_development_policy_search(
    path: Path | None,
    dataset: dict[str, object] | None,
) -> dict[str, object] | None:
    if path is None or dataset is None or not path.is_file():
        return None
    try:
        content = _read_bounded_file(path, _MAX_MINUTE_EVIDENCE_JSON_BYTES)
        if content is None:
            return None
        report = json.loads(content)
        if not isinstance(report, dict):
            return None
        digest = report.get("evidence_sha256")
        unsigned = {
            key: value for key, value in report.items() if key != "evidence_sha256"
        }
        generated_at = datetime.fromisoformat(str(report.get("generated_at")))
        trading_days = dataset.get("trading_days")
        candidates = report.get("candidates")
        expected_policies = {
            "dev-floor-095-slow6x24-v1": {
                "short_window": "6",
                "long_window": "24",
                "volatility_window": "12",
                "maximum_minute_volatility": "0.012",
                "maximum_intraday_drawdown": "0.035",
                "reduction_multiplier": "0.95",
                "cooldown_bars": "6",
            },
            "dev-floor-098-slow6x24-v1": {
                "short_window": "6",
                "long_window": "24",
                "volatility_window": "12",
                "maximum_minute_volatility": "0.012",
                "maximum_intraday_drawdown": "0.035",
                "reduction_multiplier": "0.98",
                "cooldown_bars": "6",
            },
            "dev-floor-095-slow12x48-v1": {
                "short_window": "12",
                "long_window": "48",
                "volatility_window": "24",
                "maximum_minute_volatility": "0.02",
                "maximum_intraday_drawdown": "0.04",
                "reduction_multiplier": "0.95",
                "cooldown_bars": "6",
            },
            "dev-floor-098-slow12x48-v1": {
                "short_window": "12",
                "long_window": "48",
                "volatility_window": "24",
                "maximum_minute_volatility": "0.02",
                "maximum_intraday_drawdown": "0.04",
                "reduction_multiplier": "0.98",
                "cooldown_bars": "6",
            },
        }
        dataset_claim = report.get("dataset")
        development = report.get("development_contract")
        reserved_tail = report.get("reserved_tail")
        contamination = report.get("contamination_disclosure")
        leader = report.get("observed_leader")
        future_lock = report.get("future_observation_lock")
        causal = report.get("causal_contract")
        if (
            not _is_sha256(digest)
            or _canonical_dict_sha256(cast(dict[str, object], unsigned)) != digest
            or report.get("schema_version") != 1
            or report.get("report_id")
            != "baostock-retrospective-development-policy-search-v1"
            or report.get("classification")
            != "RETROSPECTIVE_DEVELOPMENT_ONLY_POLICY_SEARCH_NOT_INDEPENDENT"
            or generated_at.tzinfo is None
            or generated_at.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
            != "2026-08-26"
            or generated_at.astimezone(ZoneInfo("Asia/Shanghai")).time()
            >= datetime_time(9, 30)
            or not isinstance(trading_days, list)
            or len(trading_days) != 368
            or not isinstance(dataset_claim, dict)
            or dataset_claim.get("data_version") != dataset.get("data_version")
            or dataset_claim.get("checksum") != dataset.get("checksum")
            or dataset_claim.get("evidence_sha256")
            != dataset.get("evidence_sha256")
            or dataset_claim.get("bar_interval") != "5m"
            or dataset_claim.get("symbol_count") != 75
            or dataset_claim.get("sector_count") != 15
            or dataset_claim.get("point_in_time") is not False
            or dataset_claim.get("future_adjustment_knowledge_possible") is not True
            or not isinstance(development, dict)
            or development.get("session_count") != 360
            or development.get("observed_start") != trading_days[0]
            or development.get("observed_end") != trading_days[359]
            or development.get("processed_bar_count") != 75 * 360 * 48
            or development.get("cost_bps") != 5
            or not isinstance(reserved_tail, dict)
            or reserved_tail.get("session_count") != 8
            or reserved_tail.get("trading_days") != trading_days[360:]
            or reserved_tail.get("entered_strategy_engine") is not False
            or reserved_tail.get("entered_candidate_metrics") is not False
            or contamination
            != {
                "prior_reserved_tail_evidence_available_before_candidate_design": True,
                "candidate_search_is_not_blind": True,
                "observed_leader_selection_is_post_hoc": True,
            }
            or report.get("candidate_count") != 4
            or not isinstance(candidates, list)
            or len(candidates) != 4
            or not isinstance(leader, dict)
            or not isinstance(future_lock, dict)
            or not isinstance(causal, dict)
            or report.get("realtime_readiness") is not False
            or report.get("real_data_paper_readiness") is not False
            or report.get("promotion") != "NOT_ELIGIBLE"
            or report.get("external_order_transmission") is not False
        ):
            return None
        eligible: list[tuple[float, float, float, str]] = []
        base_identity: tuple[float, float, float, int] | None = None
        for row, (policy_id, policy) in zip(
            candidates, expected_policies.items(), strict=True
        ):
            if not isinstance(row, dict):
                return None
            base = row.get("base")
            overlay = row.get("overlay")
            execution = row.get("execution")
            policy_hash = sha256(
                json.dumps(
                    {"policy_id": policy_id, "policy": policy},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            if (
                row.get("policy_id") != policy_id
                or row.get("policy") != policy
                or row.get("policy_hash") != policy_hash
                or not isinstance(base, dict)
                or not isinstance(overlay, dict)
                or not isinstance(execution, dict)
            ):
                return None
            base_values = (
                float(str(base.get("total_return"))),
                float(str(base.get("maximum_drawdown"))),
                float(str(base.get("realized_minute_volatility"))),
                int(base.get("point_count", 0)),
            )
            overlay_values = (
                float(str(overlay.get("total_return"))),
                float(str(overlay.get("maximum_drawdown"))),
                float(str(overlay.get("realized_minute_volatility"))),
                int(overlay.get("point_count", 0)),
            )
            return_delta = float(str(row.get("return_delta_vs_base")))
            drawdown_improvement = float(str(row.get("drawdown_improvement")))
            exposure = float(str(execution.get("mean_exposure")))
            turnover = float(str(execution.get("turnover")))
            turnover_per_symbol = float(
                str(execution.get("mean_turnover_per_symbol"))
            )
            observation_count = int(execution.get("observation_count", 0))
            if (
                not all(
                    isfinite(value)
                    for value in (*base_values[:3], *overlay_values[:3])
                )
                or base_values[3] != 360 * 48
                or overlay_values[3] != 360 * 48
                or base_identity is not None
                and base_values != base_identity
                or not isclose(return_delta, overlay_values[0] - base_values[0])
                or not isclose(
                    drawdown_improvement,
                    abs(base_values[1]) - abs(overlay_values[1]),
                )
                or execution.get("symbol_count") != 75
                or observation_count != 75 * 360 * 48
                or not isfinite(exposure)
                or not 0 <= exposure <= 1
                or not isfinite(turnover)
                or turnover < 0
                or not isclose(turnover_per_symbol, turnover / 75)
            ):
                return None
            base_identity = base_values
            if (
                drawdown_improvement >= 0.02
                and exposure >= 0.90
                and turnover_per_symbol <= 100
            ):
                eligible.append(
                    (-return_delta, -drawdown_improvement, turnover_per_symbol, policy_id)
                )
        if not eligible:
            return None
        observed_leader = min(eligible)[3]
        if (
            leader.get("policy_id") != observed_leader
            or leader.get("selection_timing") != "POST_HOC_DEVELOPMENT_ONLY"
            or leader.get("objective")
            != "maximum return delta versus base after constraints"
            or leader.get("constraints")
            != {
                "minimum_drawdown_improvement": 0.02,
                "minimum_mean_exposure": 0.9,
                "maximum_mean_turnover_per_symbol": 100,
            }
            or future_lock.get("locked_at") != report.get("generated_at")
            or future_lock.get("policy_id") != observed_leader
            or future_lock.get("first_eligible_trading_day") != "2026-08-26"
            or future_lock.get("minimum_complete_future_sessions") != 20
            or future_lock.get("partial_same_day_observations_enter_metrics")
            is not False
            or future_lock.get("parameter_changes_before_completion") is not False
            or causal
            != {
                "signal_observed_at": "FIVE_MINUTE_BAR_CLOSE_T",
                "exposure_change_at": "NEXT_FIVE_MINUTE_BAR_OPEN",
                "same_bar_signal_execution": False,
                "reduction_only": True,
                "maximum_exposure": 1,
            }
        ):
            return None
        return {**report, "minute_dataset": dataset}
    except (
        KeyError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


def _load_verified_forward_preregistration(
    path: Path | None,
    dataset: dict[str, object] | None,
    development: dict[str, object] | None,
) -> dict[str, object] | None:
    if (
        path is None
        or dataset is None
        or development is None
        or not path.is_file()
    ):
        return None
    try:
        content = _read_bounded_file(path, _MAX_MINUTE_EVIDENCE_JSON_BYTES)
        if content is None:
            return None
        report = json.loads(content)
        if not isinstance(report, dict):
            return None
        digest = report.get("preregistration_sha256")
        unsigned = {
            key: value
            for key, value in report.items()
            if key != "preregistration_sha256"
        }
        created_at = datetime.fromisoformat(str(report.get("created_at")))
        development_evidence = report.get("development_evidence")
        warmup = report.get("warmup_dataset")
        collection = report.get("collection_lock")
        evaluation = report.get("evaluation_lock")
        timing = report.get("timing_disclosure")
        causal = report.get("causal_contract")
        implementation = report.get("implementation_sha256")
        interpretation = report.get("interpretation_lock")
        future_lock = development.get("future_observation_lock")
        candidates = development.get("candidates")
        if (
            not isinstance(future_lock, dict)
            or not isinstance(candidates, list)
            or not isinstance(development_evidence, dict)
            or not isinstance(warmup, dict)
            or not isinstance(collection, dict)
            or not isinstance(evaluation, dict)
            or not isinstance(timing, dict)
            or not isinstance(causal, dict)
            or not isinstance(implementation, dict)
            or not isinstance(interpretation, dict)
        ):
            return None
        policy_id = "dev-floor-098-slow12x48-v1"
        candidate = next(
            (
                row
                for row in candidates
                if isinstance(row, dict) and row.get("policy_id") == policy_id
            ),
            None,
        )
        required_gate_ids = [
            "OVERLAY_DRAWDOWN_NOT_WORSE_AT_5_BPS",
            "RETURN_SHORTFALL_WITHIN_50_BPS_AT_5_BPS",
            "MEAN_EXPOSURE_AT_LEAST_90_PERCENT",
            "MEAN_TURNOVER_PER_SYMBOL_AT_MOST_FIVE",
            "LEAVE_ONE_SECTOR_OUT_STABILITY",
        ]
        required_gates = evaluation.get("required_gates_after_twenty_sessions")
        if (
            not _is_sha256(digest)
            or _canonical_dict_sha256(cast(dict[str, object], unsigned)) != digest
            or report.get("schema_version") != 1
            or report.get("preregistration_id")
            != "baostock-locked-forward-observation-v1"
            or report.get("status")
            != "LOCKED_AFTER_FIRST_SESSION_OPEN_BEFORE_TARGET_SESSION_FETCH"
            or created_at.tzinfo is None
            or created_at
            <= datetime.fromisoformat(str(development.get("generated_at")))
            or timing
            != {
                "development_policy_locked_before_first_session_open": True,
                "evaluation_contract_fixed_after_first_session_open": True,
                "target_session_data_fetched_before_this_lock": False,
                "claim_as_blind_preregistration": False,
            }
            or not isinstance(candidate, dict)
            or development_evidence
            != {
                "report_id": development.get("report_id"),
                "evidence_sha256": development.get("evidence_sha256"),
                "locked_at": future_lock.get("locked_at"),
                "policy_id": candidate.get("policy_id"),
                "policy_hash": candidate.get("policy_hash"),
                "parameters": candidate.get("policy"),
            }
            or warmup.get("data_version") != dataset.get("data_version")
            or warmup.get("checksum") != dataset.get("checksum")
            or warmup.get("evidence_sha256") != dataset.get("evidence_sha256")
            or warmup.get("session_count") != 368
            or warmup.get("symbol_count") != 75
            or warmup.get("sector_count") != 15
            or warmup.get("bar_interval") != "5m"
            or warmup.get("last_trading_day") != "2026-08-25"
            or warmup.get("point_in_time") is not False
            or warmup.get("future_adjustment_knowledge_possible") is not True
            or collection
            != {
                "first_eligible_trading_day": "2026-08-26",
                "minimum_complete_future_sessions": 20,
                "provider": "BaoStock",
                "provider_frequency": "5",
                "adjustflag": "2",
                "required_symbol_count": 75,
                "required_sector_count": 15,
                "required_bars_per_symbol_session": 48,
                "required_total_bars_per_session": 3600,
                "earliest_collection_time_china": "15:10:00",
                "partial_sessions_enter_dataset": False,
                "suspended_or_zero_placeholder_sessions_enter_dataset": False,
                "first_successful_complete_snapshot_is_immutable": True,
                "anchor_day_must_exactly_match_frozen_warmup_dataset": True,
                "anchor_revision_action": "REJECT_TARGET_SESSION",
            }
            or evaluation.get("metric_sessions") != "future complete sessions only"
            or evaluation.get("warmup_sessions_enter_metrics") is not False
            or evaluation.get("cost_bps") != [0, 5]
            or evaluation.get("parameter_selection_after_future_read") is not False
            or evaluation.get("parameter_changes_before_twenty_sessions") is not False
            or evaluation.get("conclusion_before_minimum_sessions") != "INCOMPLETE"
            or evaluation.get("pass_does_not_authorize_paper_or_live") is not True
            or evaluation.get("failure_must_be_retained") is not True
            or not isinstance(required_gates, list)
            or [
                row.get("gate_id") if isinstance(row, dict) else None
                for row in required_gates
            ]
            != required_gate_ids
            or causal
            != {
                "signal_observed_at": "FIVE_MINUTE_BAR_CLOSE_T",
                "exposure_change_at": "NEXT_FIVE_MINUTE_BAR_OPEN",
                "same_bar_signal_execution": False,
                "reduction_only": True,
                "maximum_exposure": 1,
                "external_order_transmission": False,
            }
            or implementation
            != {
                "minute_backtest.py": (
                    "792d300d2df913f9f42576d0d96ebc66"
                    "c70e042969ea26a4357895c0ec52fa2c"
                ),
                "probe_baostock_forward_session.py": (
                    "d1934024fc23b334dd8c4cda40ca3e55"
                    "a0e200719d80d60201e3aed42bb357d1"
                ),
                "run_baostock_locked_forward_observation.py": (
                    "0294ac923744c3101b9810142ddfa9efb"
                    "2e5e3069aa348ae3251b292c49535fa"
                ),
            }
            or interpretation
            != {
                "promotion": "NOT_ELIGIBLE",
                "realtime_readiness": False,
                "real_data_paper_readiness": False,
                "current_non_point_in_time_universe": True,
                "qfq_may_use_future_adjustment_knowledge": True,
                "no_paper_or_shadow_session_evidence": True,
            }
        ):
            return None
        sector_identities = sorted(
            {
                (str(row["sector_code"]), str(row["sector_name"]))
                for row in cast(list[dict[str, object]], dataset["instruments"])
                if row.get("instrument_type") == "STOCK"
            }
        )
        if len(sector_identities) != 15:
            return None
        return {
            "classification": "LOCKED_FORWARD_OBSERVATION_PREREGISTRATION",
            "preregistration_id": report["preregistration_id"],
            "preregistration_sha256": digest,
            "created_at": report["created_at"],
            "status": report["status"],
            "timing_disclosure": timing,
            "policy_id": policy_id,
            "policy_hash": candidate["policy_hash"],
            "parameters": candidate["policy"],
            "sectors": [
                {"sector_code": code, "sector_name": name}
                for code, name in sector_identities
            ],
            "warmup_dataset": {
                "data_version": warmup["data_version"],
                "checksum": warmup["checksum"],
                "session_count": warmup["session_count"],
                "warmup_returns_enter_metrics": False,
            },
            "first_eligible_trading_day": collection["first_eligible_trading_day"],
            "future_session_count": 0,
            "minimum_complete_future_sessions": collection[
                "minimum_complete_future_sessions"
            ],
            "required_symbol_count": collection["required_symbol_count"],
            "required_sector_count": collection["required_sector_count"],
            "required_bars_per_symbol_session": collection[
                "required_bars_per_symbol_session"
            ],
            "observation_status": "INCOMPLETE",
            "all_required_gates_passed": False,
            "gates": [
                {"gate_id": gate_id, "passed": None} for gate_id in required_gate_ids
            ],
            "cost_stress": [],
            "leave_one_sector_out": [],
            "promotion": "NOT_ELIGIBLE",
            "promotion_reasons": [
                "FUTURE_TWENTY_SESSION_OBSERVATION_INCOMPLETE",
                "CURRENT_NON_POINT_IN_TIME_UNIVERSE",
                "QFQ_MAY_USE_FUTURE_ADJUSTMENT_KNOWLEDGE",
                "NO_PAPER_OR_SHADOW_SESSION_EVIDENCE",
            ],
            "realtime_readiness": False,
            "real_data_paper_readiness": False,
            "external_order_transmission": False,
        }
    except (
        KeyError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


def _load_verified_forward_observation_report(
    path: Path | None,
    preregistration: dict[str, object] | None,
) -> dict[str, object] | None:
    if path is None or preregistration is None or not path.is_file():
        return None
    try:
        content = _read_bounded_file(path, _MAX_MINUTE_EVIDENCE_JSON_BYTES)
        if content is None:
            return None
        report = json.loads(content)
        if not isinstance(report, dict):
            return None
        expected_report_keys = {
            "schema_version",
            "report_id",
            "classification",
            "generated_at",
            "preregistration_sha256",
            "policy_id",
            "policy_hash",
            "policy",
            "warmup_dataset",
            "future_session_count",
            "accepted_future_session_count",
            "evaluated_future_session_count",
            "minimum_complete_future_sessions",
            "future_sessions",
            "observation_status",
            "all_required_gates_passed",
            "gates",
            "cost_stress",
            "leave_one_sector_out",
            "causal_contract",
            "promotion",
            "promotion_reasons",
            "realtime_readiness",
            "real_data_paper_readiness",
            "external_order_transmission",
            "evidence_sha256",
        }
        if set(report) != expected_report_keys:
            return None
        digest = report.get("evidence_sha256")
        unsigned = {
            key: value for key, value in report.items() if key != "evidence_sha256"
        }
        generated_at = datetime.fromisoformat(str(report.get("generated_at")))
        lock_created_at = datetime.fromisoformat(
            str(preregistration.get("created_at"))
        )
        warmup = report.get("warmup_dataset")
        sessions = report.get("future_sessions")
        cost_stress = report.get("cost_stress")
        sectors = report.get("leave_one_sector_out")
        gates = report.get("gates")
        causal = report.get("causal_contract")
        accepted = report.get("accepted_future_session_count")
        evaluated = report.get("evaluated_future_session_count")
        future_count = report.get("future_session_count")
        minimum = preregistration.get("minimum_complete_future_sessions")
        if (
            not _is_sha256(digest)
            or _canonical_dict_sha256(cast(dict[str, object], unsigned)) != digest
            or report.get("schema_version") != 1
            or report.get("report_id")
            != "baostock-locked-forward-observation-v1"
            or report.get("classification")
            != "LOCKED_FUTURE_COMPLETE_SESSION_OBSERVATION"
            or generated_at.tzinfo is None
            or lock_created_at.tzinfo is None
            or generated_at <= lock_created_at
            or generated_at
            > datetime.now(ZoneInfo("UTC")) + timedelta(minutes=5)
            or report.get("preregistration_sha256")
            != preregistration.get("preregistration_sha256")
            or report.get("policy_id") != preregistration.get("policy_id")
            or report.get("policy_hash") != preregistration.get("policy_hash")
            or report.get("policy") != preregistration.get("parameters")
            or not isinstance(warmup, dict)
            or warmup != preregistration.get("warmup_dataset")
            or not isinstance(accepted, int)
            or isinstance(accepted, bool)
            or accepted < 0
            or not isinstance(evaluated, int)
            or isinstance(evaluated, bool)
            or evaluated != min(accepted, 20)
            or not isinstance(future_count, int)
            or isinstance(future_count, bool)
            or future_count != accepted
            or minimum != 20
            or report.get("minimum_complete_future_sessions") != minimum
            or not isinstance(sessions, list)
            or len(sessions) != accepted
            or not isinstance(cost_stress, list)
            or not isinstance(sectors, list)
            or not isinstance(gates, list)
            or report.get("observation_status")
            != ("COMPLETE" if accepted >= 20 else "INCOMPLETE")
            or report.get("realtime_readiness") is not False
            or report.get("real_data_paper_readiness") is not False
            or report.get("promotion") != "NOT_ELIGIBLE"
            or report.get("external_order_transmission") is not False
            or causal
            != {
                "signal_observed_at": "FIVE_MINUTE_BAR_CLOSE_T",
                "exposure_change_at": "NEXT_FIVE_MINUTE_BAR_OPEN",
                "same_bar_signal_execution": False,
                "reduction_only": True,
                "maximum_exposure": 1,
                "external_order_transmission": False,
            }
        ):
            return None
        days: list[str] = []
        generated_china = generated_at.astimezone(ZoneInfo("Asia/Shanghai"))
        for row in sessions:
            if (
                not isinstance(row, dict)
                or not _is_sha256(row.get("evidence_sha256"))
                or row.get("bar_count") != 3600
                or row.get("anchor_exact_match") is not True
            ):
                return None
            target_day = date.fromisoformat(str(row.get("target_day")))
            day = target_day.isoformat()
            if (
                day < str(preregistration.get("first_eligible_trading_day"))
                or target_day > generated_china.date()
                or target_day == generated_china.date()
                and generated_china.time() < datetime_time(15, 10)
            ):
                return None
            days.append(day)
        if days != sorted(set(days)):
            return None

        expected_points = evaluated * 48

        def metric_values(value: object) -> tuple[float, float, float, int] | None:
            if not isinstance(value, dict):
                return None
            point_count = value.get("point_count")
            if not isinstance(point_count, int) or isinstance(point_count, bool):
                return None
            values = (
                float(str(value.get("total_return"))),
                float(str(value.get("maximum_drawdown"))),
                float(str(value.get("realized_minute_volatility"))),
                point_count,
            )
            if (
                not all(isfinite(item) for item in values[:3])
                or values[1] > 0
                or values[2] < 0
                or values[3] != expected_points
            ):
                return None
            return values

        if evaluated == 0:
            if cost_stress or sectors or gates:
                return None
            gate_values: dict[str, bool] = {}
        else:
            if len(cost_stress) != 2 or len(sectors) != 15 or len(gates) != 5:
                return None
            seen_costs: list[int] = []
            five_row: dict[str, object] | None = None
            base_identity: tuple[float, float, float, int] | None = None
            for row in cost_stress:
                if not isinstance(row, dict):
                    return None
                cost = row.get("cost_bps")
                base = metric_values(row.get("base"))
                overlay = metric_values(row.get("overlay"))
                execution = row.get("execution")
                if not _valid_minute_execution_diagnostics(
                    execution,
                    expected_symbol_count=75,
                    expected_observation_count=75 * expected_points,
                ):
                    return None
                assert isinstance(execution, dict)
                mean_exposure = float(str(execution.get("mean_exposure")))
                turnover = float(str(execution.get("turnover")))
                mean_turnover = float(
                    str(execution.get("mean_turnover_per_symbol"))
                )
                if (
                    not isinstance(cost, int)
                    or isinstance(cost, bool)
                    or cost not in {0, 5}
                    or base is None
                    or overlay is None
                    or base_identity is not None
                    and (
                        base[3] != base_identity[3]
                        or any(
                            not isclose(
                                base[index],
                                base_identity[index],
                                rel_tol=1e-12,
                                abs_tol=1e-15,
                            )
                            for index in range(3)
                        )
                    )
                    or not isclose(
                        float(str(row.get("excess_return"))),
                        overlay[0] - base[0],
                    )
                    or execution.get("symbol_count") != 75
                    or execution.get("observation_count") != 75 * expected_points
                    or not all(
                        isfinite(value)
                        for value in (mean_exposure, turnover, mean_turnover)
                    )
                    or not 0 <= mean_exposure <= 1
                    or turnover < 0
                    or mean_turnover < 0
                    or not isclose(mean_turnover, turnover / 75)
                ):
                    return None
                base_identity = base
                seen_costs.append(cost)
                if cost == 5:
                    five_row = cast(dict[str, object], row)
            if seen_costs != [0, 5] or five_row is None:
                return None
            seen_sectors: dict[str, str] = {}
            stable_sector_count = 0
            for row in sectors:
                if not isinstance(row, dict):
                    return None
                code = str(row.get("excluded_sector_code"))
                base = metric_values(row.get("base"))
                overlay = metric_values(row.get("overlay"))
                excess = float(str(row.get("excess_return")))
                if (
                    not code.isdigit()
                    or code in seen_sectors
                    or not str(row.get("excluded_sector_name"))
                    or base is None
                    or overlay is None
                    or not isclose(excess, overlay[0] - base[0])
                ):
                    return None
                seen_sectors[code] = str(row["excluded_sector_name"])
                stable_sector_count += excess >= -0.01
            if [
                {"sector_code": code, "sector_name": name}
                for code, name in sorted(seen_sectors.items())
            ] != preregistration.get("sectors"):
                return None
            five_base = metric_values(five_row.get("base"))
            five_overlay = metric_values(five_row.get("overlay"))
            five_execution = cast(dict[str, object], five_row["execution"])
            assert five_base is not None and five_overlay is not None
            gate_values = {
                "OVERLAY_DRAWDOWN_NOT_WORSE_AT_5_BPS": (
                    five_overlay[1] >= five_base[1]
                ),
                "RETURN_SHORTFALL_WITHIN_50_BPS_AT_5_BPS": (
                    five_overlay[0] - five_base[0] >= -0.005
                ),
                "MEAN_EXPOSURE_AT_LEAST_90_PERCENT": (
                    float(str(five_execution.get("mean_exposure"))) >= 0.90
                ),
                "MEAN_TURNOVER_PER_SYMBOL_AT_MOST_FIVE": (
                    float(str(five_execution.get("mean_turnover_per_symbol"))) <= 5
                ),
                "LEAVE_ONE_SECTOR_OUT_STABILITY": stable_sector_count >= 12,
            }
            if gates != [
                {"gate_id": gate_id, "passed": passed}
                for gate_id, passed in gate_values.items()
            ]:
                return None
        complete = accepted >= 20
        all_passed = complete and bool(gate_values) and all(gate_values.values())
        expected_reasons = [
            *([] if complete else ["FUTURE_TWENTY_SESSION_OBSERVATION_INCOMPLETE"]),
            *(
                ["PREDECLARED_FORWARD_GATES_FAILED"]
                if complete and not all_passed
                else []
            ),
            "CURRENT_NON_POINT_IN_TIME_UNIVERSE",
            "QFQ_MAY_USE_FUTURE_ADJUSTMENT_KNOWLEDGE",
            "NO_PAPER_OR_SHADOW_SESSION_EVIDENCE",
        ]
        if (
            report.get("all_required_gates_passed") is not all_passed
            or report.get("promotion_reasons") != expected_reasons
        ):
            return None
        return cast(dict[str, object], report)
    except (
        KeyError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


def _load_verified_forward_source_coverage(
    path: Path | None,
    dataset: dict[str, object] | None,
    preregistration: dict[str, object] | None,
) -> dict[str, object] | None:
    if (
        path is None
        or dataset is None
        or preregistration is None
        or not path.is_file()
    ):
        return None
    try:
        content = _read_bounded_file(path, _MAX_MINUTE_EVIDENCE_JSON_BYTES)
        if content is None:
            return None
        report = json.loads(content)
        if not isinstance(report, dict):
            return None
        expected_report_keys = {
            "schema_version",
            "report_id",
            "generated_at",
            "classification",
            "provider",
            "provider_version",
            "frequency",
            "adjustflag",
            "anchor_day",
            "target_day",
            "preregistration_id",
            "preregistration_sha256",
            "warmup_data_version",
            "warmup_checksum",
            "warmup_evidence_sha256",
            "required_symbol_count",
            "required_bars_per_symbol_session",
            "target_complete_symbol_count",
            "ready_symbol_count",
            "incomplete_symbol_count",
            "incomplete_symbols",
            "target_incomplete_symbols",
            "fetch_error_symbols",
            "coverage",
            "status",
            "ready_for_formal_collection",
            "enters_forward_metrics",
            "promotion",
            "realtime_readiness",
            "real_data_paper_readiness",
            "external_order_transmission",
            "evidence_sha256",
        }
        if set(report) != expected_report_keys:
            return None
        digest = report.get("evidence_sha256")
        unsigned = {
            key: value for key, value in report.items() if key != "evidence_sha256"
        }
        generated_at = datetime.fromisoformat(str(report.get("generated_at")))
        anchor_day = date.fromisoformat(str(report.get("anchor_day")))
        target_day = date.fromisoformat(str(report.get("target_day")))
        first_eligible_day = date.fromisoformat(
            str(preregistration.get("first_eligible_trading_day"))
        )
        warmup = preregistration.get("warmup_dataset")
        symbols = dataset.get("eligible_symbols")
        trading_days = dataset.get("trading_days")
        warmup_last_day = (
            trading_days[-1]
            if isinstance(trading_days, list) and trading_days
            else warmup.get("last_trading_day")
            if isinstance(warmup, dict)
            else None
        )
        coverage = report.get("coverage")
        if (
            not _is_sha256(digest)
            or _canonical_dict_sha256(cast(dict[str, object], unsigned)) != digest
            or report.get("schema_version") != 1
            or report.get("report_id")
            != "baostock-forward-source-coverage-diagnostic-v1"
            or report.get("classification")
            != "SOURCE_COVERAGE_DIAGNOSTIC_NOT_FORWARD_EVIDENCE"
            or report.get("provider") != "BaoStock"
            or report.get("provider_version") != "0.9.3"
            or report.get("frequency") != "5"
            or report.get("adjustflag") != "2"
            or generated_at.tzinfo is None
            or generated_at
            > datetime.now(ZoneInfo("UTC")) + timedelta(minutes=5)
            or target_day <= anchor_day
            or target_day < first_eligible_day
            or not isinstance(warmup, dict)
            or anchor_day.isoformat() != warmup_last_day
            or report.get("preregistration_id")
            != preregistration.get("preregistration_id")
            or report.get("preregistration_sha256")
            != preregistration.get("preregistration_sha256")
            or report.get("warmup_data_version") != dataset.get("data_version")
            or report.get("warmup_checksum") != dataset.get("checksum")
            or report.get("warmup_evidence_sha256")
            != dataset.get("evidence_sha256")
            or report.get("warmup_data_version") != warmup.get("data_version")
            or report.get("warmup_checksum") != warmup.get("checksum")
            or not isinstance(symbols, list)
            or len(symbols) != 75
            or len(set(symbols)) != 75
            or not isinstance(coverage, list)
            or len(coverage) != 75
            or report.get("required_symbol_count") != 75
            or report.get("required_bars_per_symbol_session") != 48
        ):
            return None
        generated_china = generated_at.astimezone(ZoneInfo("Asia/Shanghai"))
        if generated_china.date() < target_day or (
            generated_china.date() == target_day
            and generated_china.time() < datetime_time(15, 10)
        ):
            return None

        expected_row_keys = {
            "symbol",
            "fetch_status",
            "frozen_anchor_bar_count",
            "anchor_bar_count",
            "target_bar_count",
            "frozen_anchor_grid_complete",
            "anchor_grid_complete",
            "target_grid_complete",
            "anchor_exact_match",
            "ready",
            "reasons",
        }
        ready_symbols: list[str] = []
        incomplete_symbols: list[str] = []
        target_complete_symbols: list[str] = []
        target_incomplete_symbols: list[str] = []
        fetch_error_symbols: list[str] = []
        for expected_symbol, value in zip(symbols, coverage, strict=True):
            if not isinstance(expected_symbol, str) or not isinstance(value, dict):
                return None
            counts = (
                value.get("frozen_anchor_bar_count"),
                value.get("anchor_bar_count"),
                value.get("target_bar_count"),
            )
            flags = (
                value.get("frozen_anchor_grid_complete"),
                value.get("anchor_grid_complete"),
                value.get("target_grid_complete"),
                value.get("anchor_exact_match"),
                value.get("ready"),
            )
            reasons = value.get("reasons")
            fetch_status = value.get("fetch_status")
            if (
                set(value) != expected_row_keys
                or value.get("symbol") != expected_symbol
                or fetch_status not in {"OK", "ERROR"}
                or any(
                    not isinstance(count, int)
                    or isinstance(count, bool)
                    or count < 0
                    or count > 5000
                    for count in counts
                )
                or any(not isinstance(flag, bool) for flag in flags)
                or not isinstance(reasons, list)
                or any(not isinstance(reason, str) for reason in reasons)
                or (
                    value.get("frozen_anchor_grid_complete") is True
                    and value.get("frozen_anchor_bar_count") != 48
                )
                or (
                    value.get("anchor_grid_complete") is True
                    and value.get("anchor_bar_count") != 48
                )
                or (
                    value.get("target_grid_complete") is True
                    and value.get("target_bar_count") != 48
                )
                or (
                    value.get("anchor_exact_match") is True
                    and not (
                        value.get("frozen_anchor_grid_complete") is True
                        and value.get("anchor_grid_complete") is True
                    )
                )
            ):
                return None
            expected_reasons: list[str] = []
            if fetch_status == "ERROR":
                expected_reasons.append("FETCH_FAILED")
                fetch_error_symbols.append(expected_symbol)
            if value["frozen_anchor_grid_complete"] is False:
                expected_reasons.append("FROZEN_ANCHOR_INCOMPLETE")
            if value["anchor_grid_complete"] is False:
                expected_reasons.append("FETCHED_ANCHOR_INCOMPLETE")
            if value["target_grid_complete"] is False:
                expected_reasons.append("TARGET_SESSION_INCOMPLETE")
                target_incomplete_symbols.append(expected_symbol)
            else:
                target_complete_symbols.append(expected_symbol)
            if (
                value["frozen_anchor_grid_complete"] is True
                and value["anchor_grid_complete"] is True
                and value["anchor_exact_match"] is False
            ):
                expected_reasons.append("ANCHOR_REVISION")
            expected_ready = not expected_reasons
            if reasons != expected_reasons or value.get("ready") is not expected_ready:
                return None
            (ready_symbols if expected_ready else incomplete_symbols).append(
                expected_symbol
            )

        ready = len(ready_symbols) == 75
        if (
            report.get("target_complete_symbol_count")
            != len(target_complete_symbols)
            or report.get("ready_symbol_count") != len(ready_symbols)
            or report.get("incomplete_symbol_count") != len(incomplete_symbols)
            or report.get("incomplete_symbols") != incomplete_symbols
            or report.get("target_incomplete_symbols") != target_incomplete_symbols
            or report.get("fetch_error_symbols") != sorted(fetch_error_symbols)
            or report.get("status")
            != ("READY_FOR_FORMAL_COLLECTION" if ready else "SOURCE_INCOMPLETE")
            or report.get("ready_for_formal_collection") is not ready
            or report.get("enters_forward_metrics") is not False
            or report.get("promotion") != "NOT_ELIGIBLE"
            or report.get("realtime_readiness") is not False
            or report.get("real_data_paper_readiness") is not False
            or report.get("external_order_transmission") is not False
        ):
            return None
        return cast(dict[str, object], report)
    except (
        KeyError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


def _load_verified_forward_source_coverage_history(
    path: Path | None,
    coverage: dict[str, object] | None,
    release_root: Path | None,
    dataset: dict[str, object] | None,
    preregistration: dict[str, object] | None,
) -> dict[str, object] | None:
    if (
        path is None
        or coverage is None
        or release_root is None
        or dataset is None
        or preregistration is None
        or not path.is_file()
        or not release_root.is_dir()
        or release_root.is_symlink()
    ):
        return None
    try:
        verified_release_root = release_root.resolve()
        content = _read_bounded_file(path, _MAX_MINUTE_EVIDENCE_JSON_BYTES)
        if content is None:
            return None
        report = json.loads(content)
        expected_report_keys = {
            "schema_version",
            "report_id",
            "classification",
            "source_identity",
            "checkpoint_count",
            "all_checkpoints_monotonic",
            "checkpoints",
            "enters_forward_metrics",
            "promotion",
            "realtime_readiness",
            "real_data_paper_readiness",
            "external_order_transmission",
            "evidence_sha256",
        }
        if not isinstance(report, dict) or set(report) != expected_report_keys:
            return None
        digest = report.get("evidence_sha256")
        unsigned = {
            key: value for key, value in report.items() if key != "evidence_sha256"
        }
        identity = report.get("source_identity")
        checkpoints = report.get("checkpoints")
        checkpoint_count = report.get("checkpoint_count")
        expected_identity = {
            "provider": coverage.get("provider"),
            "provider_version": coverage.get("provider_version"),
            "frequency": coverage.get("frequency"),
            "adjustflag": coverage.get("adjustflag"),
            "anchor_day": coverage.get("anchor_day"),
            "target_day": coverage.get("target_day"),
            "preregistration_id": coverage.get("preregistration_id"),
            "preregistration_sha256": coverage.get("preregistration_sha256"),
            "warmup_data_version": coverage.get("warmup_data_version"),
            "warmup_checksum": coverage.get("warmup_checksum"),
            "warmup_evidence_sha256": coverage.get("warmup_evidence_sha256"),
            "required_symbol_count": coverage.get("required_symbol_count"),
            "required_bars_per_symbol_session": coverage.get(
                "required_bars_per_symbol_session"
            ),
        }
        if (
            not _is_sha256(digest)
            or _canonical_dict_sha256(cast(dict[str, object], unsigned)) != digest
            or report.get("schema_version") != 1
            or report.get("report_id")
            != "baostock-forward-source-coverage-history-v1"
            or report.get("classification")
            != "SIGNED_SOURCE_COVERAGE_HISTORY_NOT_FORWARD_EVIDENCE"
            or identity != expected_identity
            or not isinstance(checkpoint_count, int)
            or isinstance(checkpoint_count, bool)
            or not isinstance(checkpoints, list)
            or not 1 <= checkpoint_count == len(checkpoints) <= 64
            or report.get("enters_forward_metrics") is not False
            or report.get("promotion") != "NOT_ELIGIBLE"
            or report.get("realtime_readiness") is not False
            or report.get("real_data_paper_readiness") is not False
            or report.get("external_order_transmission") is not False
        ):
            return None
        expected_checkpoint_keys = {
            "sequence",
            "generated_at",
            "report_evidence_sha256",
            "ready_symbol_count",
            "target_complete_symbol_count",
            "incomplete_symbol_count",
            "fetch_error_symbol_count",
            "status",
            "ready_symbol_count_delta",
            "monotonic_vs_previous",
            "previous_checkpoint_sha256",
            "checkpoint_sha256",
        }
        previous_hash: str | None = None
        previous_time: datetime | None = None
        previous_ready: int | None = None
        previous_target: int | None = None
        previous_incomplete: int | None = None
        all_monotonic = True
        for sequence, value in enumerate(checkpoints, start=1):
            if not isinstance(value, dict) or set(value) != expected_checkpoint_keys:
                return None
            generated_at = datetime.fromisoformat(str(value.get("generated_at")))
            ready = value.get("ready_symbol_count")
            target_complete = value.get("target_complete_symbol_count")
            incomplete = value.get("incomplete_symbol_count")
            fetch_errors = value.get("fetch_error_symbol_count")
            delta = value.get("ready_symbol_count_delta")
            checkpoint_digest = value.get("checkpoint_sha256")
            checkpoint_unsigned = {
                key: item
                for key, item in value.items()
                if key != "checkpoint_sha256"
            }
            if (
                generated_at.tzinfo is None
                or generated_at
                > datetime.now(ZoneInfo("UTC")) + timedelta(minutes=5)
                or previous_time is not None
                and generated_at <= previous_time
                or value.get("sequence") != sequence
                or value.get("previous_checkpoint_sha256") != previous_hash
                or not _is_sha256(checkpoint_digest)
                or _canonical_dict_sha256(
                    cast(dict[str, object], checkpoint_unsigned)
                )
                != checkpoint_digest
                or not _is_sha256(value.get("report_evidence_sha256"))
                or any(
                    not isinstance(count, int)
                    or isinstance(count, bool)
                    or not 0 <= count <= 75
                    for count in (ready, target_complete, incomplete, fetch_errors)
                )
                or cast(int, ready) > cast(int, target_complete)
                or cast(int, ready) + cast(int, incomplete) != 75
                or value.get("status")
                != (
                    "READY_FOR_FORMAL_COLLECTION"
                    if ready == 75
                    else "SOURCE_INCOMPLETE"
                )
                or sequence == 1
                and delta is not None
                or sequence > 1
                and (
                    not isinstance(delta, int)
                    or isinstance(delta, bool)
                    or delta != cast(int, ready) - cast(int, previous_ready)
                )
            ):
                return None
            release_path = verified_release_root / (
                f"{value['report_evidence_sha256']}.json"
            )
            if release_path.is_symlink():
                return None
            release = _load_verified_forward_source_coverage(
                release_path,
                dataset,
                preregistration,
            )
            if (
                release is None
                or release.get("evidence_sha256")
                != value.get("report_evidence_sha256")
                or release.get("generated_at") != value.get("generated_at")
                or release.get("ready_symbol_count") != ready
                or release.get("target_complete_symbol_count") != target_complete
                or release.get("incomplete_symbol_count") != incomplete
                or len(cast(list[object], release.get("fetch_error_symbols")))
                != fetch_errors
                or release.get("status") != value.get("status")
            ):
                return None
            monotonic = previous_ready is None or (
                cast(int, ready) >= previous_ready
                and cast(int, target_complete) >= cast(int, previous_target)
                and cast(int, incomplete) <= cast(int, previous_incomplete)
            )
            if value.get("monotonic_vs_previous") is not monotonic:
                return None
            all_monotonic = all_monotonic and monotonic
            previous_hash = str(checkpoint_digest)
            previous_time = generated_at
            previous_ready = cast(int, ready)
            previous_target = cast(int, target_complete)
            previous_incomplete = cast(int, incomplete)
        latest = cast(dict[str, object], checkpoints[-1])
        if (
            report.get("all_checkpoints_monotonic") is not all_monotonic
            or latest.get("generated_at") != coverage.get("generated_at")
            or latest.get("report_evidence_sha256")
            != coverage.get("evidence_sha256")
            or latest.get("ready_symbol_count") != coverage.get("ready_symbol_count")
            or latest.get("target_complete_symbol_count")
            != coverage.get("target_complete_symbol_count")
            or latest.get("incomplete_symbol_count")
            != coverage.get("incomplete_symbol_count")
            or latest.get("status") != coverage.get("status")
        ):
            return None
        return cast(dict[str, object], report)
    except (
        KeyError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


def _load_verified_minute_research_snapshot(
    state: AppContext,
) -> dict[str, object]:
    dataset = _load_verified_minute_dataset(
        state.long_five_minute_manifest_path,
        expected_classification="free_historical_qfq_5m_current_non_pit_not_eligible",
        expected_interval="5m",
        expected_session_count=368,
        expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
    )
    long_report = _load_verified_five_minute_report(
        state.long_five_minute_research_report_path,
        dataset,
        expected_report_id="baostock-five-minute-long-expanding-walk-forward-v1",
        initial_train_sessions=120,
        test_sessions_per_fold=40,
        fold_count=6,
        expected_promotion_reasons=(
            "FIVE_MINUTE_INTERVAL_DOES_NOT_PROVE_ONE_MINUTE_READINESS",
            "LONGER_PROVIDER_CROSSCHECK_REUSES_PREVIOUSLY_OBSERVED_CALENDAR",
            "CURRENT_NON_POINT_IN_TIME_UNIVERSE",
            "QFQ_MAY_USE_FUTURE_ADJUSTMENT_KNOWLEDGE",
            "FREE_HISTORICAL_SOURCE_NOT_AUTHORIZED_REALTIME_FEED",
            "NO_PAPER_OR_SHADOW_SESSION_EVIDENCE",
        ),
        require_execution_diagnostics=True,
    )
    intraday_factor_robustness = _load_verified_intraday_factor_robustness(
        state.intraday_factor_robustness_path,
        dataset,
    )
    reserved_tail = _load_verified_reserved_tail_diagnostic(
        state.reserved_tail_diagnostic_report_path,
        state.reserved_tail_preregistration_path,
        state.long_five_minute_research_report_path,
        dataset,
    )
    development = _load_verified_development_policy_search(
        state.development_policy_search_path,
        dataset,
    )
    preregistration = _load_verified_forward_preregistration(
        state.forward_observation_preregistration_path,
        dataset,
        development,
    )
    forward_report = _load_verified_forward_observation_report(
        state.forward_observation_report_path,
        preregistration,
    )
    forward = (
        {**preregistration, **forward_report}
        if preregistration is not None and forward_report is not None
        else forward_report or preregistration
    )
    forward_source_coverage = _load_verified_forward_source_coverage(
        state.forward_source_coverage_path,
        dataset,
        preregistration,
    )
    forward_source_coverage_history = _load_verified_forward_source_coverage_history(
        state.forward_source_coverage_history_path,
        forward_source_coverage,
        state.forward_source_coverage_release_root,
        dataset,
        preregistration,
    )
    public_audit = _load_verified_public_minute_audit(
        state.public_minute_audit_path,
        dataset,
    )
    detail = _load_verified_intraday_observation(
        state.intraday_observation_path,
        dataset,
    )
    breadth = _load_verified_intraday_breadth(
        state.intraday_breadth_path,
        dataset,
    )
    continuity = _load_verified_intraday_continuity(
        state.intraday_continuity_path,
        detail,
        breadth,
    )
    sections: dict[str, dict[str, object] | None] = {
        "longFiveMinuteResearch": long_report,
        "intradayFactorRobustness": intraday_factor_robustness,
        "reservedTailDiagnostic": reserved_tail,
        "developmentPolicySearch": development,
        "forwardObservation": forward,
        "forwardSourceCoverage": forward_source_coverage,
        "forwardSourceCoverageHistory": forward_source_coverage_history,
        "publicMinuteAudit": public_audit,
        "intradayObservation": detail,
        "intradayBreadth": breadth,
        "intradayContinuity": continuity,
    }
    return {
        "schema_version": 1,
        "classification": "VERIFIED_MINUTE_RESEARCH_READ_SNAPSHOT",
        "data_version": dataset.get("data_version") if dataset is not None else None,
        "dataset_checksum": dataset.get("checksum") if dataset is not None else None,
        **sections,
        "unavailable_sections": [
            name for name, value in sections.items() if value is None
        ],
        "realtime_readiness": False,
        "real_data_paper_readiness": False,
        "external_order_transmission": False,
    }


def _load_verified_intraday_observation(
    path: Path | None,
    dataset: dict[str, object] | None,
) -> dict[str, object] | None:
    if path is None or dataset is None or not path.is_file():
        return None
    try:
        content = _read_bounded_file(path, _MAX_MINUTE_EVIDENCE_JSON_BYTES)
        if content is None:
            return None
        report = json.loads(content)
        if not isinstance(report, dict):
            return None
        digest = report.get("evidence_sha256")
        unsigned = {
            key: value for key, value in report.items() if key != "evidence_sha256"
        }
        observations = report.get("observations")
        started_at = datetime.fromisoformat(str(report.get("started_at")))
        completed_at = datetime.fromisoformat(str(report.get("completed_at")))
        guard_seconds = report.get("finalization_guard_seconds")
        instruments = dataset.get("instruments")
        if not isinstance(instruments, list):
            return None
        expected: list[tuple[str, str, str, str]] = []
        seen_sectors: set[str] = set()
        for row in instruments:
            if not isinstance(row, dict):
                return None
            sector_code = str(row.get("sector_code"))
            if sector_code in seen_sectors:
                continue
            seen_sectors.add(sector_code)
            expected.append(
                (
                    str(row.get("symbol")),
                    str(row.get("name")),
                    sector_code,
                    str(row.get("sector_name")),
                )
            )
        if (
            not _is_sha256(digest)
            or _canonical_dict_sha256(cast(dict[str, object], unsigned)) != digest
            or report.get("schema_version") != 2
            or report.get("classification")
            != "FREE_HISTORICAL_SOURCE_INTRADAY_OBSERVATION_NOT_REALTIME_ELIGIBLE"
            or report.get("provider")
            != "Sina CN_MarketDataService.getKLineData"
            or report.get("bar_interval") != "1m"
            or started_at.tzinfo is None
            or completed_at.tzinfo is None
            or started_at > completed_at
            or report.get("session_phase")
            != _intraday_session_phase(completed_at)
            or report.get("data_version") != dataset["data_version"]
            or report.get("dataset_checksum") != dataset["checksum"]
            or report.get("symbol_count") != 15
            or report.get("sector_count") != 15
            or not isinstance(guard_seconds, int)
            or not 0 <= guard_seconds <= 300
            or not isinstance(observations, list)
            or len(observations) != len(expected)
            or report.get("realtime_readiness") is not False
            or report.get("real_data_paper_readiness") is not False
            or report.get("promotion") != "NOT_ELIGIBLE"
            or report.get("external_order_transmission") is not False
        ):
            return None
        same_day_count = 0
        same_day_ages: list[float] = []
        timestamp_sets: list[set[str]] = []
        allowed_times = set(SINA_COMPLETE_SESSION_TIMES)
        report_day = completed_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
        for observation, identity in zip(observations, expected, strict=True):
            if not isinstance(observation, dict):
                return None
            observed_at = datetime.fromisoformat(str(observation.get("observed_at")))
            latest_observed = datetime.fromisoformat(
                str(observation.get("latest_observed_timestamp"))
            )
            latest_finalized = datetime.fromisoformat(
                str(observation.get("latest_finalized_timestamp"))
            )
            bars = observation.get("bars")
            latest_same_day_age = observation.get(
                "latest_same_day_finalized_age_seconds"
            )
            observed_identity = (
                observation.get("symbol"),
                observation.get("name"),
                observation.get("sector_code"),
                observation.get("sector_name"),
            )
            if (
                observed_identity != identity
                or observed_at.tzinfo is None
                or not started_at <= observed_at <= completed_at
                or observation.get("session_phase")
                != _intraday_session_phase(observed_at)
                or not isinstance(observation.get("source_row_count"), int)
                or cast(int, observation["source_row_count"]) <= 0
                or latest_observed.tzinfo is None
                or latest_finalized.tzinfo is None
                or latest_observed < latest_finalized
                or latest_finalized
                > observed_at - timedelta(seconds=guard_seconds)
                or not isinstance(bars, list)
                or observation.get("same_day_finalized_bar_count") != len(bars)
                or observation.get("forming_or_uncertain_bar_excluded")
                is not (latest_observed > latest_finalized)
            ):
                return None
            previous: datetime | None = None
            observed_timestamps: set[str] = set()
            for bar in bars:
                if not isinstance(bar, dict):
                    return None
                timestamp = datetime.fromisoformat(str(bar.get("timestamp")))
                local = timestamp.astimezone(ZoneInfo("Asia/Shanghai"))
                prices = [
                    Decimal(str(bar.get(name)))
                    for name in ("open", "high", "low", "close")
                ]
                volume = Decimal(str(bar.get("volume")))
                if (
                    timestamp.tzinfo is None
                    or previous is not None
                    and timestamp <= previous
                    or timestamp > observed_at - timedelta(seconds=guard_seconds)
                    or local.date() != report_day
                    or local.time() not in allowed_times
                    or bar.get("symbol") != identity[0]
                    or bar.get("exchange") not in {"SSE", "SZSE", "BSE"}
                    or str(bar.get("trading_day")) != report_day.isoformat()
                    or not all(value.is_finite() and value > 0 for value in prices)
                    or not volume.is_finite()
                    or volume < 0
                    or prices[1] < max(prices[0], prices[3])
                    or prices[2] > min(prices[0], prices[3])
                ):
                    return None
                previous = timestamp
                observed_timestamps.add(timestamp.isoformat())
            if bars:
                same_day_count += 1
                if previous != latest_finalized:
                    return None
                expected_age = round((observed_at - latest_finalized).total_seconds(), 3)
                if (
                    not isinstance(latest_same_day_age, (int, float))
                    or isinstance(latest_same_day_age, bool)
                    or not isfinite(float(latest_same_day_age))
                    or not isclose(
                        float(latest_same_day_age), expected_age, abs_tol=0.001
                    )
                ):
                    return None
                same_day_ages.append(float(latest_same_day_age))
                timestamp_sets.append(observed_timestamps)
            elif latest_same_day_age is not None:
                return None
        synchronized = (
            sorted(set.intersection(*timestamp_sets)) if timestamp_sets else []
        )
        request_window_seconds = report.get("source_request_window_seconds")
        expected_request_window = round(
            (completed_at - started_at).total_seconds(), 3
        )
        if (
            report.get("same_day_symbol_count") != same_day_count
            or report.get("all_symbols_same_day_coverage")
            is not (same_day_count == len(expected))
            or not isinstance(request_window_seconds, (int, float))
            or isinstance(request_window_seconds, bool)
            or not isfinite(float(request_window_seconds))
            or not isclose(
                float(request_window_seconds),
                expected_request_window,
                abs_tol=0.001,
            )
            or report.get("maximum_same_day_latest_age_seconds")
            != (max(same_day_ages) if same_day_ages else None)
            or report.get("synchronized_finalized_timestamp_count")
            != len(synchronized)
            or report.get("latest_synchronized_finalized_timestamp")
            != (synchronized[-1] if synchronized else None)
        ):
            return None
        return {**report, "minute_dataset": dataset}
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


def _directional_breadth_summary(
    rows: list[dict[str, object]],
) -> dict[str, object]:
    session_returns = [
        Decimal(str(row["session_return"]))
        for row in rows
        if row.get("session_return") is not None
    ]
    latest_returns = [
        Decimal(str(row["latest_minute_return"]))
        for row in rows
        if row.get("latest_minute_return") is not None
    ]
    return {
        "eligible_symbol_count": len(session_returns),
        "above_open_count": sum(value > 0 for value in session_returns),
        "unchanged_from_open_count": sum(value == 0 for value in session_returns),
        "below_open_count": sum(value < 0 for value in session_returns),
        "latest_minute_eligible_symbol_count": len(latest_returns),
        "latest_minute_up_count": sum(value > 0 for value in latest_returns),
        "latest_minute_unchanged_count": sum(value == 0 for value in latest_returns),
        "latest_minute_down_count": sum(value < 0 for value in latest_returns),
        "equal_weight_session_return": (
            str(sum(session_returns, start=Decimal("0")) / len(session_returns))
            if session_returns
            else None
        ),
        "equal_weight_latest_minute_return": (
            str(sum(latest_returns, start=Decimal("0")) / len(latest_returns))
            if latest_returns
            else None
        ),
    }


def _load_verified_intraday_breadth(
    path: Path | None,
    dataset: dict[str, object] | None,
) -> dict[str, object] | None:
    if path is None or dataset is None or not path.is_file():
        return None
    try:
        content = _read_bounded_file(path, _MAX_MINUTE_EVIDENCE_JSON_BYTES)
        if content is None:
            return None
        report = json.loads(content)
        if not isinstance(report, dict):
            return None
        digest = report.get("evidence_sha256")
        unsigned = {
            key: value for key, value in report.items() if key != "evidence_sha256"
        }
        observations = report.get("observations")
        sector_summaries = report.get("sector_summaries")
        started_at = datetime.fromisoformat(str(report.get("started_at")))
        completed_at = datetime.fromisoformat(str(report.get("completed_at")))
        guard_seconds = report.get("finalization_guard_seconds")
        instruments = dataset.get("instruments")
        if not isinstance(instruments, list):
            return None
        expected: list[tuple[str, str, str, str]] = []
        for row in instruments:
            if not isinstance(row, dict):
                return None
            expected.append(
                (
                    str(row.get("symbol")),
                    str(row.get("name")),
                    str(row.get("sector_code")),
                    str(row.get("sector_name")),
                )
            )
        expected_sector_counts = Counter(identity[2] for identity in expected)
        if (
            len(expected) != 75
            or len({identity[0] for identity in expected}) != 75
            or len(expected_sector_counts) != 15
            or set(expected_sector_counts.values()) != {5}
            or not _is_sha256(digest)
            or _canonical_dict_sha256(cast(dict[str, object], unsigned)) != digest
            or report.get("schema_version") != 2
            or report.get("classification")
            != "FREE_HISTORICAL_SOURCE_INTRADAY_BREADTH_NOT_REALTIME_ELIGIBLE"
            or report.get("coverage_scope")
            != "ALL_FROZEN_STOCKS_15_SECTORS_X5"
            or report.get("provider")
            != "Sina CN_MarketDataService.getKLineData"
            or report.get("bar_interval") != "1m"
            or started_at.tzinfo is None
            or completed_at.tzinfo is None
            or started_at > completed_at
            or report.get("session_phase")
            != _intraday_session_phase(completed_at)
            or report.get("data_version") != dataset["data_version"]
            or report.get("dataset_checksum") != dataset["checksum"]
            or report.get("symbol_count") != len(expected)
            or report.get("sector_count") != len(expected_sector_counts)
            or report.get("symbols_per_sector") != 5
            or not isinstance(guard_seconds, int)
            or not 0 <= guard_seconds <= 300
            or not isinstance(observations, list)
            or len(observations) != len(expected)
            or not isinstance(sector_summaries, list)
            or report.get("realtime_readiness") is not False
            or report.get("real_data_paper_readiness") is not False
            or report.get("promotion") != "NOT_ELIGIBLE"
            or report.get("external_order_transmission") is not False
        ):
            return None
        report_day = completed_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
        allowed_times = set(SINA_COMPLETE_SESSION_TIMES)
        same_day_count = 0
        same_day_ages: list[float] = []
        timestamp_sets: list[set[str]] = []
        directional_rows: list[dict[str, object]] = []
        sector_rows: dict[
            str, list[tuple[set[str], float | None, dict[str, object]]]
        ] = defaultdict(list)
        sanitized_observations: list[dict[str, object]] = []
        for observation, identity in zip(observations, expected, strict=True):
            if not isinstance(observation, dict):
                return None
            observed_at = datetime.fromisoformat(str(observation.get("observed_at")))
            latest_observed = datetime.fromisoformat(
                str(observation.get("latest_observed_timestamp"))
            )
            latest_finalized_raw = observation.get("latest_finalized_timestamp")
            latest_finalized = (
                None
                if latest_finalized_raw is None
                else datetime.fromisoformat(str(latest_finalized_raw))
            )
            latest_same_day_raw = observation.get(
                "latest_same_day_finalized_timestamp"
            )
            latest_same_day = (
                None
                if latest_same_day_raw is None
                else datetime.fromisoformat(str(latest_same_day_raw))
            )
            timestamp_values = observation.get("same_day_finalized_timestamps")
            latest_same_day_age = observation.get(
                "latest_same_day_finalized_age_seconds"
            )
            first_open_raw = observation.get("first_same_day_open")
            previous_close_raw = observation.get("previous_same_day_close")
            latest_close_raw = observation.get("latest_same_day_close")
            session_return_raw = observation.get("session_return")
            latest_minute_return_raw = observation.get("latest_minute_return")
            observed_identity = (
                observation.get("symbol"),
                observation.get("name"),
                observation.get("sector_code"),
                observation.get("sector_name"),
            )
            if (
                observed_identity != identity
                or observed_at.tzinfo is None
                or not started_at <= observed_at <= completed_at
                or observation.get("session_phase")
                != _intraday_session_phase(observed_at)
                or not isinstance(observation.get("source_row_count"), int)
                or not 0 < cast(int, observation["source_row_count"]) <= 476
                or latest_observed.tzinfo is None
                or latest_finalized is not None
                and (
                    latest_finalized.tzinfo is None
                    or latest_observed < latest_finalized
                    or latest_finalized
                    > observed_at - timedelta(seconds=guard_seconds)
                )
                or not isinstance(timestamp_values, list)
                or len(timestamp_values) > len(SINA_COMPLETE_SESSION_TIMES)
                or observation.get("same_day_finalized_bar_count")
                != len(timestamp_values)
                or observation.get("forming_or_uncertain_bar_excluded")
                is not (
                    latest_finalized is None or latest_observed > latest_finalized
                )
            ):
                return None
            previous: datetime | None = None
            observed_timestamps: set[str] = set()
            for value in timestamp_values:
                timestamp = datetime.fromisoformat(str(value))
                local = timestamp.astimezone(ZoneInfo("Asia/Shanghai"))
                if (
                    timestamp.tzinfo is None
                    or previous is not None
                    and timestamp <= previous
                    or timestamp > observed_at - timedelta(seconds=guard_seconds)
                    or local.date() != report_day
                    or local.time() not in allowed_times
                ):
                    return None
                previous = timestamp
                observed_timestamps.add(timestamp.isoformat())
            if len(observed_timestamps) != len(timestamp_values):
                return None
            directional_row: dict[str, object]
            if timestamp_values:
                assert previous is not None
                first_open = Decimal(str(first_open_raw))
                latest_close = Decimal(str(latest_close_raw))
                session_return = Decimal(str(session_return_raw))
                latest_minute_return: Decimal | None = None
                if (
                    latest_finalized != previous
                    or latest_same_day != previous
                    or not isinstance(latest_same_day_age, (int, float))
                    or isinstance(latest_same_day_age, bool)
                    or not isfinite(float(latest_same_day_age))
                    or not first_open.is_finite()
                    or first_open <= 0
                    or not latest_close.is_finite()
                    or latest_close <= 0
                    or not session_return.is_finite()
                    or session_return != latest_close / first_open - Decimal("1")
                ):
                    return None
                if len(timestamp_values) >= 2:
                    previous_close = Decimal(str(previous_close_raw))
                    latest_minute_return = Decimal(str(latest_minute_return_raw))
                    if (
                        not previous_close.is_finite()
                        or previous_close <= 0
                        or not latest_minute_return.is_finite()
                        or latest_minute_return
                        != latest_close / previous_close - Decimal("1")
                    ):
                        return None
                elif (
                    previous_close_raw is not None
                    or latest_minute_return_raw is not None
                ):
                    return None
                expected_age = round((observed_at - previous).total_seconds(), 3)
                if not isclose(
                    float(latest_same_day_age), expected_age, abs_tol=0.001
                ):
                    return None
                age = float(latest_same_day_age)
                same_day_count += 1
                same_day_ages.append(age)
                directional_row = {
                    "session_return": str(session_return),
                    "latest_minute_return": (
                        str(latest_minute_return)
                        if latest_minute_return is not None
                        else None
                    ),
                }
            else:
                if (
                    latest_same_day is not None
                    or latest_same_day_age is not None
                    or first_open_raw is not None
                    or previous_close_raw is not None
                    or latest_close_raw is not None
                    or session_return_raw is not None
                    or latest_minute_return_raw is not None
                ):
                    return None
                age = None
                directional_row = {
                    "session_return": None,
                    "latest_minute_return": None,
                }
            timestamp_sets.append(observed_timestamps)
            directional_rows.append(directional_row)
            sector_rows[identity[2]].append(
                (observed_timestamps, age, directional_row)
            )
            sanitized_observations.append(
                {
                    key: value
                    for key, value in observation.items()
                    if key != "same_day_finalized_timestamps"
                }
            )
        synchronized = sorted(set.intersection(*timestamp_sets)) if timestamp_sets else []
        expected_sector_summaries: list[dict[str, object]] = []
        sector_identity = {
            identity[2]: identity[3] for identity in expected
        }
        for sector_code in dict.fromkeys(identity[2] for identity in expected):
            rows = sector_rows[sector_code]
            sector_timestamps = [row[0] for row in rows]
            sector_synchronized = (
                sorted(set.intersection(*sector_timestamps))
                if sector_timestamps
                else []
            )
            ages = [row[1] for row in rows if row[1] is not None]
            expected_sector_summaries.append(
                {
                    "sector_code": sector_code,
                    "sector_name": sector_identity[sector_code],
                    "symbol_count": len(rows),
                    "same_day_symbol_count": sum(bool(row[0]) for row in rows),
                    "synchronized_finalized_timestamp_count": len(
                        sector_synchronized
                    ),
                    "latest_synchronized_finalized_timestamp": (
                        sector_synchronized[-1] if sector_synchronized else None
                    ),
                    "maximum_same_day_latest_age_seconds": (
                        max(ages) if ages else None
                    ),
                    "directional_breadth": _directional_breadth_summary(
                        [row[2] for row in rows]
                    ),
                }
            )
        request_window_seconds = report.get("source_request_window_seconds")
        expected_request_window = round(
            (completed_at - started_at).total_seconds(), 3
        )
        if (
            report.get("same_day_symbol_count") != same_day_count
            or report.get("all_symbols_same_day_coverage")
            is not (same_day_count == len(expected))
            or not isinstance(request_window_seconds, (int, float))
            or isinstance(request_window_seconds, bool)
            or not isfinite(float(request_window_seconds))
            or not isclose(
                float(request_window_seconds),
                expected_request_window,
                abs_tol=0.001,
            )
            or report.get("maximum_same_day_latest_age_seconds")
            != (max(same_day_ages) if same_day_ages else None)
            or report.get("synchronized_finalized_timestamp_count")
            != len(synchronized)
            or report.get("latest_synchronized_finalized_timestamp")
            != (synchronized[-1] if synchronized else None)
            or sector_summaries != expected_sector_summaries
            or report.get("directional_breadth")
            != _directional_breadth_summary(directional_rows)
        ):
            return None
        return {
            **report,
            "observations": sanitized_observations,
            "minute_dataset": dataset,
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


def _continuity_summary_from_report(
    report: dict[str, object],
    *,
    include_directional: bool = True,
) -> dict[str, object]:
    summary = {
        key: report[key]
        for key in (
            "evidence_sha256",
            "completed_at",
            "session_phase",
            "symbol_count",
            "sector_count",
            "same_day_symbol_count",
            "synchronized_finalized_timestamp_count",
            "latest_synchronized_finalized_timestamp",
            "source_request_window_seconds",
            "maximum_same_day_latest_age_seconds",
            "all_symbols_same_day_coverage",
        )
    }
    if include_directional and "directional_breadth" in report:
        summary["directional_breadth"] = report["directional_breadth"]
    return summary


def _valid_directional_breadth_summary(value: object, symbol_count: int) -> bool:
    if not isinstance(value, dict):
        return False
    count_keys = (
        "eligible_symbol_count",
        "above_open_count",
        "unchanged_from_open_count",
        "below_open_count",
        "latest_minute_eligible_symbol_count",
        "latest_minute_up_count",
        "latest_minute_unchanged_count",
        "latest_minute_down_count",
    )
    if set(value) != {
        *count_keys,
        "equal_weight_session_return",
        "equal_weight_latest_minute_return",
    }:
        return False
    counts = [value.get(key) for key in count_keys]
    if any(
        not isinstance(count, int)
        or isinstance(count, bool)
        or not 0 <= count <= symbol_count
        for count in counts
    ):
        return False
    eligible = cast(int, value["eligible_symbol_count"])
    latest_eligible = cast(int, value["latest_minute_eligible_symbol_count"])
    if (
        cast(int, value["above_open_count"])
        + cast(int, value["unchanged_from_open_count"])
        + cast(int, value["below_open_count"])
        != eligible
        or cast(int, value["latest_minute_up_count"])
        + cast(int, value["latest_minute_unchanged_count"])
        + cast(int, value["latest_minute_down_count"])
        != latest_eligible
    ):
        return False
    for key, expected_count in (
        ("equal_weight_session_return", eligible),
        ("equal_weight_latest_minute_return", latest_eligible),
    ):
        raw = value.get(key)
        if expected_count == 0:
            if raw is not None:
                return False
        else:
            parsed = Decimal(str(raw))
            if not parsed.is_finite():
                return False
    return True


def _validate_continuity_summary(
    summary: object,
    *,
    symbol_count: int,
    report_day: date,
) -> datetime | None:
    if not isinstance(summary, dict):
        return None
    expected_keys = {
        "evidence_sha256",
        "completed_at",
        "session_phase",
        "symbol_count",
        "sector_count",
        "same_day_symbol_count",
        "synchronized_finalized_timestamp_count",
        "latest_synchronized_finalized_timestamp",
        "source_request_window_seconds",
        "maximum_same_day_latest_age_seconds",
        "all_symbols_same_day_coverage",
    }
    directional = summary.get("directional_breadth")
    if set(summary) not in (expected_keys, expected_keys | {"directional_breadth"}):
        return None
    if "directional_breadth" in summary and (
        symbol_count != 75
        or not _valid_directional_breadth_summary(directional, symbol_count)
    ):
        return None
    completed_at = datetime.fromisoformat(str(summary.get("completed_at")))
    latest_raw = summary.get("latest_synchronized_finalized_timestamp")
    latest = None if latest_raw is None else datetime.fromisoformat(str(latest_raw))
    same_day_count = summary.get("same_day_symbol_count")
    synchronized_count = summary.get("synchronized_finalized_timestamp_count")
    request_window = summary.get("source_request_window_seconds")
    maximum_age = summary.get("maximum_same_day_latest_age_seconds")
    if (
        not _is_sha256(summary.get("evidence_sha256"))
        or completed_at.tzinfo is None
        or completed_at.astimezone(ZoneInfo("Asia/Shanghai")).date() != report_day
        or summary.get("session_phase") != _intraday_session_phase(completed_at)
        or summary.get("symbol_count") != symbol_count
        or summary.get("sector_count") != 15
        or not isinstance(same_day_count, int)
        or isinstance(same_day_count, bool)
        or not 0 <= same_day_count <= symbol_count
        or not isinstance(synchronized_count, int)
        or isinstance(synchronized_count, bool)
        or not 0 <= synchronized_count <= len(SINA_COMPLETE_SESSION_TIMES)
        or summary.get("all_symbols_same_day_coverage")
        is not (same_day_count == symbol_count)
        or not isinstance(request_window, (int, float))
        or isinstance(request_window, bool)
        or not isfinite(float(request_window))
        or not 0 <= float(request_window) <= 300
        or maximum_age is not None
        and (
            not isinstance(maximum_age, (int, float))
            or isinstance(maximum_age, bool)
            or not isfinite(float(maximum_age))
            or float(maximum_age) < 0
        )
        or synchronized_count == 0
        and latest is not None
        or synchronized_count > 0
        and (
            latest is None
            or latest.tzinfo is None
            or latest.astimezone(ZoneInfo("Asia/Shanghai")).date() != report_day
            or latest > completed_at
        )
    ):
        return None
    return completed_at


def _continuity_non_regressing(
    previous: dict[str, object],
    current: dict[str, object],
) -> bool:
    if cast(int, current["same_day_symbol_count"]) < cast(
        int, previous["same_day_symbol_count"]
    ):
        return False
    if cast(int, current["synchronized_finalized_timestamp_count"]) < cast(
        int, previous["synchronized_finalized_timestamp_count"]
    ):
        return False
    previous_latest = previous["latest_synchronized_finalized_timestamp"]
    current_latest = current["latest_synchronized_finalized_timestamp"]
    if previous_latest is None:
        return True
    if current_latest is None:
        return False
    return datetime.fromisoformat(str(current_latest)) >= datetime.fromisoformat(
        str(previous_latest)
    )


def _load_verified_intraday_continuity(
    path: Path | None,
    detail: dict[str, object] | None,
    breadth: dict[str, object] | None,
) -> dict[str, object] | None:
    if path is None or detail is None or breadth is None or not path.is_file():
        return None
    try:
        content = _read_bounded_file(path, _MAX_MINUTE_EVIDENCE_JSON_BYTES)
        if content is None:
            return None
        report = json.loads(content)
        if not isinstance(report, dict):
            return None
        digest = report.get("evidence_sha256")
        unsigned = {
            key: value for key, value in report.items() if key != "evidence_sha256"
        }
        checkpoints = report.get("checkpoints")
        report_day = date.fromisoformat(str(report.get("trading_day")))
        schema_version = report.get("schema_version")
        directional_first_sequence = report.get(
            "directional_breadth_first_sequence"
        )
        if (
            not _is_sha256(digest)
            or _canonical_dict_sha256(cast(dict[str, object], unsigned)) != digest
            or schema_version not in {1, 2}
            or report.get("classification")
            != "SIGNED_INTRADAY_SOURCE_CONTINUITY_NOT_REALTIME_ELIGIBLE"
            or not isinstance(checkpoints, list)
            or not 1 <= len(checkpoints) <= 32
            or report.get("checkpoint_count") != len(checkpoints)
            or report.get("realtime_readiness") is not False
            or report.get("real_data_paper_readiness") is not False
            or report.get("promotion") != "NOT_ELIGIBLE"
            or report.get("external_order_transmission") is not False
            or schema_version == 1
            and directional_first_sequence is not None
            or schema_version == 2
            and (
                not isinstance(directional_first_sequence, int)
                or isinstance(directional_first_sequence, bool)
                or not 1 <= directional_first_sequence <= len(checkpoints)
            )
        ):
            return None
        previous_hash: str | None = None
        previous_checkpoint: dict[str, object] | None = None
        monotonic_values: list[bool] = []
        for index, checkpoint in enumerate(checkpoints, start=1):
            if not isinstance(checkpoint, dict):
                return None
            checkpoint_digest = checkpoint.get("checkpoint_sha256")
            checkpoint_unsigned = {
                key: value
                for key, value in checkpoint.items()
                if key != "checkpoint_sha256"
            }
            captured_at = datetime.fromisoformat(str(checkpoint.get("captured_at")))
            detail_summary = checkpoint.get("detail")
            breadth_summary = checkpoint.get("breadth")
            detail_completed = _validate_continuity_summary(
                detail_summary,
                symbol_count=15,
                report_day=report_day,
            )
            breadth_completed = _validate_continuity_summary(
                breadth_summary,
                symbol_count=75,
                report_day=report_day,
            )
            breadth_has_directional = (
                isinstance(breadth_summary, dict)
                and "directional_breadth" in breadth_summary
            )
            expected_directional = (
                isinstance(directional_first_sequence, int)
                and index >= directional_first_sequence
            )
            if (
                checkpoint.get("sequence") != index
                or checkpoint.get("trading_day") != report_day.isoformat()
                or captured_at.tzinfo is None
                or detail_completed is None
                or breadth_completed is None
                or captured_at != max(detail_completed, breadth_completed)
                or checkpoint.get("previous_checkpoint_sha256") != previous_hash
                or not _is_sha256(checkpoint_digest)
                or _canonical_dict_sha256(
                    cast(dict[str, object], checkpoint_unsigned)
                )
                != checkpoint_digest
                or breadth_has_directional is not expected_directional
            ):
                return None
            expected_monotonic = previous_checkpoint is None or (
                _continuity_non_regressing(
                    cast(dict[str, object], previous_checkpoint["detail"]),
                    cast(dict[str, object], detail_summary),
                )
                and _continuity_non_regressing(
                    cast(dict[str, object], previous_checkpoint["breadth"]),
                    cast(dict[str, object], breadth_summary),
                )
            )
            if checkpoint.get("monotonic_vs_previous") is not expected_monotonic:
                return None
            monotonic_values.append(expected_monotonic)
            previous_hash = str(checkpoint_digest)
            previous_checkpoint = cast(dict[str, object], checkpoint)
        latest_checkpoint = cast(dict[str, object], checkpoints[-1])
        if (
            report.get("all_checkpoints_monotonic") is not all(monotonic_values)
            or latest_checkpoint.get("detail")
            != _continuity_summary_from_report(detail)
            or latest_checkpoint.get("breadth")
            != _continuity_summary_from_report(
                breadth,
                include_directional=schema_version == 2,
            )
        ):
            return None
        return cast(dict[str, object], report)
    except (
        KeyError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


def _intraday_session_phase(instant: datetime) -> str:
    local = instant.astimezone(ZoneInfo("Asia/Shanghai")).time()
    if local < datetime_time(9, 30):
        return "PRE_OPEN"
    if local <= datetime_time(11, 30):
        return "MORNING_SESSION"
    if local < datetime_time(13, 0):
        return "MIDDAY_BREAK"
    if local <= datetime_time(15, 0):
        return "AFTERNOON_SESSION"
    return "POST_CLOSE"


def _load_backtest_preview_bars(
    manifest_path: Path | None,
    *,
    data_version: str,
    expected_checksum: str,
    expected_universe: object,
    expected_bar_count: object,
) -> list[Bar] | None:
    if manifest_path is None or not isinstance(expected_universe, dict):
        return None
    symbols = expected_universe.get("symbols")
    if (
        not isinstance(symbols, list)
        or not symbols
        or not all(isinstance(value, str) for value in symbols)
        or len(set(symbols)) != len(symbols)
        or not isinstance(expected_bar_count, int)
        or expected_bar_count <= 0
        or expected_bar_count > _MAX_BACKTEST_PREVIEW_SOURCE_ROWS
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
        observed: set[str] = set()
        previous_instants: dict[str, datetime] = {}
        previous_days: dict[str, date] = {}
        records: list[Bar] = []
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
                row, instant = validated
                symbol = row["symbol"]
                trading_day = date.fromisoformat(row["trading_day"])
                previous = previous_instants.get(symbol)
                previous_day = previous_days.get(symbol)
                if (
                    previous is not None
                    and (
                        previous_day is None
                        or instant <= previous
                        or trading_day <= previous_day
                    )
                ):
                    return None
                previous_instants[symbol] = instant
                previous_days[symbol] = trading_day
                observed.add(symbol)
                records.append(
                    Bar(
                        symbol=symbol,
                        exchange=row["exchange"],
                        timestamp=instant,
                        trading_day=trading_day,
                        open=Decimal(row["open"]),
                        high=Decimal(row["high"]),
                        low=Decimal(row["low"]),
                        close=Decimal(row["close"]),
                        volume=Decimal(row["volume"]),
                    )
                )
        if len(records) != expected_bar_count or observed != allowed:
            return None
        return records
    except (OSError, ValueError, TypeError, InvalidOperation, pa.ArrowException):
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


def _load_minute_bar_series(
    manifest_path: Path | None,
    *,
    symbol: str,
    data_version: str,
    expected_checksum: str,
    expected_symbols: object,
    expected_bar_count: object,
    expected_trading_days: object,
    expected_times: tuple[datetime_time, ...] = SINA_COMPLETE_SESSION_TIMES,
    observation_type: str = "HISTORICAL_MINUTE_BAR",
) -> tuple[list[dict[str, str]], int] | None:
    if (
        manifest_path is None
        or not isinstance(expected_symbols, list)
        or not expected_symbols
        or not all(isinstance(value, str) for value in expected_symbols)
        or len(set(expected_symbols)) != len(expected_symbols)
        or symbol not in expected_symbols
        or not isinstance(expected_bar_count, int)
        or expected_bar_count <= 0
        or not isinstance(expected_trading_days, list)
        or not all(isinstance(value, str) for value in expected_trading_days)
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
        allowed = set(expected_symbols)
        allowed_days = set(expected_trading_days)
        allowed_times = set(expected_times)
        selected: deque[dict[str, str]] = deque(maxlen=_MAX_HISTORICAL_SERIES_ROWS)
        previous_instants: dict[str, datetime] = {}
        counts: Counter[tuple[str, str]] = Counter()
        observed: set[str] = set()
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
                local = instant.astimezone(ZoneInfo("Asia/Shanghai"))
                previous = previous_instants.get(candidate_symbol)
                if (
                    previous is not None
                    and instant <= previous
                    or candidate["trading_day"] not in allowed_days
                    or local.time() not in allowed_times
                ):
                    return None
                previous_instants[candidate_symbol] = instant
                observed.add(candidate_symbol)
                counts[(candidate_symbol, candidate["trading_day"])] += 1
                if candidate_symbol == symbol:
                    total_count += 1
                    selected.append(
                        {
                            **candidate,
                            "observation_type": observation_type,
                        }
                    )
        expected_sessions = {
            (candidate_symbol, trading_day)
            for candidate_symbol in allowed
            for trading_day in allowed_days
        }
        if (
            observed != allowed
            or set(counts) != expected_sessions
            or set(counts.values()) != {len(expected_times)}
            or total_count
            != len(expected_trading_days) * len(expected_times)
        ):
            return None
        return list(selected), total_count
    except (OSError, ValueError, TypeError, InvalidOperation, pa.ArrowException):
        return None


def _load_minute_bar_day_series(
    manifest_path: Path | None,
    *,
    symbol: str,
    trading_day: str,
    data_version: str,
    expected_checksum: str,
    expected_symbols: object,
    expected_bar_count: object,
    expected_trading_days: object,
    expected_times: tuple[datetime_time, ...],
) -> list[dict[str, str]] | None:
    if (
        manifest_path is None
        or not isinstance(expected_symbols, list)
        or not expected_symbols
        or not all(isinstance(value, str) for value in expected_symbols)
        or len(set(expected_symbols)) != len(expected_symbols)
        or symbol not in expected_symbols
        or not isinstance(expected_bar_count, int)
        or expected_bar_count <= 0
        or not isinstance(expected_trading_days, list)
        or trading_day not in expected_trading_days
    ):
        return None
    try:
        selected_date = date.fromisoformat(trading_day)
        parquet = _open_verified_market_parquet(
            manifest_path,
            expected_checksum=expected_checksum,
            expected_bar_count=expected_bar_count,
        )
        if parquet is None:
            return None
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
        table = parquet.read(columns=columns)
        selected_table = table.filter(
            pc.and_(
                pc.equal(table["symbol"], symbol),
                pc.equal(table["trading_day"], selected_date),
            )
        )
        if selected_table.num_rows != len(expected_times):
            return None
        allowed = set(cast(list[str], expected_symbols))
        bars: list[dict[str, str]] = []
        observed_times: list[datetime_time] = []
        previous: datetime | None = None
        for raw in selected_table.to_pylist():
            validated = _validated_historical_bar(raw, data_version, allowed)
            if validated is None:
                return None
            candidate, instant = validated
            local = instant.astimezone(ZoneInfo("Asia/Shanghai"))
            if (
                candidate["symbol"] != symbol
                or candidate["trading_day"] != trading_day
                or previous is not None
                and instant <= previous
            ):
                return None
            previous = instant
            observed_times.append(local.time())
            bars.append(
                {
                    **candidate,
                    "observation_type": "HISTORICAL_FIVE_MINUTE_BAR",
                }
            )
        if tuple(observed_times) != expected_times:
            return None
        return bars
    except (OSError, ValueError, TypeError, InvalidOperation, pa.ArrowException):
        return None


def _single_stock_five_minute_policy(
    payload: SingleStockFiveMinuteBacktestRequest,
) -> MinuteOverlayPolicy:
    if not re.fullmatch(r"[0-9]{6}", payload.symbol):
        raise ValueError("single-stock symbol must contain exactly six digits")
    if payload.start >= payload.end:
        raise ValueError("single-stock backtest start must precede end")
    decimal_values = (
        payload.cost_bps,
        payload.maximum_minute_volatility,
        payload.maximum_intraday_drawdown,
        payload.reduction_multiplier,
    )
    if any(not value.is_finite() for value in decimal_values):
        raise ValueError("single-stock backtest numeric parameters must be finite")
    if not Decimal("0") <= payload.cost_bps <= Decimal("100"):
        raise ValueError("single-stock backtest cost must be in [0, 100] bps")
    if not 2 <= payload.short_window <= 96:
        raise ValueError("single-stock short window must be in [2, 96]")
    if not 3 <= payload.long_window <= 240:
        raise ValueError("single-stock long window must be in [3, 240]")
    if not 2 <= payload.volatility_window <= 240:
        raise ValueError("single-stock volatility window must be in [2, 240]")
    if payload.maximum_minute_volatility > Decimal("1"):
        raise ValueError("single-stock maximum minute volatility cannot exceed one")
    if payload.cooldown_bars > 240:
        raise ValueError("single-stock cooldown cannot exceed 240 bars")
    return MinuteOverlayPolicy(
        short_window=payload.short_window,
        long_window=payload.long_window,
        volatility_window=payload.volatility_window,
        maximum_minute_volatility=payload.maximum_minute_volatility,
        maximum_intraday_drawdown=payload.maximum_intraday_drawdown,
        reduction_multiplier=payload.reduction_multiplier,
        cooldown_bars=payload.cooldown_bars,
    )


def _load_single_stock_five_minute_backtest_bars(
    manifest_path: Path | None,
    *,
    symbol: str,
    through_day: date,
    data_version: str,
    expected_checksum: str,
    expected_bar_count: int,
    expected_trading_days: list[str],
    expected_times: tuple[datetime_time, ...],
) -> list[Bar] | None:
    through_text = through_day.isoformat()
    if manifest_path is None or through_text not in expected_trading_days:
        return None
    try:
        content = _read_verified_market_parquet_content(
            manifest_path,
            expected_checksum=expected_checksum,
            expected_bar_count=expected_bar_count,
        )
        if content is None:
            return None
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
        table = pq.read_table(
            pa.BufferReader(content),
            columns=columns,
            filters=[("symbol", "=", symbol), ("trading_day", "<=", through_day)],
        )
        expected_days = expected_trading_days[
            : expected_trading_days.index(through_text) + 1
        ]
        if table.num_rows != len(expected_days) * len(expected_times):
            return None
        allowed = {symbol}
        bars: list[Bar] = []
        observed_times: defaultdict[str, list[datetime_time]] = defaultdict(list)
        previous: datetime | None = None
        for raw in table.to_pylist():
            validated = _validated_historical_bar(raw, data_version, allowed)
            if validated is None:
                return None
            candidate, instant = validated
            trading_day = candidate["trading_day"]
            local = instant.astimezone(ZoneInfo("Asia/Shanghai"))
            if (
                candidate["symbol"] != symbol
                or trading_day not in expected_days
                or previous is not None
                and instant <= previous
            ):
                return None
            previous = instant
            observed_times[trading_day].append(local.time())
            bars.append(
                Bar(
                    symbol=symbol,
                    exchange=candidate["exchange"],
                    timestamp=instant,
                    trading_day=date.fromisoformat(trading_day),
                    open=Decimal(candidate["open"]),
                    high=Decimal(candidate["high"]),
                    low=Decimal(candidate["low"]),
                    close=Decimal(candidate["close"]),
                    volume=Decimal(candidate["volume"]),
                )
            )
        if list(observed_times) != expected_days or any(
            tuple(observed_times[trading_day]) != expected_times
            for trading_day in expected_days
        ):
            return None
        return bars
    except (OSError, ValueError, TypeError, InvalidOperation, pa.ArrowException):
        return None


def _finite_float_text(value: float) -> str:
    if not isfinite(value):
        raise ValueError("single-stock backtest produced a non-finite metric")
    return "0" if value == 0 else format(value, ".17g")


def _minute_curve_metrics_payload(
    metrics: MinuteCurveMetrics,
) -> dict[str, object]:
    return {
        "total_return": _finite_float_text(metrics.total_return),
        "maximum_drawdown": _finite_float_text(metrics.maximum_drawdown),
        "realized_minute_volatility": _finite_float_text(
            metrics.realized_minute_volatility
        ),
        "point_count": metrics.point_count,
    }


def _minute_execution_payload(raw: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in raw.items():
        if isinstance(value, float):
            result[key] = _finite_float_text(value)
        else:
            result[key] = value
    return result


def _single_stock_five_minute_curve(
    result: MinuteBacktestResult,
    selected_days: set[date],
) -> list[dict[str, str]]:
    indexes = [
        index
        for index, trading_day in enumerate(result.trading_days)
        if trading_day in selected_days
    ]
    if not indexes:
        raise ValueError("single-stock curve selection cannot be empty")
    first = indexes[0]
    base_denominator = 1.0 if first == 0 else result.base_equity[first - 1]
    overlay_denominator = 1.0 if first == 0 else result.overlay_equity[first - 1]
    base_peak = 1.0
    overlay_peak = 1.0
    curve: list[dict[str, str]] = []
    for index in indexes:
        base = result.base_equity[index] / base_denominator
        overlay = result.overlay_equity[index] / overlay_denominator
        base_peak = max(base_peak, base)
        overlay_peak = max(overlay_peak, overlay)
        curve.append(
            {
                "session": result.timestamps[index]
                .astimezone(ZoneInfo("Asia/Shanghai"))
                .isoformat(),
                "equity": _finite_float_text(overlay),
                "drawdown": _finite_float_text(overlay / overlay_peak - 1),
                "benchmark_equity": _finite_float_text(base),
                "benchmark_drawdown": _finite_float_text(base / base_peak - 1),
                "excess_return": _finite_float_text(overlay - base),
            }
        )
    return curve


def _single_stock_five_minute_preview(
    *,
    payload: SingleStockFiveMinuteBacktestRequest,
    policy: MinuteOverlayPolicy,
    result: MinuteBacktestResult,
    selected_days: set[date],
    selected_trading_days: list[str],
    warmup_session_count: int,
    data_version: str,
    dataset_checksum: str,
    available_trading_days: list[str],
    verified_universe_symbol_count: int,
    instrument: dict[str, object],
) -> dict[str, object]:
    metrics = result.metrics(days=selected_days)
    policy_payload = {key: str(value) for key, value in asdict(policy).items()}
    parameters: dict[str, object] = {
        "symbol": payload.symbol,
        "start": payload.start.isoformat(),
        "end": payload.end.isoformat(),
        "cost_bps": str(payload.cost_bps),
        **{
            key: int(value) if key.endswith("window") or key == "cooldown_bars" else value
            for key, value in policy_payload.items()
        },
    }
    daily = []
    for trading_day_text in selected_trading_days:
        trading_day = date.fromisoformat(trading_day_text)
        day_metrics = result.metrics(days={trading_day})
        daily.append(
            {
                "trading_day": trading_day_text,
                "base": _minute_curve_metrics_payload(day_metrics["base"]),
                "overlay": _minute_curve_metrics_payload(day_metrics["overlay"]),
                "excess_return": _finite_float_text(
                    day_metrics["overlay"].total_return
                    - day_metrics["base"].total_return
                ),
                "execution": _minute_execution_payload(
                    result.execution_metrics(days={trading_day})
                ),
            }
        )
    unsigned: dict[str, object] = {
        "classification": "AD_HOC_SINGLE_STOCK_FIVE_MINUTE_BACKTEST_CURRENT_NON_PIT",
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": [
            "AD_HOC_PARAMETERS_NOT_PREREGISTERED",
            "CURRENT_NON_POINT_IN_TIME_UNIVERSE",
            "QFQ_MAY_USE_FUTURE_ADJUSTMENT_KNOWLEDGE",
            "SINGLE_STOCK_RESULT_DOES_NOT_PROVE_CROSS_SECTIONAL_ROBUSTNESS",
            "FREE_HISTORICAL_SOURCE_NOT_AUTHORIZED_REALTIME_FEED",
        ],
        "symbol": payload.symbol,
        "instrument": {
            "name": instrument["name"],
            "sector_code": instrument["sector_code"],
            "sector_name": instrument["sector_name"],
        },
        "data_version": data_version,
        "dataset_checksum": dataset_checksum,
        "data_interval": "5m",
        "verified_universe_symbol_count": verified_universe_symbol_count,
        "available_session_count": len(available_trading_days),
        "available_start": available_trading_days[0],
        "available_end": available_trading_days[-1],
        "maximum_selectable_session_count": _MAX_SINGLE_STOCK_BACKTEST_SESSIONS,
        "selected_trading_days": selected_trading_days,
        "selected_session_count": len(selected_trading_days),
        "selected_bar_count": len(selected_trading_days)
        * len(SINA_FIVE_MINUTE_SESSION_TIMES),
        "warmup_session_count": warmup_session_count,
        "warmup_bar_count": warmup_session_count
        * len(SINA_FIVE_MINUTE_SESSION_TIMES),
        "parameters": parameters,
        "policy_id": result.policy_id,
        "policy_hash": result.policy_hash,
        "policy": policy_payload,
        "cost_bps": _finite_float_text(result.cost_bps),
        "metrics": {
            "base": _minute_curve_metrics_payload(metrics["base"]),
            "overlay": _minute_curve_metrics_payload(metrics["overlay"]),
            "excess_return": _finite_float_text(
                metrics["overlay"].total_return - metrics["base"].total_return
            ),
        },
        "execution": _minute_execution_payload(
            result.execution_metrics(days=selected_days)
        ),
        "daily": daily,
        "curve": _single_stock_five_minute_curve(result, selected_days),
        "causal_contract": {
            "signal_observed_at": "FIVE_MINUTE_BAR_CLOSE_T",
            "exposure_change_at": "NEXT_FIVE_MINUTE_BAR_OPEN",
            "same_bar_signal_execution": False,
            "reduction_only": True,
            "maximum_exposure": _finite_float_text(result.maximum_exposure),
            "prior_history_used_only_as_causal_warmup": True,
        },
        "point_in_time": False,
        "future_adjustment_knowledge_possible": True,
        "realtime_readiness": False,
        "real_data_paper_readiness": False,
        "state_mutation": False,
        "external_order_transmission": False,
    }
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        **unsigned,
        "preview_hash": sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _load_intraday_factor_bars(
    manifest_path: Path | None,
    *,
    trading_days: list[str],
    symbols: list[str],
    data_version: str,
    expected_checksum: str,
    expected_bar_count: int,
    expected_times: tuple[datetime_time, ...],
) -> list[Bar] | None:
    if (
        manifest_path is None
        or not trading_days
        or not symbols
        or trading_days != sorted(trading_days)
        or symbols != sorted(symbols)
        or len(set(trading_days)) != len(trading_days)
        or len(set(symbols)) != len(symbols)
    ):
        return None
    try:
        content = _read_verified_market_parquet_content(
            manifest_path,
            expected_checksum=expected_checksum,
            expected_bar_count=expected_bar_count,
        )
        if content is None:
            return None
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
        first_day = date.fromisoformat(trading_days[0])
        last_day = date.fromisoformat(trading_days[-1])
        table = pq.read_table(
            pa.BufferReader(content),
            columns=columns,
            filters=[
                ("trading_day", ">=", first_day),
                ("trading_day", "<=", last_day),
            ],
        )
        expected_rows = len(trading_days) * len(symbols) * len(expected_times)
        if table.num_rows != expected_rows:
            return None
        allowed = set(symbols)
        allowed_days = set(trading_days)
        bars: list[Bar] = []
        observed_times: defaultdict[
            tuple[str, str], list[datetime_time]
        ] = defaultdict(list)
        for raw in table.to_pylist():
            validated = _validated_historical_bar(raw, data_version, allowed)
            if validated is None:
                return None
            candidate, instant = validated
            symbol = candidate["symbol"]
            trading_day = candidate["trading_day"]
            if trading_day not in allowed_days:
                return None
            observed_times[(symbol, trading_day)].append(
                instant.astimezone(ZoneInfo("Asia/Shanghai")).time()
            )
            bars.append(
                Bar(
                    symbol=symbol,
                    exchange=candidate["exchange"],
                    timestamp=instant,
                    trading_day=date.fromisoformat(trading_day),
                    open=Decimal(candidate["open"]),
                    high=Decimal(candidate["high"]),
                    low=Decimal(candidate["low"]),
                    close=Decimal(candidate["close"]),
                    volume=Decimal(candidate["volume"]),
                )
            )
        expected_keys = {
            (symbol, trading_day)
            for symbol in symbols
            for trading_day in trading_days
        }
        if set(observed_times) != expected_keys or any(
            tuple(observed_times[key]) != expected_times for key in expected_keys
        ):
            return None
        bars.sort(key=lambda item: (item.symbol, item.trading_day, item.timestamp))
        return bars
    except (OSError, ValueError, TypeError, InvalidOperation, pa.ArrowException):
        return None


def _performance_metrics_payload(metrics: PerformanceMetrics) -> dict[str, object]:
    return {
        key: value if isinstance(value, int) else str(value)
        for key, value in asdict(metrics).items()
    }


def _intraday_group_payload(group: IntradayFactorGroup) -> dict[str, object]:
    return {
        "group_id": group.group_id,
        "group_name": group.group_name,
        "observation_count": group.observation_count,
        "signal_count": group.signal_count,
        "mean_signal_forward_return": str(group.mean_signal_forward_return),
        "signal_hit_rate": str(group.signal_hit_rate),
        "strategy_metrics": _performance_metrics_payload(group.strategy_metrics),
        "benchmark_metrics": _performance_metrics_payload(group.benchmark_metrics),
    }


def _intraday_factor_correlation_preview(
    *,
    payload: IntradayFactorCorrelationRequest,
    specs: tuple[OpeningTripleBullishSpec, ...],
    result: IntradayRuleCorrelationResult,
    effectiveness: tuple[IntradayRuleEffectiveness, ...],
    dataset: dict[str, object],
    required_trading_days: list[str],
    source_bar_count: int,
) -> dict[str, object]:
    rule_ids = tuple(f"R{index:02d}" for index in range(1, len(specs) + 1))
    rules = [
        {
            "rule_id": rule_id,
            "factor_id": spec.factor_id,
            "name": spec.factor_name,
            "expression": spec.factor_expression,
            "directions": list(spec.directions),
            "combination": spec.combination,
            "minimum_body_return_bps": str(spec.minimum_body_return_bps),
        }
        for rule_id, spec in zip(rule_ids, specs, strict=True)
    ]
    unsigned: dict[str, object] = {
        "classification": (
            "AD_HOC_INTRADAY_FACTOR_CORRELATION_CURRENT_NON_PIT"
        ),
        "parameters": {
            "start": payload.start.isoformat(),
            "end": payload.end.isoformat(),
            "rules": [
                {
                    "directions": list(rule.directions),
                    "combination": rule.combination,
                    "minimum_body_return_bps": str(
                        rule.minimum_body_return_bps
                    ),
                }
                for rule in payload.rules
            ],
        },
        "score_contract": {
            "source_interval": "NATIVE_5M_AGGREGATED_TO_10M",
            "source_timestamp_convention": "BAR_END_TIME",
            "source_bars": "09:35,09:40,09:45,09:50,09:55,10:00",
            "known_at": "10:00_BAR_CLOSE",
            "score_definition": (
                "ALL_MIN_OR_ANY_MAX_OF_SIGNED_TEN_MINUTE_BODY_RETURNS"
            ),
            "binary_signal_used": False,
            "future_features_used": False,
            "arbitrary_code_execution": False,
        },
        "rules": rules,
        "multiple_testing_contract": {
            "method": "HOLM_BONFERRONI",
            "family_size": len(effectiveness),
            "alpha": "0.05",
            "raw_p_value": (
                "TWO_SIDED_ASYMPTOTIC_NORMAL_FROM_NEWEY_WEST_T"
            ),
            "effect_direction": "POSITIVE_SESSION_CONDITIONAL_EXCESS",
            "minimum_clustered_signal_sessions": 10,
            "minimum_signal_count": 20,
            "minimum_t_statistic": "1.96",
        },
        "effectiveness": [
            {
                "rule_id": row.rule_id,
                "minimum_body_return_bps": str(
                    row.minimum_body_return_bps
                ),
                "observation_count": row.observation_count,
                "signal_count": row.signal_count,
                "clustered_signal_session_count": (
                    row.clustered_signal_session_count
                ),
                "mean_session_conditional_excess_return": str(
                    row.mean_session_conditional_excess_return
                ),
                "newey_west_lag": row.newey_west_lag,
                "newey_west_standard_error": str(
                    row.newey_west_standard_error
                ),
                "newey_west_t_statistic": str(
                    row.newey_west_t_statistic
                ),
                "confidence_interval_95_lower": str(
                    row.confidence_interval_95_lower
                ),
                "confidence_interval_95_upper": str(
                    row.confidence_interval_95_upper
                ),
                "raw_p_value": str(row.raw_p_value),
                "holm_adjusted_p_value": str(row.holm_adjusted_p_value),
                "criteria": dict(row.criteria),
                "passed": row.passed,
            }
            for row in effectiveness
        ],
        "effective_rule_count": sum(row.passed for row in effectiveness),
        "observation_count": result.observation_count,
        "matrix": [
            {
                "rule_id": rule_id,
                "correlations": {
                    column_id: str(value)
                    for column_id, value in zip(
                        rule_ids,
                        row,
                        strict=True,
                    )
                },
            }
            for rule_id, row in zip(rule_ids, result.matrix, strict=True)
        ],
        "pairs": [
            {
                "left_rule_id": pair.left_rule_id,
                "right_rule_id": pair.right_rule_id,
                "correlation": str(pair.correlation),
                "redundant": pair.redundant,
            }
            for pair in result.pairs
        ],
        "redundancy_threshold": str(result.redundancy_threshold),
        "maximum_absolute_correlation": str(
            result.maximum_absolute_correlation
        ),
        "passed": result.passed,
        "data_version": dataset["data_version"],
        "dataset_checksum": dataset["checksum"],
        "data_interval": "5m",
        "selected_trading_days": required_trading_days[:-1],
        "exit_completion_day": required_trading_days[-1],
        "source_bar_count": source_bar_count,
        "symbol_count": dataset["symbol_count"],
        "sector_count": dataset["sector_count"],
        "point_in_time": False,
        "future_adjustment_knowledge_possible": True,
        "realtime_readiness": False,
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": [
            "AD_HOC_CORRELATION_DIAGNOSTIC_ONLY",
            "CURRENT_CONSTITUENTS_ARE_NOT_POINT_IN_TIME",
            "NO_AUTHORIZED_FORWARD_REALTIME_OBSERVATION",
        ],
        "paper_readiness": "NOT_ELIGIBLE",
        "live_readiness": "DISABLED_REQUIRES_SEPARATE_AUTHORIZATION",
        "state_mutation": False,
        "external_order_transmission": False,
    }
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        **unsigned,
        "preview_hash": sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _intraday_factor_discovery_preview(
    *,
    payload: IntradayFactorDiscoveryRequest,
    result: IntradayFactorDiscoveryResult,
    dataset: dict[str, object],
    required_trading_days: list[str],
    source_bar_count: int,
    symbol_count: int,
    sector_count: int,
) -> dict[str, object]:
    selected = next(
        candidate
        for candidate in result.candidates
        if candidate.candidate_id == result.selected_candidate_id
    )
    first_test_signal_known_at = datetime.combine(
        result.test_start,
        datetime_time(10, 0),
        ZoneInfo("Asia/Shanghai"),
    )
    unsigned: dict[str, object] = {
        "classification": (
            "BOUNDED_INTRADAY_FACTOR_DISCOVERY_REUSED_HISTORY_NOT_INDEPENDENT"
        ),
        "parameters": {
            "start": payload.start.isoformat(),
            "end": payload.end.isoformat(),
            "cost_bps": str(payload.cost_bps),
        },
        "candidate_contract": {
            "direction_values": ["BULLISH", "BEARISH", "IGNORE"],
            "active_slot_requirement": "AT_LEAST_ONE",
            "combination": "ALL",
            "thresholds_bps": ["0", "5", "10", "20"],
            "candidate_count": result.candidate_count,
            "arbitrary_code_execution": False,
            "future_features_used": False,
        },
        "split": {
            "training_start": result.training_start.isoformat(),
            "training_end": result.training_end.isoformat(),
            "training_session_count": result.training_session_count,
            "test_start": result.test_start.isoformat(),
            "test_end": result.test_end.isoformat(),
            "test_session_count": result.test_session_count,
            "selection_known_at": result.selection_known_at.isoformat(),
            "first_test_signal_known_at": (
                first_test_signal_known_at.isoformat()
            ),
            "selection_uses_test_returns": result.selection_uses_test_returns,
            "chronological_disjoint": result.training_end < result.test_start,
        },
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "directions": list(candidate.directions),
                "combination": candidate.combination,
                "minimum_body_return_bps": str(
                    candidate.minimum_body_return_bps
                ),
                "factor_expression": candidate.factor_expression,
                "training_observation_count": (
                    candidate.training_observation_count
                ),
                "training_signal_count": candidate.training_signal_count,
                "training_signal_session_count": (
                    candidate.training_signal_session_count
                ),
                "training_mean_session_conditional_excess_return": str(
                    candidate.training_mean_session_conditional_excess_return
                ),
                "training_newey_west_t_statistic": str(
                    candidate.training_newey_west_t_statistic
                ),
                "training_strategy_metrics": _performance_metrics_payload(
                    candidate.training_strategy_metrics
                ),
                "training_benchmark_metrics": _performance_metrics_payload(
                    candidate.training_benchmark_metrics
                ),
                "training_gate_criteria": dict(
                    candidate.training_gate_criteria
                ),
                "training_gate_passed": candidate.training_gate_passed,
            }
            for candidate in result.candidates
        ],
        "selected": {
            "candidate_id": result.selected_candidate_id,
            "directions": list(result.selected_directions),
            "combination": "ALL",
            "minimum_body_return_bps": str(
                result.selected_minimum_body_return_bps
            ),
            "factor_expression": result.selected_factor_expression,
            "training_gate_passed": result.selected_training_gate_passed,
            "training_signal_count": selected.training_signal_count,
            "training_signal_session_count": (
                selected.training_signal_session_count
            ),
            "training_mean_session_conditional_excess_return": str(
                selected.training_mean_session_conditional_excess_return
            ),
            "training_newey_west_t_statistic": str(
                selected.training_newey_west_t_statistic
            ),
            "training_strategy_metrics": _performance_metrics_payload(
                selected.training_strategy_metrics
            ),
            "selection_rule": (
                "TRAINING_GATE_THEN_NEWEY_WEST_T_THEN_EFFECT_"
                "THEN_LOWER_THRESHOLD_THEN_ID"
            ),
        },
        "overfitting_audit": {
            "method": result.overfitting_audit.method,
            "block_count": result.overfitting_audit.block_count,
            "split_count": result.overfitting_audit.split_count,
            "selection_metric": result.overfitting_audit.selection_metric,
            "rank_tie_method": result.overfitting_audit.rank_tie_method,
            "rank_components": [
                "GATE_PASSED",
                "NEWEY_WEST_T_STATISTIC",
                "SESSION_CONDITIONAL_EXCESS",
                "LOWER_BODY_THRESHOLD",
            ],
            "block_partition": (
                "EIGHT_CONTIGUOUS_BALANCED_BLOCKS_OLDEST_TO_NEWEST"
            ),
            "training_blocks_per_split": 4,
            "test_blocks_per_split": 4,
            "reuses_original_locked_test_blocks": True,
            "candidate_selection_uses_only_each_cscv_training_partition": True,
            "changes_original_locked_candidate": False,
            "independent_confirmation": False,
            "promotion_authority": False,
            "blocks": [
                {
                    "block_id": block.block_id,
                    "start": block.start.isoformat(),
                    "end": block.end.isoformat(),
                    "session_count": block.session_count,
                }
                for block in result.overfitting_audit.blocks
            ],
            "splits": [
                {
                    "split_id": split.split_id,
                    "training_block_ids": list(split.training_block_ids),
                    "test_block_ids": list(split.test_block_ids),
                    "selected_candidate_id": split.selected_candidate_id,
                    "training_excess_return": str(
                        split.training_excess_return
                    ),
                    "test_excess_return": str(split.test_excess_return),
                    "test_average_rank": str(split.test_average_rank),
                    "test_rank_percentile": str(
                        split.test_rank_percentile
                    ),
                    "test_rank_logit": str(split.test_rank_logit),
                    "below_median": split.below_median,
                }
                for split in result.overfitting_audit.splits
            ],
            "below_median_split_count": (
                result.overfitting_audit.below_median_split_count
            ),
            "probability_of_backtest_overfitting": str(
                result.overfitting_audit.probability_of_backtest_overfitting
            ),
            "selected_oos_positive_excess_split_count": (
                result.overfitting_audit.selected_oos_positive_excess_split_count
            ),
            "selected_oos_positive_excess_ratio": str(
                result.overfitting_audit.selected_oos_positive_excess_ratio
            ),
            "median_selected_oos_excess_return": str(
                result.overfitting_audit.median_selected_oos_excess_return
            ),
            "unique_selected_candidate_count": (
                result.overfitting_audit.unique_selected_candidate_count
            ),
            "most_selected_candidate_id": (
                result.overfitting_audit.most_selected_candidate_id
            ),
            "most_selected_candidate_count": (
                result.overfitting_audit.most_selected_candidate_count
            ),
            "criteria": dict(result.overfitting_audit.criteria),
            "passed": result.overfitting_audit.passed,
        },
        "locked_test_cost_stress": {
            "costs_bps": ["0", "5", "10", "20"],
            "selected_candidate_id": result.selected_candidate_id,
            "test_start": result.test_start.isoformat(),
            "test_end": result.test_end.isoformat(),
            "signal_count": result.test_signal_count,
            "candidate_reselected": False,
            "cost_rows_used_for_selection": False,
            "rows": {
                str(cost_bps): {
                    "strategy_metrics": _performance_metrics_payload(
                        row.strategy_metrics
                    ),
                    "benchmark_metrics": _performance_metrics_payload(
                        row.benchmark_metrics
                    ),
                }
                for cost_bps, row in result.locked_test_cost_stress.items()
            },
            "criteria": dict(result.locked_test_cost_stress_criteria),
            "passed": result.locked_test_cost_stress_passed,
        },
        "locked_test_sector_stress": {
            "selected_candidate_id": result.selected_candidate_id,
            "test_start": result.test_start.isoformat(),
            "test_end": result.test_end.isoformat(),
            "candidate_reselected": False,
            "sector_rows_used_for_selection": False,
            "sector_only": [
                {
                    "sector_code": row.group_id,
                    "sector_name": row.group_name,
                    "observation_count": row.observation_count,
                    "signal_count": row.signal_count,
                    "mean_signal_forward_return": str(
                        row.mean_signal_forward_return
                    ),
                    "signal_hit_rate": str(row.signal_hit_rate),
                    "strategy_metrics": _performance_metrics_payload(
                        row.strategy_metrics
                    ),
                    "benchmark_metrics": _performance_metrics_payload(
                        row.benchmark_metrics
                    ),
                }
                for row in result.locked_test_sectors
            ],
            "leave_one_sector_out": [
                {
                    "excluded_sector_code": row.group_id,
                    "excluded_sector_name": row.group_name,
                    "observation_count": row.observation_count,
                    "signal_count": row.signal_count,
                    "strategy_metrics": _performance_metrics_payload(
                        row.strategy_metrics
                    ),
                    "benchmark_metrics": _performance_metrics_payload(
                        row.benchmark_metrics
                    ),
                }
                for row in result.locked_test_sector_exclusions
            ],
            "positive_sector_count": sum(
                row.strategy_metrics.total_return > 0
                for row in result.locked_test_sectors
            ),
            "positive_sector_ratio": str(
                Decimal(
                    sum(
                        row.strategy_metrics.total_return > 0
                        for row in result.locked_test_sectors
                    )
                )
                / len(result.locked_test_sectors)
            ),
            "positive_exclusion_count": sum(
                row.strategy_metrics.total_return > 0
                for row in result.locked_test_sector_exclusions
            ),
            "positive_exclusion_ratio": str(
                Decimal(
                    sum(
                        row.strategy_metrics.total_return > 0
                        for row in result.locked_test_sector_exclusions
                    )
                )
                / len(result.locked_test_sector_exclusions)
            ),
            "criteria": dict(result.locked_test_sector_stress_criteria),
            "passed": result.locked_test_sector_stress_passed,
        },
        "locked_test_symbol_stress": {
            "selected_candidate_id": result.selected_candidate_id,
            "test_start": result.test_start.isoformat(),
            "test_end": result.test_end.isoformat(),
            "candidate_reselected": False,
            "symbol_rows_used_for_selection": False,
            "symbols": [
                {
                    "symbol": row.symbol,
                    "sector_code": row.sector_code,
                    "sector_name": row.sector_name,
                    "observation_count": row.observation_count,
                    "signal_count": row.signal_count,
                    "strategy_metrics": _performance_metrics_payload(
                        row.strategy_metrics
                    ),
                    "benchmark_metrics": _performance_metrics_payload(
                        row.benchmark_metrics
                    ),
                }
                for row in result.locked_test_symbols
            ],
            "positive_symbol_count": sum(
                row.strategy_metrics.total_return > 0
                for row in result.locked_test_symbols
            ),
            "positive_symbol_ratio": str(
                result.locked_test_positive_symbol_ratio
            ),
            "median_symbol_total_return": str(
                result.locked_test_median_symbol_total_return
            ),
            "top_five_absolute_return_share": str(
                result.locked_test_top_five_absolute_return_share
            ),
            "top_five_absolute_return_symbols": [
                row.symbol
                for row in sorted(
                    result.locked_test_symbols,
                    key=lambda item: (
                        -abs(item.strategy_metrics.total_return),
                        item.symbol,
                    ),
                )[:5]
            ],
            "criteria": dict(result.locked_test_symbol_stress_criteria),
            "passed": result.locked_test_symbol_stress_passed,
        },
        "locked_test_regime_stress": {
            "selected_candidate_id": result.selected_candidate_id,
            "test_start": result.test_start.isoformat(),
            "test_end": result.test_end.isoformat(),
            "candidate_reselected": False,
            "regime_rows_used_for_selection": False,
            "regime_definition": (
                "PREVIOUS_SESSION_EQUAL_WEIGHT_UNIVERSE_GROSS_RETURN_SIGN"
            ),
            "regime_result_known_at": "CURRENT_SESSION_09_35",
            "current_signal_known_at": "CURRENT_SESSION_10_00",
            "regimes": [
                {
                    "regime_id": row.regime_id,
                    "regime_name": row.regime_name,
                    "session_count": row.session_count,
                    "observation_count": row.observation_count,
                    "signal_count": row.signal_count,
                    "previous_session_mean_universe_return_minimum": str(
                        row.previous_session_mean_universe_return_minimum
                    ),
                    "previous_session_mean_universe_return_maximum": str(
                        row.previous_session_mean_universe_return_maximum
                    ),
                    "known_before_signal": row.known_before_signal,
                    "strategy_metrics": _performance_metrics_payload(
                        row.strategy_metrics
                    ),
                    "benchmark_metrics": _performance_metrics_payload(
                        row.benchmark_metrics
                    ),
                }
                for row in result.locked_test_regimes
            ],
            "criteria": dict(result.locked_test_regime_stress_criteria),
            "passed": result.locked_test_regime_stress_passed,
        },
        "locked_test_placebo_audit": {
            "selected_candidate_id": result.selected_candidate_id,
            "test_start": result.test_start.isoformat(),
            "test_end": result.test_end.isoformat(),
            "candidate_reselected": False,
            "placebo_rows_used_for_selection": False,
            "method": result.locked_test_placebo_audit.method,
            "replications": result.locked_test_placebo_audit.replications,
            "seed": result.locked_test_placebo_audit.seed,
            "preserves_daily_signal_counts": (
                result.locked_test_placebo_audit.preserves_daily_signal_counts
            ),
            "observed_mean_session_conditional_excess_return": str(
                result.locked_test_placebo_audit
                .observed_mean_session_conditional_excess_return
            ),
            "placebo_mean_session_conditional_excess_return": str(
                result.locked_test_placebo_audit
                .placebo_mean_session_conditional_excess_return
            ),
            "placebo_median_session_conditional_excess_return": str(
                result.locked_test_placebo_audit
                .placebo_median_session_conditional_excess_return
            ),
            "placebo_p95_session_conditional_excess_return": str(
                result.locked_test_placebo_audit
                .placebo_p95_session_conditional_excess_return
            ),
            "placebo_at_least_observed_count": (
                result.locked_test_placebo_audit
                .placebo_at_least_observed_count
            ),
            "empirical_one_sided_p_value": str(
                result.locked_test_placebo_audit.empirical_one_sided_p_value
            ),
            "criteria": dict(result.locked_test_placebo_audit.criteria),
            "passed": result.locked_test_placebo_audit.passed,
        },
        "locked_test_execution_delay_stress": {
            "selected_candidate_id": result.selected_candidate_id,
            "test_start": result.test_start.isoformat(),
            "test_end": result.test_end.isoformat(),
            "cost_bps": str(payload.cost_bps),
            "candidate_reselected": False,
            "delay_rows_used_for_selection": False,
            "signal_known_at": "CURRENT_SESSION_10_00",
            "source_bar_timestamp_convention": "BAR_END_TIME",
            "rows": [
                {
                    "delay_bars_after_signal": row.delay_bars_after_signal,
                    "entry_source_bar_time": row.entry_source_bar_time.strftime(
                        "%H:%M"
                    ),
                    "signal_count": row.signal_count,
                    "signal_session_count": row.signal_session_count,
                    "mean_session_conditional_excess_return": str(
                        row.mean_session_conditional_excess_return
                    ),
                    "strategy_metrics": _performance_metrics_payload(
                        row.strategy_metrics
                    ),
                    "benchmark_metrics": _performance_metrics_payload(
                        row.benchmark_metrics
                    ),
                }
                for row in result.locked_test_execution_delay_rows
            ],
            "criteria": dict(
                result.locked_test_execution_delay_stress_criteria
            ),
            "passed": result.locked_test_execution_delay_stress_passed,
        },
        "locked_test_liquidity_capacity_stress": {
            "selected_candidate_id": result.selected_candidate_id,
            "test_start": result.test_start.isoformat(),
            "test_end": result.test_end.isoformat(),
            "candidate_reselected": False,
            "capacity_rows_used_for_selection": False,
            "volume_source_bar_time": "10:00",
            "volume_source_interval": "09:55-10:00",
            "volume_source_bar_completed_before_entry": True,
            "volume_unit": "SHARES",
            "order_lot_size_shares": 100,
            "source_amount_available": False,
            "qfq_price_volume_used_as_actual_notional": False,
            "rmb_notional_capacity_status": (
                "UNAVAILABLE_QFQ_OHLCV_WITHOUT_PROVIDER_AMOUNT"
            ),
            "rows": [
                {
                    "participation_rate": str(row.participation_rate),
                    "signal_count": row.signal_count,
                    "signal_session_count": row.signal_session_count,
                    "minimum_round_lot_share_capacity": (
                        row.minimum_round_lot_share_capacity
                    ),
                    "lower_median_round_lot_share_capacity": (
                        row.lower_median_round_lot_share_capacity
                    ),
                    "maximum_round_lot_share_capacity": (
                        row.maximum_round_lot_share_capacity
                    ),
                    "zero_capacity_signal_count": (
                        row.zero_capacity_signal_count
                    ),
                    "one_lot_eligible_signal_count": (
                        row.one_lot_eligible_signal_count
                    ),
                    "one_lot_eligible_signal_ratio": str(
                        row.one_lot_eligible_signal_ratio
                    ),
                }
                for row in result.locked_test_liquidity_capacity_rows
            ],
            "criteria": dict(
                result.locked_test_liquidity_capacity_stress_criteria
            ),
            "passed": (
                result.locked_test_liquidity_capacity_stress_passed
            ),
        },
        "discovery_gate": {
            "criteria": {
                "chronological_locked_test_passed": result.test_gate_passed,
                "selection_bias_audit_passed": (
                    result.overfitting_audit.passed
                ),
                "locked_test_cost_stress_passed": (
                    result.locked_test_cost_stress_passed
                ),
                "locked_test_sector_stress_passed": (
                    result.locked_test_sector_stress_passed
                ),
                "locked_test_symbol_stress_passed": (
                    result.locked_test_symbol_stress_passed
                ),
                "locked_test_regime_stress_passed": (
                    result.locked_test_regime_stress_passed
                ),
                "locked_test_placebo_audit_passed": (
                    result.locked_test_placebo_audit.passed
                ),
                "locked_test_execution_delay_stress_passed": (
                    result.locked_test_execution_delay_stress_passed
                ),
                "locked_test_liquidity_capacity_stress_passed": (
                    result.locked_test_liquidity_capacity_stress_passed
                ),
            },
            "passed": all(
                (
                    result.test_gate_passed,
                    result.overfitting_audit.passed,
                    result.locked_test_cost_stress_passed,
                    result.locked_test_sector_stress_passed,
                    result.locked_test_symbol_stress_passed,
                    result.locked_test_regime_stress_passed,
                    result.locked_test_placebo_audit.passed,
                    result.locked_test_execution_delay_stress_passed,
                    result.locked_test_liquidity_capacity_stress_passed,
                )
            ),
            "promotion_authority": False,
        },
        "test_validation": {
            "observation_count": result.test_observation_count,
            "signal_count": result.test_signal_count,
            "signal_session_count": result.test_signal_session_count,
            "mean_session_conditional_excess_return": str(
                result.test_mean_session_conditional_excess_return
            ),
            "newey_west_lag": result.test_newey_west_lag,
            "newey_west_standard_error": str(
                result.test_newey_west_standard_error
            ),
            "newey_west_t_statistic": str(
                result.test_newey_west_t_statistic
            ),
            "confidence_interval_95_lower": str(
                result.test_confidence_interval_95_lower
            ),
            "confidence_interval_95_upper": str(
                result.test_confidence_interval_95_upper
            ),
            "strategy_metrics": _performance_metrics_payload(
                result.test_strategy_metrics
            ),
            "benchmark_metrics": _performance_metrics_payload(
                result.test_benchmark_metrics
            ),
            "criteria": dict(result.test_gate_criteria),
            "passed": result.test_gate_passed,
        },
        "data_version": dataset["data_version"],
        "dataset_checksum": dataset["checksum"],
        "data_interval": "5m",
        "selected_trading_days": required_trading_days[:-1],
        "exit_completion_day": required_trading_days[-1],
        "source_bar_count": source_bar_count,
        "symbol_count": symbol_count,
        "sector_count": sector_count,
        "point_in_time": False,
        "future_adjustment_knowledge_possible": True,
        "realtime_readiness": False,
        "independent_confirmation": False,
        "promotion": result.promotion,
        "promotion_reasons": [
            "CURRENT_UNIVERSE_NON_PIT",
            "QFQ_MAY_CONTAIN_FUTURE_ADJUSTMENT_KNOWLEDGE",
            "DISCOVERY_AND_TEST_WINDOW_ALREADY_VIEWED",
            "NO_AUTHORIZED_FORWARD_PAPER_EVIDENCE",
            "NO_AUTHORIZED_REALTIME_OR_L2_PROVIDER",
        ],
        "paper_readiness": "NOT_ELIGIBLE",
        "live_readiness": "DISABLED_REQUIRES_SEPARATE_AUTHORIZATION",
        "state_mutation": result.state_mutation,
        "external_order_transmission": result.external_order_transmission,
    }
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        **unsigned,
        "preview_hash": sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _intraday_factor_preview(
    *,
    payload: IntradayFactorBacktestRequest,
    result: IntradayFactorResearchResult,
    dataset: dict[str, object],
    required_trading_days: list[str],
    source_bar_count: int,
) -> dict[str, object]:
    criteria = dict(result.research_gate.criteria)
    validation_keys: tuple[str, ...] = (
        "minimum_observation_count",
        "minimum_signal_count",
        "positive_mean_signal_return",
        "positive_conditional_excess_return",
        "minimum_ten_clustered_signal_sessions",
        "newey_west_t_statistic_above_1_96",
        "temporal_stability_passed",
        "positive_rank_ic",
        "positive_sector_ratio_at_least_60pct",
    )
    if result.exit_factor_validation is not None:
        validation_keys = (
            *validation_keys,
            "exit_factor_effectiveness_passed",
        )
    factor_validation_passed = all(criteria[key] for key in validation_keys)
    correlation_passed = criteria[
        "maximum_absolute_factor_correlation_below_0_90"
    ]
    robustness_passed = result.robustness_validation.passed
    backtest_passed = (
        criteria["base_cost_total_return_positive"]
        and criteria["base_cost_excess_return_positive"]
    )
    required_research_stages: list[dict[str, object]] = [
        {
            "stage_id": "FACTOR_VALIDATION",
            "passed": factor_validation_passed,
        },
        {
            "stage_id": "CORRELATION_VALIDATION",
            "passed": correlation_passed,
        },
        {
            "stage_id": "ROBUSTNESS_VALIDATION",
            "passed": robustness_passed,
        },
    ]
    passed_research_stage_count = sum(
        stage["passed"] is True for stage in required_research_stages
    )
    all_research_stages_passed = (
        passed_research_stage_count == len(required_research_stages)
    )
    first_blocking_stage = next(
        str(stage["stage_id"])
        for stage in required_research_stages
        if stage["passed"] is False
    ) if not all_research_stages_passed else (
        "CAUSAL_BACKTEST" if not backtest_passed else "STRATEGY_REGISTRATION"
    )
    sequential_advancement: dict[str, object] = {
        "classification": (
            "READ_ONLY_SEQUENTIAL_FACTOR_TO_STRATEGY_ADVANCEMENT"
        ),
        "required_research_stages": required_research_stages,
        "passed_research_stage_count": passed_research_stage_count,
        "required_research_stage_count": len(required_research_stages),
        "all_research_stages_passed": all_research_stages_passed,
        "first_blocking_stage": first_blocking_stage,
        "strategy_construction_status": (
            "RESEARCH_ADMITTED_READ_ONLY_PREVIEW"
            if all_research_stages_passed
            else "DIAGNOSTIC_PREVIEW_ONLY"
        ),
        "strategy_preview_generated": True,
        "causal_backtest_diagnostic_completed": True,
        "causal_backtest_passed": backtest_passed,
        "strategy_registration_allowed": False,
        "historical_result_can_authorize_paper": False,
        "viewed_window_reselection_allowed": False,
        "next_allowed_action": (
            "PREDECLARE_NEW_HYPOTHESIS_BEFORE_NEW_EVIDENCE"
            if not all_research_stages_passed or not backtest_passed
            else "REGISTER_IMMUTABLE_STRATEGY_THEN_AUTHORIZED_FORWARD_SHADOW"
        ),
        "state_mutation": False,
        "external_order_transmission": False,
    }
    paper_admission_gates: list[dict[str, object]] = [
        {
            "gate_id": "verified_historical_dataset_identity_bound",
            "passed": True,
            "evidence": f"{dataset['data_version']} · {dataset['checksum']}",
        },
        {
            "gate_id": "research_gate_passed",
            "passed": result.research_gate.passed,
            "evidence": (
                f"research_gate.passed={str(result.research_gate.passed).lower()}"
            ),
        },
        {
            "gate_id": "causal_backtest_passed",
            "passed": backtest_passed,
            "evidence": f"backtest.passed={str(backtest_passed).lower()}",
        },
        {
            "gate_id": "registered_immutable_strategy_version_available",
            "passed": False,
            "evidence": "AD_HOC_UNREGISTERED",
        },
        {
            "gate_id": "unseen_forward_shadow_evidence_attached",
            "passed": False,
            "evidence": "NO_AUTHORIZED_FORWARD_PAPER_EVIDENCE",
        },
        {
            "gate_id": "authorized_realtime_provider_ready",
            "passed": False,
            "evidence": "NO_AUTHORIZED_REALTIME_PROVIDER",
        },
        {
            "gate_id": "fresh_runtime_readiness_attestation_available",
            "passed": False,
            "evidence": "NO_RUNTIME_ATTESTATION",
        },
        {
            "gate_id": "reconciliation_ready",
            "passed": False,
            "evidence": "NO_BOUND_PAPER_RUNTIME",
        },
    ]
    paper_admission_passed_gate_count = sum(
        gate["passed"] is True for gate in paper_admission_gates
    )
    factor_metrics = result.factor_metrics
    event = result.event_summary
    workflow = [
        {
            "id": "FACTOR_DEFINITION",
            "status": "AVAILABLE",
            "detail": "Exact expression and data contract are fixed for this run.",
        },
        {
            "id": "FACTOR_VALIDATION",
            "status": "PASSED" if factor_validation_passed else "FAILED",
            "detail": (
                "Cross-sectional IC, session-clustered Newey-West and four "
                "contiguous-block stability tests completed."
                if result.exit_factor_validation is None
                else "Entry-factor tests and triggered-exit timing-advantage "
                "Newey-West tests completed."
            ),
        },
        {
            "id": "CORRELATION_VALIDATION",
            "status": "PASSED" if correlation_passed else "FAILED",
            "detail": "Redundancy checks against related opening features completed.",
        },
        {
            "id": "ROBUSTNESS_VALIDATION",
            "status": "PASSED" if robustness_passed else "FAILED",
            "detail": (
                "Expanding-window threshold selection and disjoint test folds "
                "completed without using future test returns."
            ),
        },
        {
            "id": "STRATEGY_CONSTRUCTION",
            "status": (
                "RESEARCH_ADMITTED_PREVIEW"
                if all_research_stages_passed
                else "DIAGNOSTIC_ONLY"
            ),
            "detail": (
                "Entry, exit, sizing, cost and benchmark rules are explicit, "
                "but strategy construction is not admitted because at least "
                "one prerequisite research stage failed."
                if not all_research_stages_passed
                else "All prerequisite research stages passed; the strategy "
                "remains a read-only preview pending causal backtest and "
                "immutable registration."
            ),
        },
        {
            "id": "CAUSAL_BACKTEST",
            "status": "PASSED" if backtest_passed else "FAILED",
            "detail": "75-stock and 15-sector causal replay completed.",
        },
        {
            "id": "PAPER",
            "status": "NOT_ELIGIBLE",
            "detail": "No authorized forward realtime observation is attached.",
        },
        {
            "id": "LIVE",
            "status": "DISABLED",
            "detail": "Separate explicit user authorization is required.",
        },
    ]
    parameters: dict[str, object] = {
        "factor": payload.factor,
        "start": payload.start.isoformat(),
        "end": payload.end.isoformat(),
        "cost_bps": str(payload.cost_bps),
        "minimum_body_return_bps": str(payload.minimum_body_return_bps),
        "directions": list(payload.directions),
        "combination": payload.combination,
    }
    strategy: dict[str, object] = {
        "entry_factor_id": result.factor_id,
        "entry_condition": result.factor_expression,
        "entry_minimum_body_return_bps": str(
            payload.minimum_body_return_bps
        ),
        "entry_directions": list(payload.directions),
        "entry_combination": payload.combination,
        "entry_rule": "NEXT_5M_OPEN_10_05",
        "exit_rule": "NEXT_SESSION_OPEN_T_PLUS_ONE",
        "exit_condition_type": "TIME_EXIT",
        "entry_price_source": (
            "OPEN_OF_BAR_LABELLED_10_05_INTERVAL_START_10_00"
        ),
        "exit_price_source": (
            "OPEN_OF_NEXT_SESSION_BAR_LABELLED_09_35_INTERVAL_START_09_30"
        ),
        "position_rule": "EQUAL_WEIGHT_TRIGGERED_STOCKS_ELSE_CASH",
        "benchmark_rule": "EQUAL_WEIGHT_ALL_ELIGIBLE_STOCKS_SAME_HORIZON",
        "single_side_cost_bps": str(payload.cost_bps),
        "t_plus_one_compliant": True,
    }
    if payload.exit_rule != "NEXT_SESSION_OPEN":
        parameters.update(
            {
                "exit_rule": payload.exit_rule,
                "exit_minimum_body_return_bps": str(
                    payload.exit_minimum_body_return_bps
                ),
            }
        )
        conditional_exit_count = sum(
            item.signal and item.exit_condition_triggered
            for item in result.observations
        )
        signal_count = sum(item.signal for item in result.observations)
        strategy = {
            "entry_factor_id": result.factor_id,
            "entry_condition": result.factor_expression,
            "entry_minimum_body_return_bps": str(
                payload.minimum_body_return_bps
            ),
            "entry_directions": list(payload.directions),
            "entry_combination": payload.combination,
            "entry_rule": "NEXT_5M_OPEN_10_05",
            "exit_rule": payload.exit_rule,
            "exit_condition_type": "FACTOR_THEN_TIME_STOP",
            "exit_factor_id": "next-session-three-ten-minute-bearish-v1",
            "exit_condition": (
                "bearish_10m_0930_0940 AND bearish_10m_0940_0950 "
                "AND bearish_10m_0950_1000"
            ),
            "exit_minimum_body_return_bps": str(
                payload.exit_minimum_body_return_bps
            ),
            "exit_condition_known_at": (
                "NEXT_SESSION_10_00_BAR_CLOSE_T_PLUS_ONE"
            ),
            "entry_price_source": (
                "OPEN_OF_BAR_LABELLED_10_05_INTERVAL_START_10_00"
            ),
            "conditional_exit_price_source": (
                "OPEN_OF_NEXT_SESSION_BAR_LABELLED_10_05_"
                "INTERVAL_START_10_00"
            ),
            "fallback_exit_price_source": (
                "OPEN_OF_NEXT_SESSION_BAR_LABELLED_15_00_"
                "INTERVAL_START_14_55"
            ),
            "conditional_exit_count": conditional_exit_count,
            "fallback_time_stop_count": signal_count - conditional_exit_count,
            "position_rule": "EQUAL_WEIGHT_TRIGGERED_STOCKS_ELSE_CASH",
            "benchmark_rule": (
                "EQUAL_WEIGHT_ALL_ELIGIBLE_STOCKS_SAME_PER_SYMBOL_EXIT_RULE"
            ),
            "single_side_cost_bps": str(payload.cost_bps),
            "t_plus_one_compliant": True,
        }
    strategy_spec = {
        key: value
        for key, value in strategy.items()
        if key not in {"conditional_exit_count", "fallback_time_stop_count"}
    }
    strategy_spec_hash = _canonical_dict_sha256(strategy_spec)
    strategy_identity: dict[str, object] = {
        "classification": "AD_HOC_READ_ONLY_STRATEGY_IDENTITY",
        "strategy_id": f"adhoc-intraday-{strategy_spec_hash[:16]}",
        "spec_hash": strategy_spec_hash,
        "spec_hash_scope": "strategy_without_observed_exit_counts",
        "registration_status": "AD_HOC_UNREGISTERED",
        "immutable_within_response": True,
        "paper_eligible": False,
        "promotion_authority": False,
        "state_mutation": False,
        "external_order_transmission": False,
    }
    next_hypothesis_preregistration: dict[str, object] = {
        "classification": (
            "READ_ONLY_NEXT_HYPOTHESIS_PREREGISTRATION_PREFLIGHT"
        ),
        "status": (
            "REQUIRED_NOT_CREATED"
            if sequential_advancement["next_allowed_action"]
            == "PREDECLARE_NEW_HYPOTHESIS_BEFORE_NEW_EVIDENCE"
            else "NOT_REQUIRED_CURRENT_RESULT_RESEARCH_ADMITTED"
        ),
        "current_result_binding": {
            "strategy_id": strategy_identity["strategy_id"],
            "strategy_spec_hash": strategy_spec_hash,
            "data_version": dataset["data_version"],
            "dataset_checksum": dataset["checksum"],
            "viewed_evidence_period": {
                "start": parameters["start"],
                "end": parameters["end"],
            },
            "first_blocking_stage": sequential_advancement[
                "first_blocking_stage"
            ],
            "next_allowed_action": sequential_advancement[
                "next_allowed_action"
            ],
        },
        "current_result_may_seed_motivation": True,
        "current_result_may_seed_new_parameters": False,
        "required_hypothesis_fields": [
            "hypothesis_id",
            "motivation",
            "economic_intuition",
            "expected_regimes",
            "expected_failure_modes",
            "required_data",
            "evaluation_criteria",
        ],
        "required_experiment_lock_fields": [
            "experiment_id",
            "strategy_id",
            "git_commit",
            "data_version",
            "universe",
            "factor_set",
            "label",
            "split",
            "model",
            "parameters",
            "cost_model_id",
            "transaction_cost",
            "seed",
        ],
        "required_governance_gates": [
            {
                "gate_id": "new_hypothesis_identity",
                "required": True,
                "requirement": "Use a hypothesis_id not evaluated before.",
            },
            {
                "gate_id": (
                    "new_evidence_period_disjoint_from_viewed_and_"
                    "prior_test_periods"
                ),
                "required": True,
                "requirement": (
                    "Use a new evidence period disjoint from this viewed "
                    "window and prior held-out test periods."
                ),
            },
            {
                "gate_id": "independent_leakage_review_pass",
                "required": True,
                "requirement": "Attach an independent leakage review PASS.",
            },
            {
                "gate_id": "fixed_candidate_budget",
                "required": True,
                "requirement": "Fix the candidate budget before data access.",
            },
            {
                "gate_id": "fixed_robustness_scenarios",
                "required": True,
                "requirement": (
                    "Fix bounded robustness scenarios before data access."
                ),
            },
            {
                "gate_id": "fixed_cost_model",
                "required": True,
                "requirement": "Fix the cost model before data access.",
            },
            {
                "gate_id": "immutable_spec_and_hash_before_data_access",
                "required": True,
                "requirement": (
                    "Freeze the experiment specification and hash before "
                    "new evidence is read."
                ),
            },
            {
                "gate_id": "contamination_disclosure",
                "required": True,
                "requirement": (
                    "Disclose all prior evidence used to motivate the new "
                    "hypothesis."
                ),
            },
        ],
        "evidence_window_contract": {
            "must_be_new_and_disjoint": True,
            "current_viewed_period_excluded_from_new_confirmation": True,
            "prior_test_period_reuse_for_candidate_selection_allowed": False,
        },
        "strategy_registry_bridge": {
            "preregistration_is_registry_insertion": False,
            "registry_write_performed": False,
            "earliest_status_after_completed_preregistration_and_leakage_review": (
                "IDEA"
            ),
            "legal_lifecycle": [
                "IDEA",
                "EXPERIMENT",
                "CANDIDATE",
                "VALIDATED",
                "PAPER",
                "SHADOW",
                "APPROVED",
                "CHAMPION",
            ],
            "human_authorization_required_for": ["APPROVED", "CHAMPION"],
            "current_lifecycle_status": "NOT_REGISTERED",
        },
        "preregistration_created": False,
        "state_mutation": False,
        "registry_write": False,
        "paper_runtime_start": False,
        "live_runtime_start": False,
        "external_order_transmission": False,
    }
    strategy_registration_gates: list[dict[str, object]] = [
        {
            "gate_id": "all_ordered_research_stages_passed",
            "passed": all_research_stages_passed,
            "evidence": (
                f"{passed_research_stage_count}/"
                f"{len(required_research_stages)}"
            ),
        },
        {
            "gate_id": "causal_backtest_passed",
            "passed": backtest_passed,
            "evidence": f"backtest.passed={str(backtest_passed).lower()}",
        },
        {
            "gate_id": "signed_hypothesis_preregistration_attached",
            "passed": False,
            "evidence": "NOT_ATTACHED",
        },
        {
            "gate_id": "independent_leakage_review_passed",
            "passed": False,
            "evidence": "NOT_ATTACHED",
        },
        {
            "gate_id": "behavior_spec_hash_bound",
            "passed": True,
            "evidence": strategy_spec_hash,
        },
        {
            "gate_id": "complete_registry_spec_and_hash_bound",
            "passed": False,
            "evidence": "NOT_COMPUTABLE_REQUIRED_FIELDS_MISSING",
        },
        {
            "gate_id": "dataset_identity_bound",
            "passed": True,
            "evidence": f"{dataset['data_version']} · {dataset['checksum']}",
        },
        {
            "gate_id": "disjoint_time_split_frozen",
            "passed": False,
            "evidence": "NOT_FROZEN",
        },
        {
            "gate_id": "lineage_root_or_parent_binding_declared",
            "passed": False,
            "evidence": "NOT_DECLARED",
        },
    ]
    strategy_registration_preflight: dict[str, object] = {
        "classification": "READ_ONLY_STRATEGY_REGISTRY_ADMISSION_PREFLIGHT",
        "decision": "BLOCKED",
        "current_candidate": {
            "strategy_id": strategy_identity["strategy_id"],
            "behavior_spec_hash": strategy_spec_hash,
            "behavior_spec_hash_is_complete_registry_spec_hash": False,
            "registration_status": "AD_HOC_UNREGISTERED",
            "registry_spec_hash_status": (
                "NOT_COMPUTABLE_REQUIRED_FIELDS_MISSING"
            ),
        },
        "required_strategy_spec_fields": [
            "strategy_id",
            "name",
            "family",
            "version",
            "parent_strategy_id",
            "git_commit",
            "data_version",
            "universe",
            "factor_set",
            "model",
            "portfolio_rule",
            "parameters",
            "split",
            "created_at",
            "updated_at",
        ],
        "current_field_readiness": {
            "preview_source_available_fields": [
                "strategy_id",
                "data_version",
                "universe",
                "factor_set",
                "portfolio_rule",
                "parameters",
            ],
            "missing_registration_fields": [
                "name",
                "family",
                "version",
                "parent_strategy_id",
                "git_commit",
                "model",
                "split",
                "created_at",
                "updated_at",
            ],
            "available_field_count": 6,
            "required_field_count": 15,
            "complete": False,
        },
        "required_gates": strategy_registration_gates,
        "passed_gate_count": sum(
            gate["passed"] is True for gate in strategy_registration_gates
        ),
        "required_gate_count": len(strategy_registration_gates),
        "all_required_gates_passed": False,
        "earliest_registry_status": "IDEA",
        "idea_to_experiment_transition": {
            "from": "IDEA",
            "to": "EXPERIMENT",
            "allowed": False,
            "required_evidence": [
                "COMPLETED_EXPERIMENT_RUN_SPEC",
                "QUALITY_PASSED_DATA_MANIFEST",
                "INDEPENDENT_LEAKAGE_REVIEW_PASS",
            ],
        },
        "registration_operation_exposed": False,
        "registry_write": False,
        "state_mutation": False,
        "paper_runtime_start": False,
        "live_runtime_start": False,
        "external_order_transmission": False,
    }
    paper_admission_gates[3]["evidence"] = (
        "AD_HOC_UNREGISTERED · "
        f"{strategy_identity['strategy_id']} · {strategy_spec_hash}"
    )
    unsigned: dict[str, object] = {
        "classification": "AD_HOC_INTRADAY_FACTOR_RESEARCH_CURRENT_NON_PIT",
        "factor_definition": {
            "factor_id": result.factor_id,
            "name": result.factor_name,
            "expression": result.factor_expression,
            "source_interval": "NATIVE_5M_AGGREGATED_TO_10M",
            "source_timestamp_convention": "BAR_END_TIME",
            "source_bars": "09:35,09:40,09:45,09:50,09:55,10:00",
            "known_at": "10:00_BAR_CLOSE",
            "minimum_body_return_bps": str(payload.minimum_body_return_bps),
            "directions": list(result.factor_directions),
            "combination": result.factor_combination,
            "allowed_features": [
                "TEN_MINUTE_BODY_0930_0940",
                "TEN_MINUTE_BODY_0940_0950",
                "TEN_MINUTE_BODY_0950_1000",
            ],
            "arbitrary_code_execution": False,
        },
        "parameters": parameters,
        "workflow": workflow,
        "sequential_advancement": sequential_advancement,
        "next_hypothesis_preregistration": (
            next_hypothesis_preregistration
        ),
        "strategy_registration_preflight": (
            strategy_registration_preflight
        ),
        "paper_admission": {
            "classification": "READ_ONLY_INTRADAY_FACTOR_PAPER_ADMISSION",
            "decision": "BLOCKED",
            "required_gates": paper_admission_gates,
            "passed_gate_count": paper_admission_passed_gate_count,
            "required_gate_count": len(paper_admission_gates),
            "all_required_gates_passed": False,
            "next_allowed_stage": sequential_advancement[
                "next_allowed_action"
            ],
            "automatic_start_allowed": False,
            "runtime_mode_change": False,
            "state_mutation": False,
            "external_order_transmission": False,
        },
        "factor_validation": {
            "number_of_periods": factor_metrics.number_of_periods,
            "minimum_instruments": factor_metrics.minimum_instruments,
            "ic": str(factor_metrics.ic),
            "rank_ic": str(factor_metrics.rank_ic),
            "icir": str(factor_metrics.icir),
            "quantile_returns": [str(value) for value in factor_metrics.quantile_returns],
            "factor_turnover": str(factor_metrics.factor_turnover),
            "long_short_spread": str(factor_metrics.long_short_spread),
            "passed": factor_validation_passed,
        },
        "event_summary": {
            "observation_count": event.observation_count,
            "signal_count": event.signal_count,
            "signal_rate": str(event.signal_rate),
            "mean_signal_forward_return": str(event.mean_signal_forward_return),
            "mean_universe_forward_return": str(event.mean_universe_forward_return),
            "conditional_excess_return": str(event.conditional_excess_return),
            "signal_hit_rate": str(event.signal_hit_rate),
            "positive_sector_count": event.positive_sector_count,
            "sector_count": event.sector_count,
            "clustered_signal_session_count": (
                event.clustered_signal_session_count
            ),
            "mean_session_conditional_excess_return": str(
                event.mean_session_conditional_excess_return
            ),
            "newey_west_lag": event.newey_west_lag,
            "newey_west_standard_error": str(event.newey_west_standard_error),
            "newey_west_t_statistic": str(event.newey_west_t_statistic),
            "confidence_interval_95_lower": str(
                event.confidence_interval_95_lower
            ),
            "confidence_interval_95_upper": str(
                event.confidence_interval_95_upper
            ),
        },
        "stability_validation": {
            "classification": result.stability_validation.classification,
            "blocks": [
                {
                    "block_id": block.block_id,
                    "start": block.start.isoformat(),
                    "end": block.end.isoformat(),
                    "session_count": block.session_count,
                    "observation_count": block.observation_count,
                    "signal_count": block.signal_count,
                    "signal_session_count": block.signal_session_count,
                    "mean_session_conditional_excess_return": str(
                        block.mean_session_conditional_excess_return
                    ),
                }
                for block in result.stability_validation.blocks
            ],
            "block_count": result.stability_validation.block_count,
            "positive_block_count": (
                result.stability_validation.positive_block_count
            ),
            "worst_block_mean_conditional_excess_return": str(
                result.stability_validation.worst_block_mean_conditional_excess_return
            ),
            "maximum_absolute_block_contribution_share": str(
                result.stability_validation.maximum_absolute_block_contribution_share
            ),
            "contribution_share_threshold": str(
                result.stability_validation.contribution_share_threshold
            ),
            "criteria": dict(result.stability_validation.criteria),
            "passed": result.stability_validation.passed,
        },
        "correlation_validation": {
            "correlations": {
                key: str(value) for key, value in result.correlations.items()
            },
            "maximum_absolute_correlation": "0.90",
            "passed": correlation_passed,
        },
        "robustness_validation": {
            "classification": result.robustness_validation.classification,
            "candidate_thresholds_bps": [
                str(value)
                for value in result.robustness_validation.candidate_thresholds_bps
            ],
            "initial_training_session_count": (
                result.robustness_validation.initial_training_session_count
            ),
            "stitched_test_session_count": (
                result.robustness_validation.stitched_test_session_count
            ),
            "folds": [
                {
                    "fold_id": fold.fold_id,
                    "training_start": fold.training_start.isoformat(),
                    "training_end": fold.training_end.isoformat(),
                    "test_start": fold.test_start.isoformat(),
                    "test_end": fold.test_end.isoformat(),
                    "training_session_count": fold.training_session_count,
                    "test_session_count": fold.test_session_count,
                    "selected_minimum_body_return_bps": str(
                        fold.selected_minimum_body_return_bps
                    ),
                    "selection_known_at": fold.selection_known_at.isoformat(),
                    "training_signal_count": fold.training_signal_count,
                    "test_signal_count": fold.test_signal_count,
                    "training_strategy_metrics": _performance_metrics_payload(
                        fold.training_strategy_metrics
                    ),
                    "test_strategy_metrics": _performance_metrics_payload(
                        fold.test_strategy_metrics
                    ),
                    "test_benchmark_metrics": _performance_metrics_payload(
                        fold.test_benchmark_metrics
                    ),
                }
                for fold in result.robustness_validation.folds
            ],
            "stitched_strategy_metrics": _performance_metrics_payload(
                result.robustness_validation.stitched_strategy_metrics
            ),
            "stitched_benchmark_metrics": _performance_metrics_payload(
                result.robustness_validation.stitched_benchmark_metrics
            ),
            "stitched_curve": [
                {
                    "trading_day": item.trading_day.isoformat(),
                    "signal_count": item.signal_count,
                    "eligible_symbol_count": item.eligible_symbol_count,
                    "strategy_return": str(item.strategy_return),
                    "benchmark_return": str(item.benchmark_return),
                    "strategy_equity": str(item.strategy_equity),
                    "benchmark_equity": str(item.benchmark_equity),
                    "excess_equity": str(item.excess_equity),
                }
                for item in result.robustness_validation.stitched_daily
            ],
            "threshold_sensitivity": [
                {
                    "minimum_body_return_bps": str(
                        score.minimum_body_return_bps
                    ),
                    "signal_count": score.signal_count,
                    "signal_rate": str(score.signal_rate),
                    "strategy_metrics": _performance_metrics_payload(
                        score.strategy_metrics
                    ),
                    "benchmark_metrics": _performance_metrics_payload(
                        score.benchmark_metrics
                    ),
                }
                for score in result.robustness_validation.threshold_sensitivity
            ],
            "criteria": dict(result.robustness_validation.criteria),
            "passed": robustness_passed,
        },
        "strategy": strategy,
        "strategy_identity": strategy_identity,
        "backtest": {
            "strategy_metrics": _performance_metrics_payload(
                result.strategy_metrics
            ),
            "benchmark_metrics": _performance_metrics_payload(
                result.benchmark_metrics
            ),
            "cost_stress": {
                str(cost): {
                    "strategy_metrics": _performance_metrics_payload(
                        stress.strategy_metrics
                    ),
                    "benchmark_metrics": _performance_metrics_payload(
                        stress.benchmark_metrics
                    ),
                }
                for cost, stress in result.cost_stress.items()
            },
            "curve": [
                {
                    "trading_day": item.trading_day.isoformat(),
                    "signal_count": item.signal_count,
                    "eligible_symbol_count": item.eligible_symbol_count,
                    "strategy_return": str(item.strategy_return),
                    "benchmark_return": str(item.benchmark_return),
                    "strategy_equity": str(item.strategy_equity),
                    "benchmark_equity": str(item.benchmark_equity),
                    "excess_equity": str(item.excess_equity),
                }
                for item in result.daily
            ],
            "sectors": [_intraday_group_payload(item) for item in result.sectors],
            "symbols": [_intraday_group_payload(item) for item in result.symbols],
            "passed": backtest_passed,
        },
        "research_gate": {
            "passed": result.research_gate.passed,
            "criteria": criteria,
        },
        "causal_contract": dict(result.causal_contract),
        "l2_factor_readiness": _l2_factor_readiness_payload(),
        "data_version": dataset["data_version"],
        "dataset_checksum": dataset["checksum"],
        "data_interval": "5m",
        "selected_trading_days": required_trading_days[:-1],
        "exit_completion_day": required_trading_days[-1],
        "selected_session_count": result.selected_session_count,
        "source_bar_count": source_bar_count,
        "symbol_count": result.symbol_count,
        "sector_count": result.sector_count,
        "point_in_time": False,
        "future_adjustment_knowledge_possible": True,
        "realtime_readiness": False,
        "promotion": result.promotion,
        "promotion_reasons": list(result.promotion_reasons),
        "paper_readiness": "NOT_ELIGIBLE",
        "live_readiness": "DISABLED_REQUIRES_SEPARATE_AUTHORIZATION",
        "state_mutation": result.state_mutation,
        "external_order_transmission": result.external_order_transmission,
    }
    if result.exit_factor_validation is not None:
        exit_validation = result.exit_factor_validation
        unsigned["exit_factor_validation"] = {
            "classification": exit_validation.classification,
            "advantage_definition": (
                "CONDITIONAL_EXIT_OPEN_DIVIDED_BY_TIME_STOP_OPEN_MINUS_ONE"
            ),
            "entry_signal_count": exit_validation.entry_signal_count,
            "trigger_count": exit_validation.trigger_count,
            "trigger_rate": str(exit_validation.trigger_rate),
            "clustered_trigger_session_count": (
                exit_validation.clustered_trigger_session_count
            ),
            "mean_triggered_timing_advantage": str(
                exit_validation.mean_triggered_timing_advantage
            ),
            "mean_session_triggered_timing_advantage": str(
                exit_validation.mean_session_triggered_timing_advantage
            ),
            "trigger_hit_rate": str(exit_validation.trigger_hit_rate),
            "newey_west_lag": exit_validation.newey_west_lag,
            "newey_west_standard_error": str(
                exit_validation.newey_west_standard_error
            ),
            "newey_west_t_statistic": str(
                exit_validation.newey_west_t_statistic
            ),
            "confidence_interval_95_lower": str(
                exit_validation.confidence_interval_95_lower
            ),
            "confidence_interval_95_upper": str(
                exit_validation.confidence_interval_95_upper
            ),
            "criteria": dict(exit_validation.criteria),
            "passed": exit_validation.passed,
        }
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        **unsigned,
        "preview_hash": sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _l2_factor_readiness_payload() -> dict[str, object]:
    required_gates: list[dict[str, object]] = [
        {
            "gate_id": "authorized_l2_provider_selected",
            "passed": False,
            "reason": "NO_AUTHORIZED_L2_PROVIDER",
        },
        {
            "gate_id": "five_level_schema_certified",
            "passed": False,
            "reason": "PROVIDER_SCHEMA_NOT_CERTIFIED",
        },
        {
            "gate_id": "size_unit_normalized_to_shares",
            "passed": False,
            "reason": "PROVIDER_SIZE_UNIT_NOT_CERTIFIED",
        },
        {
            "gate_id": "event_time_and_sequence_available",
            "passed": False,
            "reason": "PROVIDER_CLOCK_NOT_CERTIFIED",
        },
        {
            "gate_id": "freshness_threshold_certified",
            "passed": False,
            "reason": "PROVIDER_FRESHNESS_NOT_CERTIFIED",
        },
        {
            "gate_id": "recorded_replay_reconciliation_passed",
            "passed": False,
            "reason": "NO_AUTHORIZED_RECORDED_L2_SESSION",
        },
    ]
    return {
        "classification": "READ_ONLY_L2_FACTOR_DATA_CONTRACT",
        "factor_id": "order-book-buying-intensity-v1",
        "formula": (
            "(bid_size_1+...+bid_size_5)/(ask_size_1+...+ask_size_5)"
        ),
        "buying_hypothesis": {
            "condition": "buying_intensity > 1",
            "interpretation": (
                "BUY_SIDE_QUANTITY_EXCEEDS_SELL_SIDE_QUANTITY"
            ),
            "validation_status": "UNTESTED_NO_AUTHORIZED_L2_DATA",
        },
        "sell_condition_status": "NOT_DEFINED_REQUIRES_SEPARATE_RESEARCH",
        "required_data": "AUTHORIZED_L2_FIVE_LEVEL_ORDER_BOOK",
        "normalized_input_contract": {
            "level_count": 5,
            "bid_price_fields": [
                f"bid_price_{level}" for level in range(1, 6)
            ],
            "bid_size_fields": [
                f"bid_size_{level}" for level in range(1, 6)
            ],
            "ask_price_fields": [
                f"ask_price_{level}" for level in range(1, 6)
            ],
            "ask_size_fields": [
                f"ask_size_{level}" for level in range(1, 6)
            ],
            "size_unit": "SHARES",
            "required_identity_fields": [
                "symbol",
                "exchange",
                "provider_event_at",
                "received_at",
                "sequence",
            ],
            "allowed_session": "CONTINUOUS_AUCTION",
        },
        "invalid_snapshot_policy": {
            "missing_or_non_finite_value": "REJECT",
            "negative_size": "REJECT",
            "zero_total_ask_size": "REJECT_FACTOR_UNDEFINED",
            "non_monotonic_or_crossed_book": "REJECT",
            "stale_snapshot": "REJECT",
            "future_snapshot": "REJECT",
        },
        "causal_contract": {
            "decision_clock": "FINALIZED_MINUTE_CORRECTION",
            "snapshot_must_be_known_before_decision": True,
            "same_symbol_snapshot": True,
            "same_provider_snapshot": True,
            "future_book_used": False,
            "freshness_limit": "REQUIRES_PROVIDER_CERTIFICATION",
        },
        "required_gates": required_gates,
        "passed_gate_count": sum(
            gate["passed"] is True for gate in required_gates
        ),
        "required_gate_count": len(required_gates),
        "readiness": "UNCONFIGURED",
        "reason": "NO_AUTHORIZED_L2_PROVIDER",
        "factor_effectiveness_tested": False,
        "correlation_tested": False,
        "strategy_backtested": False,
        "derived_from_ohlcv": False,
        "state_mutation": False,
        "external_order_transmission": False,
    }


def _read_verified_market_parquet_content(
    manifest_path: Path,
    *,
    expected_checksum: str,
    expected_bar_count: int,
) -> bytes | None:
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
        not parquet.schema_arrow.equals(_MARKET_PARQUET_SCHEMA, check_metadata=False)
        or metadata.num_rows != expected_bar_count
        or metadata.num_rows > _MAX_MARKET_PARQUET_ROWS
        or metadata.num_row_groups > _MAX_MARKET_PARQUET_ROW_GROUPS
        or uncompressed_bytes > _MAX_MARKET_PARQUET_UNCOMPRESSED_BYTES
    ):
        return None
    return content


def _open_verified_market_parquet(
    manifest_path: Path,
    *,
    expected_checksum: str,
    expected_bar_count: int,
) -> pq.ParquetFile | None:
    content = _read_verified_market_parquet_content(
        manifest_path,
        expected_checksum=expected_checksum,
        expected_bar_count=expected_bar_count,
    )
    return None if content is None else pq.ParquetFile(pa.BufferReader(content))


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
