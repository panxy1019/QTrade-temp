"""Opt-in R2 transport for the nonproduction publication protocol.

No credential discovery, bucket creation, deletion or automatic write retry.
SDK tests are not cloud acceptance. Callers own an overall process deadline;
the SDK's individual socket timeouts are not an end-to-end publication SLA.
"""

from __future__ import annotations

import base64
import importlib
import logging
import re
import time
from contextvars import ContextVar
from hashlib import md5, sha256
from typing import Any

from qtrade.research.object_publication import (
    LATEST,
    OBJECT_LIMIT,
    OutcomeUnknown,
    PreconditionFailed,
    StoredObject,
    _etag,
    object_key,
)

_ATTEMPT: ContextVar[list[Any] | None] = ContextVar("qtrade_r2_http_attempt", default=None)


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise ValueError("R2_PUBLICATION_" + code)


def _token(value: Any) -> str:
    return _etag(value)


def _error(error: Exception) -> tuple[str, int | None]:
    """Only classify SDK metadata; never expose exception text or request details."""
    response = getattr(error, "response", None)
    if not isinstance(response, dict):
        return "", None
    detail, metadata = response.get("Error"), response.get("ResponseMetadata")
    code = detail.get("Code", "") if isinstance(detail, dict) else ""
    status = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
    return str(code), status if type(status) is int else None


