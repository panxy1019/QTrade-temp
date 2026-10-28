"""Author's inverse replay of the six original independent counterexamples."""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_r2_sdk_independent_original as original
from qtrade.research.object_publication import OutcomeUnknown

synthetic_only = original.synthetic_only
SPEC = importlib.util.spec_from_file_location("frozen_fixed_r2", Path(__file__).parent / "r2_object_store_fixed.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def create():
    return MODULE.R2ObjectStore.create(
        account_id=original.ACCOUNT, bucket=original.BUCKET,
        access_key_id="SYNTHETIC_ACCESS_KEY_NOT_AUTHORIZED",
        secret_access_key="SYNTHETIC_SECRET_NOT_AUTHORIZED", writable=True,
    )


def test_fixed_explicit_credentials_never_discovers_shared_config(synthetic_only):
    seen, config = synthetic_only
    store = create()
    try:
        assert str(config) not in seen
        assert seen
    finally:
        store.close()


@pytest.mark.parametrize("logger_name", ["botocore", "botocore.endpoint"])
def test_fixed_late_debug_cannot_sign_log_or_send(monkeypatch, logger_name):
    store = create()
    count = original.wire(monkeypatch, store)
    logger = logging.getLogger(logger_name)
    previous = logger.level
    try:
        with original.observe_endpoint_logs() as observed:
            logger.setLevel(logging.DEBUG)
            with pytest.raises(OutcomeUnknown, match="^R2_PUBLICATION_WRITE_UNCERTAIN$"):
                store.put(original.KEY, original.RAW, if_none_match="*")
            assert count == [] and not observed.signed_header_logged
    finally:
        logger.setLevel(previous)
        store.close()


def test_fixed_factory_rejects_preexisting_child_debug():
    logger = logging.getLogger("botocore.endpoint")
    previous = logger.level
    try:
        with original.observe_endpoint_logs() as observed:
            logger.setLevel(logging.DEBUG)
            with pytest.raises(ValueError, match="SDK_DEBUG_LOGGING_FORBIDDEN"):
                create()
            assert not observed.signed_header_logged
    finally:
        logger.setLevel(previous)


def test_fixed_factory_does_not_load_environment_selected_plugin(monkeypatch):
    initialized = []
    plugin = SimpleNamespace(initialize_client_plugin=lambda client: initialized.append(True))
    monkeypatch.setitem(sys.modules, "synthetic_r2_client_plugin", plugin)
    monkeypatch.setenv("BOTOCORE_EXPERIMENTAL__PLUGINS", "fixture=synthetic_r2_client_plugin")
    store = create()
    try:
        assert initialized == []
    finally:
        store.close()


def test_fixed_factory_never_consults_environment_selected_model_path(monkeypatch, tmp_path):
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
        assert str(path) not in seen
    finally:
        store.close()
