from __future__ import annotations

import io
import logging
import os
import socket
import sys
from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from qtrade.research.object_publication import (
    LATEST,
    OBJECT_LIMIT,
    OutcomeUnknown,
    PreconditionFailed,
)
from qtrade.research.r2_object_store import R2ObjectStore

ACCOUNT = "a" * 32
BUCKET = "qtrade-nonproduction-fixture"
RAW = b'{"fixture":true}'
KEY = "market/shards/" + sha256(RAW).hexdigest() + ".json"


class Body(io.BytesIO):
    pass


class Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.body = Body(RAW)
        self.error: Exception | None = None
        self.response: dict[str, Any] = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "ContentLength": len(RAW),
            "ETag": '"opaque-etag"',
            "Body": self.body,
        }

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("GET", kwargs))
        if self.error is not None:
            raise self.error
        return self.response

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("PUT", kwargs))
        if self.error is not None:
            raise self.error
        return self.response


def store(client: Client, writable: bool = True) -> R2ObjectStore:
    return R2ObjectStore(client, ACCOUNT, BUCKET, writable=writable)


def sdk_error(code: str, status: int) -> Exception:
    error = RuntimeError("SYNTHETIC_PRIVATE_MESSAGE_MUST_NOT_ESCAPE")
    error.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}  # type: ignore[attr-defined]
    return error


def test_complete_get_checks_content_hash_and_closes() -> None:
    client = Client()
    result = store(client).get(KEY)
    assert result is not None and result.raw == RAW and result.etag == '"opaque-etag"'
    assert client.body.closed and client.calls == [("GET", {"Bucket": BUCKET, "Key": KEY})]


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("NoSuchBucket", 404),
        ("AccessDenied", 403),
        ("NoSuchKey", 403),
        ("SlowDown", 503),
        ("PermanentRedirect", 301),
    ],
)
def test_only_exact_missing_object_is_missing(code: str, status: int) -> None:
    client = Client()
    client.error = sdk_error(code, status)
    with pytest.raises(OutcomeUnknown, match="^R2_PUBLICATION_READ_UNCERTAIN$"):
        store(client).get(KEY)
    assert len(client.calls) == 1


def test_exact_no_such_key_is_missing_not_definitive_previous_write_failure() -> None:
    client = Client()
    client.error = sdk_error("NoSuchKey", 404)
    assert store(client).get(KEY) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ContentLength", True),
        ("ContentLength", 0),
        ("ContentLength", OBJECT_LIMIT + 1),
        ("ContentLength", len(RAW) + 1),
        ("ETag", 'W/"weak"'),
        ("ETag", "bad\nheader"),
    ],
)
def test_invalid_get_metadata_rejects_and_closes(field: str, value: Any) -> None:
    client = Client()
    client.response[field] = value
    with pytest.raises(OutcomeUnknown):
        store(client).get(KEY)
    assert client.body.closed


def test_wrong_content_rejects_even_when_metadata_and_length_match() -> None:
    client = Client()
    client.response["Body"] = Body(b"x" * len(RAW))
    with pytest.raises(OutcomeUnknown):
        store(client).get(KEY)
    assert client.response["Body"].closed


def test_put_conditions_are_forwarded_without_treating_etag_as_hash() -> None:
    client = Client()
    writer = store(client)
    result = writer.put(KEY, RAW, if_none_match="*")
    assert result.raw == RAW and result.etag == '"opaque-etag"'
    args = client.calls[-1][1]
    assert args["IfNoneMatch"] == "*" and "IfMatch" not in args
    assert args["Metadata"]["sha256"] == sha256(RAW).hexdigest()
    assert args["ContentLength"] == len(RAW) and args["ContentMD5"]
    writer.put(LATEST, RAW, if_match='"previous-token"')
    assert client.calls[-1][1]["IfMatch"] == '"previous-token"'
    assert client.calls[-1][1]["CacheControl"] == "no-store"


@pytest.mark.parametrize(
    "conditions",
    [
        {},
        {"if_match": '"old"'},
        {"if_none_match": "wrong"},
        {"if_none_match": "*", "if_match": '"old"'},
    ],
)
def test_immutable_objects_cannot_be_overwritten(conditions: dict[str, str]) -> None:
    client = Client()
    with pytest.raises(ValueError):
        store(client).put(KEY, RAW, **conditions)
    assert not client.calls


def test_readonly_role_and_unsafe_keys_stop_before_sdk() -> None:
    client = Client()
    with pytest.raises(ValueError, match="READ_ONLY_STORE"):
        store(client, False).put(KEY, RAW, if_none_match="*")
    for key in ("https://example.invalid/key", "../private", "market/shards/wrong.json"):
        with pytest.raises(ValueError):
            store(client).get(key)
    assert not client.calls