class R2ObjectStore:
    """Use ``create`` with explicit credentials and an account-scoped endpoint.

    The injected client constructor is an offline testing seam. Production
    callers must use the factory's TLS, retry, endpoint and logging controls.
    """

    def __init__(self, client: Any, account_id: str, bucket: str, *, writable: bool):
        _require(
            type(account_id) is str and re.fullmatch(r"[a-f0-9]{32}", account_id) is not None,
            "ACCOUNT_ID_INVALID",
        )
        _require(
            type(bucket) is str
            and re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket) is not None
            and ".." not in bucket
            and re.fullmatch(r"[0-9.]+", bucket) is None,
            "BUCKET_INVALID",
        )
        _require(type(writable) is bool, "ROLE_INVALID")
        self._client = client
        self._bucket = bucket
        self._writable = writable
        self._endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
        self.scope_id = sha256(
            ("qtrade.r2-s3-v1\0" + self._endpoint + "\0" + bucket).encode()
        ).hexdigest()

    def __repr__(self) -> str:
        return f"R2ObjectStore(scope_id={self.scope_id!r}, writable={self._writable!r})"

    @classmethod
    def create(
        cls,
        *,
        account_id: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        writable: bool = False,
    ) -> R2ObjectStore:
        # Validate nonsecret scope before loading the optional SDK.
        checked = cls(None, account_id, bucket, writable=writable)
        _require(
            all(
                type(v) is str and 1 <= len(v) <= 256 and all(33 <= ord(c) < 127 for c in v)
                for v in (access_key_id, secret_access_key)
            ),
            "CREDENTIALS_REQUIRED",
        )
        _require(
            not any(
                logging.getLogger(name).isEnabledFor(logging.DEBUG)
                for name in ("boto3", "botocore", "urllib3", "s3transfer")
            ),
            "SDK_DEBUG_LOGGING_FORBIDDEN",
        )
        try:
            boto3 = importlib.import_module("boto3")
            config_class = importlib.import_module("botocore.config").Config
        except ImportError:
            raise ValueError("R2_PUBLICATION_OPTIONAL_SDK_REQUIRED") from None
        try:
            client = boto3.session.Session().client(
                "s3",
                endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
                region_name="auto",
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                verify=True,
                use_ssl=True,
                config=config_class(
                    signature_version="s3v4",
                    connect_timeout=5,
                    read_timeout=10,
                    retries={"mode": "standard", "total_max_attempts": 1},
                    proxies={},
                    s3={"addressing_style": "path"},
                    max_pool_connections=4,
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required",
                ),
            )
            # Enforce one wire attempt as well as the SDK retry configuration;
            # redirects/service-specific retry handlers cannot expand this budget.
            client.meta.events.register("before-send.s3", checked._before_send)
        except Exception:
            raise OutcomeUnknown("R2_PUBLICATION_CLIENT_CONFIGURATION_REJECTED") from None
        checked._client = client
        return checked

    def _before_send(self, request: Any, **_: Any) -> None:
        state = _ATTEMPT.get()
        _require(state is not None, "UNSCOPED_SDK_REQUEST")
        assert state is not None
        _require(state[2] == 0, "SDK_RETRY_FORBIDDEN")
        _require(
            request.method == state[0] and request.url == state[1],
            "SDK_DESTINATION_OR_METHOD_CHANGED",
        )
        state[2] += 1

    def _call(self, operation: str, key: str, **kwargs: Any) -> dict[str, Any]:
        token = _ATTEMPT.set(
            [
                "GET" if operation == "get_object" else "PUT",
                self._endpoint + "/" + self._bucket + "/" + key,
                0,
            ]
        )
        try:
            value: dict[str, Any] = getattr(self._client, operation)(
                Bucket=self._bucket, Key=key, **kwargs
            )
            return value
        finally:
            _ATTEMPT.reset(token)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            raise OutcomeUnknown("R2_PUBLICATION_CLOSE_UNCERTAIN") from None

    def get(self, key: str) -> StoredObject | None:
        object_key(key)
        body = None
        try:
            response = self._call("get_object", key)
            body = response.get("Body")
            _require(
                response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200,
                "GET_STATUS_INVALID",
            )
            size = response.get("ContentLength")
            _require(type(size) is int and 0 < size <= OBJECT_LIMIT, "GET_SIZE_INVALID")
            token = _token(response.get("ETag"))
            _require(body is not None, "GET_BODY_MISSING")
            assert body is not None
            deadline = time.monotonic() + 20
            chunks: list[bytes] = []
            count = 0
            while True:
                _require(time.monotonic() < deadline, "READ_DEADLINE")
                raw = body.read(min(65_536, OBJECT_LIMIT + 1 - count))
                _require(type(raw) is bytes, "GET_BODY_TYPE")
                if not raw:
                    break
                chunks.append(raw)
                count += len(raw)
                _require(count <= OBJECT_LIMIT, "GET_SIZE_INVALID")
            _require(time.monotonic() <= deadline and count == size, "GET_BODY_INCOMPLETE")
            raw = b"".join(chunks)
            if key != LATEST:
                _require(
                    sha256(raw).hexdigest() == key.rsplit("/", 1)[1][:-5],
                    "CONTENT_ADDRESS_MISMATCH",
                )
            return StoredObject(raw, token)
        except Exception as error:
            code, status = _error(error)
            if code == "NoSuchKey" and status == 404:
                return None
            raise OutcomeUnknown("R2_PUBLICATION_READ_UNCERTAIN") from None
        finally:
            if body is not None:
                try:
                    body.close()
                except Exception:
                    raise OutcomeUnknown("R2_PUBLICATION_BODY_CLOSE_UNCERTAIN") from None

    def put(
        self, key: str, raw: bytes, *, if_none_match: str | None = None, if_match: str | None = None
    ) -> StoredObject:
        object_key(key)
        _require(self._writable, "READ_ONLY_STORE")
        _require(type(raw) is bytes and 0 < len(raw) <= OBJECT_LIMIT, "BYTE_BOUND")
        _require(
            (if_none_match == "*" and if_match is None)
            or (if_none_match is None and if_match is not None and key == LATEST),
            "CONDITIONAL_WRITE_REQUIRED",
        )
        if if_match is not None:
            _token(if_match)
        if key != LATEST:
            _require(
                sha256(raw).hexdigest() == key.rsplit("/", 1)[1][:-5], "CONTENT_ADDRESS_MISMATCH"
            )
        condition = {"IfNoneMatch": "*"} if if_none_match is not None else {"IfMatch": if_match}
        try:
            response = self._call(
                "put_object",
                key,
                Body=raw,
                ContentLength=len(raw),
                ContentType="application/json",
                ContentMD5=base64.b64encode(md5(raw, usedforsecurity=False).digest()).decode(),
                Metadata={"sha256": sha256(raw).hexdigest()},
                CacheControl="no-store" if key == LATEST else "max-age=31536000, immutable",
                **condition,
            )
            _require(
                response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200,
                "PUT_STATUS_INVALID",
            )
            return StoredObject(raw, _token(response.get("ETag")))
        except Exception as error:
            code, status = _error(error)
            if code == "PreconditionFailed" and status == 412:
                raise PreconditionFailed("R2_PUBLICATION_CONDITION_REJECTED") from None
            raise OutcomeUnknown("R2_PUBLICATION_WRITE_UNCERTAIN") from None
