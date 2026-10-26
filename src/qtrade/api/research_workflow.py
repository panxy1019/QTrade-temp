"""Authenticated research-only submission; anonymous catalog and immutable results."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from datetime import UTC, datetime
from typing import Any
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from fastapi import APIRouter, HTTPException, Request, Response

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.workflow import SCHEMA, capabilities, digest, validate_spec
from qtrade.research.workflow_queue import GitQueue, seal

router = APIRouter(prefix="/research")
PUBLIC_ROOT = "https://raw.githubusercontent.com/panxy1019/QTrade-observations/main/"
COOKIE = "qtrade_research_session"
ORIGIN = "https://q-trade-temp.vercel.app"


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
        "scope": "RESEARCH_JOBS_ONLY", "external_order_transmission": False,
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
        value["publication_available"] = True
        value["checked_at"] = datetime.now(UTC).isoformat()
        return value
    except Exception:
        return {
            **AUTHORITY, "schema_version": SCHEMA, **capabilities(),
            "publication_available": False, "experiments": [],
            "worker": {"state": "UNAVAILABLE"},
        }


@router.get("/experiments/{experiment_id}")
def experiment(experiment_id: str, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}", experiment_id):
        raise HTTPException(422, "实验标识无效")
    try:
        listing = read_public("workflow.json")
        row = next(
            r for r in listing["experiments"]
            if r["experiment_id"] == experiment_id and r["state"] == "COMPLETE"
        )
        relative = row["detail_file"]
        if not re.fullmatch(r"workflow-results/[a-f0-9]{64}\.json", relative):
            raise ValueError("INVALID_RESULT_PATH")
        result = read_public(relative)
        if result["evidence_sha256"] != row["detail_sha256"]:
            raise ValueError("RESULT_IDENTITY_MISMATCH")
        return result
    except StopIteration:
        raise HTTPException(404, "未找到已完成实验") from None
    except Exception:
        raise HTTPException(503, "实验发布不可用或校验失败") from None


@router.post("/jobs", status_code=202)
async def submit(request: Request) -> dict[str, Any]:
    user = actor(request)
    body = await _body(request)
    if set(body) != {"spec", "request_id"} or not re.fullmatch(
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
        if (datetime.now(UTC) - heartbeat).total_seconds() > 720:
            raise ValueError("WORKER_STALE")
        if len([r for r in listing["experiments"] if r["state"] not in {
            "COMPLETE", "FAILED", "CANCELLED", "INTERRUPTED", "REJECTED"
        }]) >= 3:
            raise HTTPException(429, "研究队列已满，请等待当前任务完成")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "集群执行器未就绪，未提交任务") from None
    identity = body["request_id"]
    # Deterministic envelope supports safe browser retry after a lost response.
    value = {
        "job_id": identity, "actor": user, "spec": spec, "spec_sha256": digest(spec),
        "kind": "QLIB_RESEARCH_JOB", "live_trading_enabled": False,
    }
    try:
        queue = GitQueue.from_environment()
        queue.append("requests", identity, seal(
            value, os.environ["QTRADE_RESEARCH_QUEUE_SECRET"]
        ))
    except ValueError as error:
        if str(error) == "IMMUTABLE_JOB_ID_CONFLICT":
            raise HTTPException(409, "请求标识已绑定另一份规格") from None
        raise HTTPException(503, "研究队列配置无效，未确认提交") from None
    except Exception:
        raise HTTPException(503, "任务提交未获确认；请使用同一请求标识重试") from None
    return {**AUTHORITY, "job_id": identity, "state": "QUEUED", "spec_sha256": digest(spec)}


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
    except Exception:
        raise HTTPException(503, "取消请求未获确认") from None
    return {"job_id": job_id, "state": "CANCEL_REQUESTED"}
