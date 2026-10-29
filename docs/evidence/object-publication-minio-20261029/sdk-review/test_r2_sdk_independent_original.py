"""Offline counterexamples against the frozen original provider; synthetic only."""
from __future__ import annotations

import importlib.util
import logging
import os
import socket
import sys
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location("frozen_original_r2", ROOT / "r2_object_store_original.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
Store = MODULE.R2ObjectStore
ACCOUNT = "a" * 32
BUCKET = "qtrade-independent-synthetic"
RAW = b'{"synthetic":true}'
KEY = "market/shards/" + sha256(RAW).hexdigest() + ".json"


@pytest.fixture(autouse=True)
def synthetic_only(monkeypatch, tmp_path):
    import botocore.configloader
    import botocore.loaders

    config, credentials = tmp_path / "fixture-config", tmp_path / "fixture-credentials"
    config.write_text("[profile fixture]\nregion = auto\n", encoding="ascii")
    credentials.write_text("", encoding="ascii")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.setenv("AWS_PROFILE", "fixture")
    monkeypatch.setenv("AWS_DEFAULT_PROFILE", "fixture")
    monkeypatch.setenv("BOTO_CONFIG", os.devnull)
    monkeypatch.setenv("AWS_DATA_PATH", "")
    monkeypatch.setenv("BOTOCORE_EXPERIMENTAL__PLUGINS", "DISABLED")
    synthetic_models = tmp_path / "fixture-models"
    synthetic_models.mkdir()
    monkeypatch.setattr(botocore.loaders.Loader, "CUSTOMER_DATA_PATH", str(synthetic_models))
    parse = botocore.configloader.raw_config_parse
    seen = []

    def guarded_parse(path, *args, **kwargs):
        assert str(path) in {str(config), str(credentials), os.devnull}, "NO_PRIVATE_CONFIG_READ"
        seen.append(str(path))
        return parse(path, *args, **kwargs)

    def no_network(*_args, **_kwargs):
        raise AssertionError("NETWORK_FORBIDDEN")

    monkeypatch.setattr(botocore.configloader, "raw_config_parse", guarded_parse)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    levels = {}
    for name in ("boto3", "botocore", "urllib3", "s3transfer", "botocore.endpoint"):
        logger = logging.getLogger(name)
        levels[name] = logger.level
        logger.setLevel(logging.WARNING if name != "botocore.endpoint" else logging.NOTSET)
    try:
        yield seen, config
    finally:
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)


def create():
    return Store.create(
        account_id=ACCOUNT, bucket=BUCKET,
        access_key_id="SYNTHETIC_ACCESS_KEY_NOT_AUTHORIZED",
        secret_access_key="SYNTHETIC_SECRET_NOT_AUTHORIZED", writable=True,
    )


class SafeProjection(logging.Handler):
    """Keep only a boolean, never archive any raw signature/header record."""
    def __init__(self):
        super().__init__()
        self.signed_header_logged = False

    def emit(self, record):
        value = record.getMessage()
        self.signed_header_logged |= (
            "Authorization" in value and "SYNTHETIC_ACCESS_KEY_NOT_AUTHORIZED" in value
        )


@contextmanager
def observe_endpoint_logs():
    logger = logging.getLogger("botocore.endpoint")
    old_handlers, old_propagate = logger.handlers[:], logger.propagate
    handler = SafeProjection()
    logger.handlers, logger.propagate = [handler], False
    try:
        yield handler
    finally:
        logger.handlers, logger.propagate = old_handlers, old_propagate


def wire(monkeypatch, store):
    from botocore.awsrequest import AWSResponse
    count = []

    def send(request):
        count.append(1)
        return AWSResponse(request.url, 200, {"etag": '"synthetic-etag"'},
                           SimpleNamespace(stream=lambda: iter(())))

    monkeypatch.setattr(store._client._endpoint.http_session, "send", send)
    return count


def test_original_explicit_credentials_still_discovers_shared_config(synthetic_only):
    seen, config = synthetic_only
    store = create()
    try:
        assert str(config) in seen
    finally:
        store.close()


@pytest.mark.parametrize("logger_name", ["botocore", "botocore.endpoint"])
def test_original_late_debug_sends_and_logs_signed_header(monkeypatch, logger_name):
    store = create()
    count = wire(monkeypatch, store)
    logger, previous = logging.getLogger(logger_name), logging.getLogger(logger_name).level
    try:
        with observe_endpoint_logs() as observed:
            logger.setLevel(logging.DEBUG)
            store.put(KEY, RAW, if_none_match="*")
            assert count == [1]
            assert observed.signed_header_logged
    finally:
        logger.setLevel(previous)
        store.close()


def test_original_factory_accepts_preexisting_child_debug(monkeypatch):
    logger, previous = logging.getLogger("botocore.endpoint"), logging.getLogger("botocore.endpoint").level
    try:
        with observe_endpoint_logs() as observed:
            logger.setLevel(logging.DEBUG)
            store = create()
            try:
                count = wire(monkeypatch, store)
                store.put(KEY, RAW, if_none_match="*")
                assert count == [1] and observed.signed_header_logged
            finally:
                store.close()
    finally:
        logger.setLevel(previous)


def test_original_factory_loads_environment_selected_plugin(monkeypatch):
    initialized = []
    plugin = SimpleNamespace(initialize_client_plugin=lambda client: initialized.append(True))
    monkeypatch.setitem(sys.modules, "synthetic_r2_client_plugin", plugin)
    monkeypatch.setenv("BOTOCORE_EXPERIMENTAL__PLUGINS", "fixture=synthetic_r2_client_plugin")
    store = create()
    try:
        assert initialized == [True]
    finally:
        store.close()


def test_original_factory_consults_environment_selected_model_path(monkeypatch, tmp_path):
    import botocore.loaders
    import botocore.session
    path = tmp_path / "environment-model-directory"
    path.mkdir()
    monkeypatch.setenv("AWS_DATA_PATH", str(path))
    seen = []
    real_loader = botocore.session.create_loader

    def loader(search_path):
        result = real_loader(search_path)
        seen.extend(result.search_paths)
        return result

    monkeypatch.setattr(botocore.session, "create_loader", loader)
    store = create()
    try:
        assert str(path) in seen
    finally:
        store.close()