@pytest.mark.parametrize(
    ("code", "status", "expected"),
    [
        ("PreconditionFailed", 412, PreconditionFailed),
        ("ConditionalRequestConflict", 409, OutcomeUnknown),
        ("AccessDenied", 403, OutcomeUnknown),
    ],
)
def test_put_error_is_sanitized_and_never_retried(code: str, status: int, expected: type) -> None:
    client = Client()
    client.error = sdk_error(code, status)
    with pytest.raises(expected) as result:
        store(client).put(KEY, RAW, if_none_match="*")
    assert "SYNTHETIC_PRIVATE" not in str(result.value) and len(client.calls) == 1


def test_factory_rejects_debug_logging_before_loading_sdk(caplog: pytest.LogCaptureFixture) -> None:
    with (
        caplog.at_level(logging.DEBUG, logger="botocore"),
        pytest.raises(ValueError, match="SDK_DEBUG_LOGGING_FORBIDDEN"),
    ):
        R2ObjectStore.create(
            account_id=ACCOUNT,
            bucket=BUCKET,
            access_key_id="SYNTHETIC_KEY",
            secret_access_key="SYNTHETIC_SECRET",
        )


@pytest.fixture
def sdk_sandbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    pytest.importorskip("boto3", reason="install project r2 extra for actual SDK tests")
    import botocore.configloader
    import botocore.loaders

    seen: list[str] = []
    shared = tmp_path / "must-not-read-shared-config"
    credentials = tmp_path / "must-not-read-shared-credentials"
    monkeypatch.setenv("AWS_CONFIG_FILE", str(shared))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.setenv("AWS_PROFILE", "synthetic-profile-that-does-not-exist")
    monkeypatch.setenv("AWS_DEFAULT_PROFILE", "synthetic-profile-that-does-not-exist")
    monkeypatch.setenv("BOTO_CONFIG", os.devnull)
    monkeypatch.setenv("BOTOCORE_EXPERIMENTAL__PLUGINS", "DISABLED")
    parse = botocore.configloader.raw_config_parse

    def isolated_parse(path: str, *args: Any, **kwargs: Any) -> Any:
        assert path == os.devnull, "SDK attempted ambient configuration discovery"
        seen.append(path)
        return parse(path, *args, **kwargs)

    def no_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Network is forbidden in offline SDK tests")

    monkeypatch.setattr(botocore.configloader, "raw_config_parse", isolated_parse)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    # Both model discovery mechanisms contain poison data: consulting either
    # must fail, rather than silently producing a client with a changed model.
    model_paths = [tmp_path / "environment-models", tmp_path / "default-customer-models"]
    for model_path in model_paths:
        target = model_path / "s3" / "2006-03-01"
        target.mkdir(parents=True)
        (target / "service-2.json").write_text("{}", encoding="ascii")
    monkeypatch.setenv("AWS_DATA_PATH", str(model_paths[0]))
    monkeypatch.setattr(botocore.loaders.Loader, "CUSTOMER_DATA_PATH", str(model_paths[1]))
    return {"seen_config": seen, "model_paths": model_paths}


@pytest.fixture
def native(sdk_sandbox: dict[str, Any]) -> Iterator[R2ObjectStore]:
    client = R2ObjectStore.create(
        account_id=ACCOUNT,
        bucket=BUCKET,
        access_key_id="SYNTHETIC_ACCESS_KEY_NOT_AUTHORIZED",
        secret_access_key="SYNTHETIC_SECRET_NOT_AUTHORIZED",
        writable=True,
    )
    try:
        yield client
    finally:
        client.close()


def test_actual_sdk_configuration_is_explicit_and_repr_has_no_credentials(
    native: R2ObjectStore,
) -> None:
    config = native._client.meta.config
    assert config.retries["total_max_attempts"] == 1
    assert config.proxies == {} and config.signature_version == "s3v4"
    assert config.s3["addressing_style"] == "path"
    assert native._client.meta.endpoint_url == f"https://{ACCOUNT}.r2.cloudflarestorage.com"
    assert "SYNTHETIC" not in repr(native)


def test_actual_sdk_serializes_conditional_put_and_get_without_network(
    native: R2ObjectStore,
) -> None:
    from botocore.stub import Stubber

    with Stubber(native._client) as stub:
        stub.add_response(
            "put_object", {"ETag": '"sdk-opaque"', "ResponseMetadata": {"HTTPStatusCode": 200}}
        )
        stub.add_response(
            "get_object",
            {
                "ETag": '"sdk-opaque"',
                "ContentLength": len(RAW),
                "Body": Body(RAW),
                "ResponseMetadata": {"HTTPStatusCode": 200},
            },
            {"Bucket": BUCKET, "Key": KEY},
        )
        native.put(KEY, RAW, if_none_match="*")
        result = native.get(KEY)
        assert result is not None and result.raw == RAW
        stub.assert_no_pending_responses()


