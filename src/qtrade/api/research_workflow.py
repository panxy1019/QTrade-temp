"""Authenticated research-only submission; anonymous catalog and immutable results."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from fastapi import APIRouter, HTTPException, Request, Response

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.factor_strategy_catalog import preview_from_catalog, validate_catalog
from qtrade.research.strategy_contract import freeze_strategy, validate_strategy
from qtrade.research.workflow import SCHEMA, archived_template, capabilities, digest, validate_spec
from qtrade.research.workflow_queue import GitQueue, seal

router = APIRouter(prefix="/research")
PUBLIC_ROOT = "https://raw.githubusercontent.com/panxy1019/QTrade-observations/main/"
COOKIE = "qtrade_research_session"
ORIGIN = "https://q-trade-temp.vercel.app"
logger = logging.getLogger(__name__)


def _queue_failure(operation: str, error: Exception) -> None:
    # Exception messages/tracebacks can contain keys or transport credentials.
    logger.warning("research_queue_failure operation=%s error_type=%s",
                   operation, type(error).__name__)


def research_clock() -> str | None:
    try:
        path = Path(__file__).resolve().parents[3] / "configs/observer_calendar_2026.json"
        calendar = Calendar(path)
        return calendar.expected(datetime.now(UTC), "daily").date().isoformat()
    except (ValueError, OSError, KeyError):
        return None


def configured() -> bool:
    return all(os.environ.get(key) for key in (
        "QTRADE_RESEARCH_PASSWORD_HASH", "QTRADE_RESEARCH_SESSION_KEY",
        "QTRADE_RESEARCH_QUEUE_KEY", "QTRADE_RESEARCH_QUEUE_SECRET",
        "QTRADE_RESEARCH_GITHUB_HOST_KEY",
    ))


def _session(user: str, expires: int) -> str:
    key = os.environ.get("QTRADE_RESEARCH_SESSION_KEY", "")
    if len(key) < 32:
        raise HTTPException(503, "研究登录尚未配置")
    payload = base64.urlsafe_b64encode(
        json.dumps({"user": user, "expires": expires}).encode()
    ).decode()
    return payload + "." + hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def actor(request: Request) -> str:
    token = request.cookies.get(COOKIE, "")
    try:
        payload, _signature = token.split(".")
        value = json.loads(base64.urlsafe_b64decode(payload))
        if not hmac.compare_digest(_session(value["user"], value["expires"]), token):
            raise ValueError("INVALID_SESSION")
        if value["expires"] <= time.time() or value["user"] != os.environ.get(
            "QTRADE_RESEARCH_USER", "panxy1019"
        ):
            raise ValueError("EXPIRED_SESSION")
        return str(value["user"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise HTTPException(401, "请先登录研究账户") from None


def _same_origin(request: Request) -> None:
    expected = os.environ.get("QTRADE_RESEARCH_ORIGIN", ORIGIN)
    if request.headers.get("origin") != expected:
        raise HTTPException(403, "研究操作来源不匹配")


async def _body(request: Request, maximum: int = 16000) -> dict[str, Any]:
    _same_origin(request)
    raw = await request.body()
    if len(raw) > maximum or "application/json" not in request.headers.get(
        "content-type", ""
    ):
        raise HTTPException(413, "研究请求格式或大小无效")
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("OBJECT_REQUIRED")
        return value
    except (ValueError, TypeError):
        raise HTTPException(422, "研究请求需要 JSON 对象") from None


def read_public(relative: str, *, maximum: int = 3_000_000) -> dict[str, Any]:
    request = UrlRequest(
        PUBLIC_ROOT + relative + "?v=" + str(int(time.time()) // 15),
        headers={"User-Agent": "QTrade-Research/1"},
    )
    with urlopen(request, timeout=8) as handle:
        raw = handle.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("PUBLICATION_TOO_LARGE")
    value: dict[str, Any] = json.loads(raw)
    if signed({k: v for k, v in value.items() if k != "evidence_sha256"}) != value:
        raise ValueError("PUBLICATION_HASH_MISMATCH")
    for key in ("live_trading_enabled", "external_order_transmission"):
        if value.get(key) is not False:
            raise ValueError("RESEARCH_AUTHORITY_MISMATCH")
    return value


@router.get("/session")
def session(request: Request, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        user: str | None = actor(request)
    except HTTPException:
        user = None
    return {
        "configured": configured(), "authenticated": user is not None, "user": user,
        "scope": "RESEARCH_AND_MODEL_CONTROL", "external_order_transmission": False,
    }


@router.post("/login")
async def login(request: Request, response: Response) -> dict[str, Any]:
    body = await _body(request, 1500)
    if not configured():
        raise HTTPException(503, "研究登录尚未配置")
    user = body.get("user", "")
    password = body.get("password", "")
    valid = isinstance(password, str) and 16 <= len(password) <= 256
    try:
        algorithm, rounds, salt, expected = os.environ[
            "QTRADE_RESEARCH_PASSWORD_HASH"
        ].split("$")
        if algorithm != "pbkdf2_sha256" or not 100_000 <= int(rounds) <= 500_000:
            raise ValueError("INVALID_PASSWORD_CONFIGURATION")
        supplied = hashlib.pbkdf2_hmac(
            "sha256", (password if valid else "invalid").encode(),
            bytes.fromhex(salt), int(rounds),
        ).hex()
        valid = valid and hmac.compare_digest(supplied, expected) and (
            user == os.environ.get("QTRADE_RESEARCH_USER", "panxy1019")
        )
    except (ValueError, KeyError):
        raise HTTPException(503, "研究登录配置无效") from None
    if not valid:
        raise HTTPException(401, "研究账户或密码错误")
    response.set_cookie(
        COOKIE, _session(user, int(time.time()) + 8 * 3600),
        max_age=8 * 3600, secure=True, httponly=True, samesite="strict", path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": True, "user": user, "scope": "RESEARCH_JOBS_ONLY"}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, Any]:
    await _body(request)
    response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="strict", path="/")
    return {"authenticated": False}


@router.get("/catalog")
def catalog(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        value = read_public("workflow.json")
        if value.get("schema_version") != SCHEMA:
            raise ValueError("SCHEMA_MISMATCH")
        heartbeat = datetime.fromisoformat(value["worker"]["heartbeat_at"])
        age = (datetime.now(UTC) - heartbeat).total_seconds()
        value["worker_fresh"] = -60 <= age <= 720
        expected = research_clock()
        value["expected_complete_day"] = expected
        value["frozen_data_current"] = (any(
            r["end"] >= expected
            and r['id'] == value.get('default_spec', {}).get('dataset_id', r['id'])
            for r in value.get("datasets", [])
        ) if expected and value.get("datasets") else None)
        value["publication_available"] = True
        value["checked_at"] = datetime.now(UTC).isoformat()
        return value
    except Exception:
        return {
            **AUTHORITY, "schema_version": SCHEMA, **capabilities(),
            "publication_available": False, "experiments": [],
            "worker_fresh": False,
            "worker": {"state": "UNAVAILABLE"},
        }


@router.get("/experiments/{experiment_id}")
def experiment(experiment_id: str, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}", experiment_id):
        raise HTTPException(422, "实验标识无效")
    try:
        listing = read_public("workflow.json")
        if listing.get("schema_version") != SCHEMA:
            raise ValueError("CATALOG_SCHEMA_MISMATCH")
        row = next(
            r for r in listing["experiments"]
            if r["experiment_id"] == experiment_id and r["state"] == "COMPLETE"
        )
        relative = row["detail_file"]
        if not re.fullmatch(r"workflow-results/[a-f0-9]{64}\.json", relative):
            raise ValueError("INVALID_RESULT_PATH")
        result = read_public(relative)
        if result["evidence_sha256"] != row["detail_sha256"] or (
            result.get("schema_version") != "qtrade.workflow-result.v1"
            or result.get("experiment_id") != experiment_id
            or result.get("engine") != "QLIB" or result.get("state") != "COMPLETE"
        ):
            raise ValueError("RESULT_IDENTITY_MISMATCH")
        return result
    except StopIteration:
        raise HTTPException(404, "未找到已完成实验") from None
    except Exception:
        raise HTTPException(503, "实验发布不可用或校验失败") from None


@router.get("/experiments/{experiment_id}/template")
def template(experiment_id: str, response: Response) -> dict[str, Any]:
    result = experiment(experiment_id, response)
    try:
        listing = read_public("workflow.json")
        if listing.get("schema_version") != SCHEMA:
            raise ValueError("CATALOG_SCHEMA_MISMATCH")
        revision = listing["worker"].get("source_revision")
    except Exception:
        raise HTTPException(503, "当前执行器版本不可核验，配置未载入") from None
    try:
        return archived_template(result, revision)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except (KeyError, TypeError):
        raise HTTPException(422, "归档配置不完整，不能用当前默认值补齐") from None


def bound_definition(spec: dict[str, Any], listing: dict[str, Any]) -> dict[str, Any]:
    """Freeze only published immutable inputs; the cluster independently verifies bytes."""
    try:
        if listing.get("schema_version") != SCHEMA:
            raise ValueError("CATALOG_SCHEMA_MISMATCH")
        heartbeat = datetime.fromisoformat(listing["worker"]["heartbeat_at"])
        age = (datetime.now(UTC)-heartbeat).total_seconds() if heartbeat.tzinfo else None
        if age is None or not -60 <= age <= 720:
            raise ValueError("WORKER_STALE")
        binding = spec["data_binding"]
        matches = [row for row in listing["datasets"]
                   if row.get("data_binding") == binding and row["id"] == spec["dataset_id"]]
        if len(matches) != 1:
            raise HTTPException(422, "所选 READY 数据版本未在当前研究目录发布")
        revision = listing["worker"]["source_revision"]
        # A config-code mismatch between Vercel and the worker must close this
        # entrance; custom parameters may derive a new contract from this base.
        bases = [row for row in listing.get("strategy_versions", [])
                 if row.get("spec", {}).get("data_binding") == binding
                 and row.get('source_revision') == revision]
        if not bases or any(validate_strategy(row) != row or row["source_revision"] != revision
                            for row in bases):
            raise ValueError("PUBLISHED_STRATEGY_BUILDER_MISMATCH")
        return freeze_strategy(spec, revision)
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError):
        raise HTTPException(503, "集群版本规格不可核验，未生成冻结策略") from None


@router.post("/specification")
async def specification(request: Request, response: Response) -> dict[str, Any]:
    actor(request)
    response.headers["Cache-Control"] = "no-store"
    body = await _body(request)
    try:
        if set(body) != {"spec"}:
            raise ValueError("SPEC_FIELDS_MISMATCH")
        spec = validate_spec(body["spec"])
        if "data_binding" not in spec:
            raise ValueError("READY_DATA_BINDING_REQUIRED")
    except (ValueError, TypeError, KeyError):
        raise HTTPException(422, "请先选择不可变 READY 数据与已支持的特征版本") from None
    try:
        listing = read_public("workflow.json")
    except Exception:
        raise HTTPException(503, "研究目录不可用，未生成冻结策略") from None
    definition = bound_definition(spec, listing)
    return signed({**AUTHORITY, "schema_version": "qtrade.qlib-specification-preview.v1",
        "spec": spec, "source_revision": definition["source_revision"],
        "strategy_definition": definition,
        "expected_strategy_version": definition["strategy_version"]})


@router.post("/jobs", status_code=202)
async def submit(request: Request) -> dict[str, Any]:
    user = actor(request)
    body = await _body(request)
    if set(body) not in ({"spec", "request_id"},
                         {"spec", "request_id", "expected_strategy_version"}) or not re.fullmatch(
        r"[a-f0-9]{32}", str(body.get("request_id", ""))
    ):
        raise HTTPException(422, "需要有效的规格与幂等请求标识")
    try:
        spec = validate_spec(body["spec"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, "研究规格不符合已开放的配置合同") from None
    try:
        listing = read_public("workflow.json")
        heartbeat = datetime.fromisoformat(listing["worker"]["heartbeat_at"])
        if not -60 <= (datetime.now(UTC) - heartbeat).total_seconds() <= 720:
            raise ValueError("WORKER_STALE")
        if len([r for r in listing["experiments"] if r["state"] not in {
            "COMPLETE", "FAILED", "CANCELLED", "INTERRUPTED", "REJECTED"
        }]) >= 3:
            raise HTTPException(429, "研究队列已满，请等待当前任务完成")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "集群执行器未就绪，未提交任务") from None
    frozen = None
    if "data_binding" in spec:
        frozen = bound_definition(spec, listing)
        if body.get("expected_strategy_version") != frozen["strategy_version"]:
            raise HTTPException(409, "冻结策略版本已变化，请重新预览后提交")
    elif "expected_strategy_version" in body:
        raise HTTPException(422, "策略版本需要绑定不可变 READY 数据")
    identity = body["request_id"]
    # Deterministic envelope supports safe browser retry after a lost response.
    value = {
        "job_id": identity, "actor": user, "spec": spec, "spec_sha256": digest(spec),
        "kind": "QLIB_RESEARCH_JOB", "live_trading_enabled": False,
    }
    if frozen is not None:
        value["expected_strategy_version"] = frozen["strategy_version"]
    try:
        queue = GitQueue.from_environment()
        queue.append("requests", identity, seal(
            value, os.environ["QTRADE_RESEARCH_QUEUE_SECRET"]
        ))
    except ValueError as error:
        if str(error) == "IMMUTABLE_JOB_ID_CONFLICT":
            raise HTTPException(409, "请求标识已绑定另一份规格") from None
        _queue_failure("submit", error)
        raise HTTPException(503, "研究队列配置无效，未确认提交") from None
    except Exception as error:
        _queue_failure("submit", error)
        raise HTTPException(503, "任务提交未获确认；请使用同一请求标识重试") from None
    return {**AUTHORITY, "job_id": identity, "state": "QUEUED", "spec_sha256": digest(spec),
            **({"strategy_version": frozen["strategy_version"]} if frozen else {})}


@router.post("/jobs/{job_id}/cancel", status_code=202)
async def cancel(job_id: str, request: Request) -> dict[str, Any]:
    user = actor(request)
    await _body(request)
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(422, "任务标识无效")
    try:
        GitQueue.from_environment().append("cancellations", job_id, seal(
            {"job_id": job_id, "actor": user, "kind": "CANCEL_RESEARCH_JOB"},
            os.environ["QTRADE_RESEARCH_QUEUE_SECRET"],
        ))
    except Exception as error:
        _queue_failure("cancel", error)
        raise HTTPException(503, "取消请求未获确认") from None
    return {"job_id": job_id, "state": "CANCEL_REQUESTED"}


def _factor_catalog() -> dict[str, Any]:
    """Only the fixed trusted publication; no client paths or archive downloads."""
    try:
        value = read_public("factor-catalog.json", maximum=1_000_000)
        return validate_catalog(value)
    except Exception:
        raise HTTPException(503, "因子目录未发布或校验失败，登记入口保持关闭") from None


def _factor_worker(catalog: dict[str, Any]) -> dict[str, Any]:
    """Both catalogs must describe the same currently supported worker source."""
    try:
        listing = read_public("workflow.json")
        if listing.get("schema_version") != SCHEMA:
            raise ValueError("WORKFLOW_SCHEMA_MISMATCH")
        now = datetime.now(UTC)
        for worker in (catalog["worker"], listing["worker"]):
            heartbeat = datetime.fromisoformat(worker["heartbeat_at"])
            if heartbeat.tzinfo is None or not -60 <= (now-heartbeat).total_seconds() <= 720:
                raise ValueError("WORKER_STALE")
        if (catalog["worker"]["source_revision"] != listing["worker"]["source_revision"]
                or "FACTOR_STRATEGY_REGISTER_V1" not in catalog["worker"]["supported_kinds"]):
            raise ValueError("FACTOR_WORKER_NOT_SUPPORTED")
        return listing
    except Exception:
        raise HTTPException(503, "因子目录与当前执行器不一致或已过期，请等待重新发布") from None


@router.get("/factor-strategies/catalog")
def factor_catalog(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return _factor_catalog()


@router.get("/factor-strategies/jobs/{job_id}")
def factor_registration(job_id: str, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    if re.fullmatch(r"[a-f0-9]{32}", job_id) is None:
        raise HTTPException(422, "登记任务标识无效")
    try:
        listing = read_public("workflow.json")
        if listing.get("schema_version") != SCHEMA:
            raise ValueError("WORKFLOW_SCHEMA_MISMATCH")
        rows = [row for row in listing["experiments"] if row["experiment_id"] == job_id
                and row["state"] == "COMPLETE" and row.get("engine") == "FACTOR_REGISTRATION"]
        if not rows:
            raise HTTPException(404, "未找到已完成的因子登记回执")
        if len(rows) != 1:
            raise ValueError("AMBIGUOUS_REGISTRATION_RESULT")
        row = rows[0]
        if not re.fullmatch(r"workflow-results/[a-f0-9]{64}\.json", row["detail_file"]):
            raise ValueError("REGISTRATION_RESULT_PATH_INVALID")
        value = read_public(row["detail_file"], maximum=100_000)
        if (value["evidence_sha256"] != row["detail_sha256"] or value["job_id"] != job_id
                or value.get("schema_version") != "qtrade.factor-strategy-registration.v1"
                or value.get("kind") != "FACTOR_STRATEGY_REGISTER_V1"
                or value.get("state") != "COMPLETE" or value.get("registration_only") is not True
                or value.get("status") != "EXPERIMENT" or value.get("role") != "NONE"
                or any(value.get(key) is not False for key in (
                    "backtest_implemented", "paper_admission", "model_activation"))):
            raise ValueError("REGISTRATION_RESULT_IDENTITY_INVALID")
        return value
    except HTTPException:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(503, "登记回执不可用或校验失败") from None


def _factor_preview(spec: Any, catalog: dict[str, Any]) -> dict[str, Any]:
    try:
        return preview_from_catalog(catalog, spec)
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, "因子、评价、数据或组合规格不符合已发布的登记合同") from None


@router.post("/factor-strategies/specification")
async def factor_specification(request: Request, response: Response) -> dict[str, Any]:
    actor(request)
    response.headers["Cache-Control"] = "no-store"
    body = await _body(request)
    if set(body) != {"spec"}:
        raise HTTPException(422, "因子预览仅接受规格字段")
    catalog = _factor_catalog()
    _factor_worker(catalog)
    definition = _factor_preview(body["spec"], catalog)
    return signed({**AUTHORITY, "schema_version": "qtrade.factor-strategy-preview.v1",
        "spec": definition["spec"], "source_revision": catalog["worker"]["source_revision"],
        "strategy_definition": definition,
        "expected_strategy_version": definition["strategy_version"],
        "expected_catalog_sha256": catalog["catalog_sha256"]})


@router.post("/factor-strategies/jobs", status_code=202)
async def submit_factor_strategy(request: Request, response: Response) -> dict[str, Any]:
    user = actor(request)
    response.headers["Cache-Control"] = "no-store"
    body = await _body(request)
    if (set(body) != {"spec", "request_id", "expected_strategy_version",
                     "expected_catalog_sha256"}
            or type(body["request_id"]) is not str
            or re.fullmatch(r"[a-f0-9]{32}", body["request_id"]) is None):
        raise HTTPException(422, "因子登记需要完整预览绑定及幂等请求标识")
    catalog = _factor_catalog()
    listing = _factor_worker(catalog)
    definition = _factor_preview(body["spec"], catalog)
    if (body["expected_strategy_version"] != definition["strategy_version"]
            or body["expected_catalog_sha256"] != catalog["catalog_sha256"]):
        raise HTTPException(409, "因子目录或冻结策略已变化，请重新预览")
    try:
        pending = [row for row in listing["experiments"] if row["state"] not in {
            "COMPLETE", "FAILED", "CANCELLED", "INTERRUPTED", "REJECTED"}]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(503, "研究队列状态不可核验，未提交登记") from None
    if len(pending) >= 3:
        raise HTTPException(429, "研究队列已满，请等待当前任务完成")
    identity = body["request_id"]
    value = {"schema_version": "qtrade.factor-strategy-register-request.v1",
        "kind": "FACTOR_STRATEGY_REGISTER_V1", "job_id": identity, "actor": user,
        "factor_spec": definition["spec"], "factor_spec_sha256": digest(definition["spec"]),
        "expected_strategy_version": definition["strategy_version"],
        "expected_catalog_sha256": catalog["catalog_sha256"],
        "source_revision": catalog["worker"]["source_revision"],
        "data_version_id": definition["evaluation_context"]["data_binding"]["data_version_id"],
        "live_trading_enabled": False}
    try:
        GitQueue.from_environment().append("requests", identity, seal(
            value, os.environ["QTRADE_RESEARCH_QUEUE_SECRET"]))
    except ValueError as error:
        if str(error) == "IMMUTABLE_JOB_ID_CONFLICT":
            raise HTTPException(409, "请求标识已绑定另一份登记规格") from None
        _queue_failure("factor_strategy_register", error)
        raise HTTPException(503, "登记队列配置无效，未确认提交") from None
    except Exception as error:
        _queue_failure("factor_strategy_register", error)
        raise HTTPException(503, "登记未获确认；请使用同一请求标识重试") from None
    return {**AUTHORITY, "job_id": identity, "state": "QUEUED",
        "kind": "FACTOR_STRATEGY_REGISTER_V1", "strategy_version": definition["strategy_version"],
        "spec_sha256": digest(definition["spec"]), "registration_only": True}