def test_actual_signed_sdk_request_hits_only_fixed_origin_and_one_attempt(
    native: R2ObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from botocore.awsrequest import AWSResponse

    seen: list[Any] = []

    def send(request: Any) -> Any:
        seen.append(request)
        assert request.url == f"https://{ACCOUNT}.r2.cloudflarestorage.com/{BUCKET}/{KEY}"
        assert request.method == "PUT" and b"AWS4-HMAC-SHA256" in request.headers["Authorization"]
        return AWSResponse(
            request.url, 200, {"etag": '"wire-opaque"'}, SimpleNamespace(stream=lambda: iter(()))
        )

    monkeypatch.setattr(native._client._endpoint.http_session, "send", send)
    assert native.put(KEY, RAW, if_none_match="*").etag == '"wire-opaque"'
    assert len(seen) == 1 and seen[0].headers["If-None-Match"] == b"*"


def test_wire_guard_rejects_retry_redirect_and_unscoped_calls(native: R2ObjectStore) -> None:
    from qtrade.research.r2_object_store import _ATTEMPT

    request = SimpleNamespace(
        method="PUT", url=f"https://{ACCOUNT}.r2.cloudflarestorage.com/{BUCKET}/{KEY}"
    )
    with pytest.raises(ValueError, match="UNSCOPED"):
        native._before_send(request)
    token = _ATTEMPT.set(["PUT", request.url, 0])
    try:
        native._before_send(request)
        with pytest.raises(ValueError, match="SDK_RETRY_FORBIDDEN"):
            native._before_send(request)
    finally:
        _ATTEMPT.reset(token)
    token = _ATTEMPT.set(["PUT", "https://different.invalid/path", 0])
    try:
        with pytest.raises(ValueError, match="SDK_DESTINATION_OR_METHOD_CHANGED"):
            native._before_send(request)
    finally:
        _ATTEMPT.reset(token)


@pytest.mark.parametrize("failure", ["timeout", "redirect", "unavailable"])
def test_actual_sdk_failure_does_not_send_a_second_request(
    native: R2ObjectStore,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    from botocore.awsrequest import AWSResponse
    from botocore.exceptions import ReadTimeoutError

    seen: list[Any] = []

    def send(request: Any) -> Any:
        seen.append(request)
        if failure == "timeout":
            raise ReadTimeoutError(endpoint_url="https://synthetic-private.invalid")
        code, status = ("PermanentRedirect", 301) if failure == "redirect" else ("SlowDown", 503)
        raw = ("<Error><Code>" + code + "</Code><Message>synthetic</Message></Error>").encode()
        headers = {"x-amz-bucket-region": "us-east-1", "content-length": str(len(raw))}
        return AWSResponse(
            request.url, status, headers, SimpleNamespace(stream=lambda: iter([raw]))
        )

    monkeypatch.setattr(native._client._endpoint.http_session, "send", send)
    with pytest.raises(OutcomeUnknown, match="^R2_PUBLICATION_WRITE_UNCERTAIN$"):
        native.put(KEY, RAW, if_none_match="*")
    assert len(seen) == 1


def test_factory_never_reads_environment_selected_shared_files(
    sdk_sandbox: dict[str, Any], native: R2ObjectStore
) -> None:
    assert sdk_sandbox["seen_config"]
    assert set(sdk_sandbox["seen_config"]) == {os.devnull}


def test_factory_ignores_environment_and_default_customer_models(
    sdk_sandbox: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import botocore.loaders

    seen: list[str] = []
    original = botocore.loaders.Loader.load_service_model

    def model(loader: Any, *args: Any, **kwargs: Any) -> Any:
        seen.extend(loader.search_paths)
        return original(loader, *args, **kwargs)

    monkeypatch.setattr(botocore.loaders.Loader, "load_service_model", model)
    client = R2ObjectStore.create(
        account_id=ACCOUNT,
        bucket=BUCKET,
        access_key_id="SYNTHETIC_ACCESS_KEY_NOT_AUTHORIZED",
        secret_access_key="SYNTHETIC_SECRET_NOT_AUTHORIZED",
    )
    try:
        assert seen
        assert not set(map(str, sdk_sandbox["model_paths"])) & set(seen)
        assert botocore.loaders.Loader.BUILTIN_DATA_PATH in seen
    finally:
        client.close()


@pytest.mark.parametrize("outer_context", [False, True])
def test_factory_does_not_initialize_ambient_plugins_and_restores_context(
    sdk_sandbox: dict[str, Any], monkeypatch: pytest.MonkeyPatch, outer_context: bool
) -> None:
    import botocore.plugin

    initialized: list[bool] = []
    module = SimpleNamespace(initialize_client_plugin=lambda client: initialized.append(True))
    monkeypatch.setitem(sys.modules, "synthetic_plugin_that_must_not_run", module)
    selected = "fixture=synthetic_plugin_that_must_not_run"
    monkeypatch.setenv("BOTOCORE_EXPERIMENTAL__PLUGINS", selected)
    prior = botocore.plugin.get_plugin_context()
    context = botocore.plugin.PluginContext(plugins=selected) if outer_context else None
    token = botocore.plugin.set_plugin_context(context)
    try:
        client = R2ObjectStore.create(
            account_id=ACCOUNT,
            bucket=BUCKET,
            access_key_id="SYNTHETIC_ACCESS_KEY_NOT_AUTHORIZED",
            secret_access_key="SYNTHETIC_SECRET_NOT_AUTHORIZED",
        )
        client.close()
        assert not initialized
        assert botocore.plugin.get_plugin_context() is context
        assert os.environ["BOTOCORE_EXPERIMENTAL__PLUGINS"] == selected
    finally:
        botocore.plugin.reset_plugin_context(token)
    assert botocore.plugin.get_plugin_context() is prior


def test_factory_restores_plugin_context_on_sdk_configuration_failure(
    sdk_sandbox: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import boto3.session
    import botocore.plugin

    context = botocore.plugin.PluginContext(plugins="outer=synthetic_not_loaded")
    token = botocore.plugin.set_plugin_context(context)

    def rejected(*args: Any, **kwargs: Any) -> None:
        assert botocore.plugin.get_plugin_context().plugins == "DISABLED"
        raise RuntimeError("SYNTHETIC_PRIVATE_CONFIGURATION_DETAIL")

    monkeypatch.setattr(boto3.session.Session, "client", rejected)
    try:
        with pytest.raises(OutcomeUnknown, match="^R2_PUBLICATION_CLIENT_CONFIGURATION_REJECTED$"):
            R2ObjectStore.create(
                account_id=ACCOUNT,
                bucket=BUCKET,
                access_key_id="SYNTHETIC_ACCESS_KEY_NOT_AUTHORIZED",
                secret_access_key="SYNTHETIC_SECRET_NOT_AUTHORIZED",
            )
        assert botocore.plugin.get_plugin_context() is context
    finally:
        botocore.plugin.reset_plugin_context(token)


@pytest.mark.parametrize("logger_name", ["botocore.endpoint", "urllib3.connectionpool"])
def test_factory_rejects_child_debug_before_sdk_configuration(
    caplog: pytest.LogCaptureFixture, logger_name: str
) -> None:
    with (
        caplog.at_level(logging.DEBUG, logger=logger_name),
        pytest.raises(ValueError, match="SDK_DEBUG_LOGGING_FORBIDDEN"),
    ):
        R2ObjectStore.create(
            account_id=ACCOUNT,
            bucket=BUCKET,
            access_key_id="SYNTHETIC_ACCESS_KEY_NOT_AUTHORIZED",
            secret_access_key="SYNTHETIC_SECRET_NOT_AUTHORIZED",
        )


@pytest.mark.parametrize("logger_name", ["botocore", "botocore.endpoint"])
def test_late_debug_stops_before_signing_or_wire_send(
    native: R2ObjectStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    logger_name: str,
) -> None:
    sent: list[bool] = []

    def send(request: Any) -> None:
        sent.append(True)
        raise AssertionError("SDK should not reach HTTP transport")

    monkeypatch.setattr(native._client._endpoint.http_session, "send", send)
    with (
        caplog.at_level(logging.DEBUG, logger=logger_name),
        pytest.raises(OutcomeUnknown, match="^R2_PUBLICATION_WRITE_UNCERTAIN$"),
    ):
        native.put(KEY, RAW, if_none_match="*")
    assert not sent and "SYNTHETIC_ACCESS_KEY" not in caplog.text


def test_wire_guard_also_rechecks_child_debug(
    native: R2ObjectStore, caplog: pytest.LogCaptureFixture
) -> None:
    from qtrade.research.r2_object_store import _ATTEMPT

    request = SimpleNamespace(
        method="PUT", url=f"https://{ACCOUNT}.r2.cloudflarestorage.com/{BUCKET}/{KEY}"
    )
    state = ["PUT", request.url, 0]
    token = _ATTEMPT.set(state)
    try:
        with (
            caplog.at_level(logging.DEBUG, logger="botocore.endpoint"),
            pytest.raises(ValueError, match="SDK_DEBUG_LOGGING_FORBIDDEN"),
        ):
            native._before_send(request)
        assert state[2] == 0
    finally:
        _ATTEMPT.reset(token)
