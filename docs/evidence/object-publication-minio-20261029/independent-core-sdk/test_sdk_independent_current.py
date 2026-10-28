"""New independent actual-SDK tests; HTTP send is memory-only and sockets denied."""
import importlib.util
import sys
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

sys.path[:0]=['E:/QTrade-temp/r2-sdk-20261029','E:/QTrade-git/src','E:/QTrade-git',
    'E:/QTrade-git/tests/unit']
from test_r2_object_store import sdk_sandbox  # noqa:F401

HERE=Path(__file__).parent
spec=importlib.util.spec_from_file_location('r2_factory_exact_d266',HERE/'r2_object_store_d266_frozen.py')
r2=importlib.util.module_from_spec(spec);spec.loader.exec_module(r2)
ACCOUNT='a'*32;BUCKET='qtrade-nonproduction-fixture'
RAW=b'{"fixture":"PUBLIC_SYNTHETIC"}'
KEY='market/shards/'+sha256(RAW).hexdigest()+'.json'


def create():
    assert sha256((HERE/'r2_object_store_d266_frozen.py').read_bytes()).hexdigest()==(
        'd26685bf8f95db48ef3db8d0c8d4716aaac56d68963fd2c302fa9b5c62c16202')
    return r2.R2ObjectStore.create(account_id=ACCOUNT,bucket=BUCKET,
        access_key_id='SYNTHETIC_ACCESS_KEY_NOT_AUTHORIZED',
        secret_access_key='SYNTHETIC_SECRET_NOT_AUTHORIZED',writable=True)


def response(request):
    from botocore.awsrequest import AWSResponse
    return AWSResponse(request.url,200,{'etag':'"independent-wire-token"'},
        SimpleNamespace(stream=lambda:iter(())))


def test_fixed_factory_ignores_ambient_endpoint_ca_region_and_proxy(sdk_sandbox,monkeypatch):
    for name,value in {'AWS_ENDPOINT_URL':'http://127.0.0.1:1',
        'AWS_ENDPOINT_URL_S3':'http://127.0.0.1:2','AWS_CA_BUNDLE':'SYNTHETIC_MISSING_CA_FILE',
        'AWS_DEFAULT_REGION':'SYNTHETIC_OTHER_REGION','HTTPS_PROXY':'http://127.0.0.1:3',
        'HTTP_PROXY':'http://127.0.0.1:4'}.items():monkeypatch.setenv(name,value)
    store=create();seen=[]
    def send(request):
        # Never retain signed headers, credentials, exception body or payload.
        seen.append((request.method,request.url))
        return response(request)
    monkeypatch.setattr(store._client._endpoint.http_session,'send',send)
    try:
        assert store._client.meta.endpoint_url==f'https://{ACCOUNT}.r2.cloudflarestorage.com'
        assert store._client.meta.region_name=='auto'
        assert store._client.meta.config.proxies=={}
        assert store._client._endpoint.http_session._verify is True
        assert store.put(KEY,RAW,if_none_match='*').raw==RAW
        assert seen==[('PUT',f'https://{ACCOUNT}.r2.cloudflarestorage.com/{BUCKET}/{KEY}')]
        assert r2._ATTEMPT.get() is None
    finally:store.close()


def test_contextvar_is_per_call_for_two_real_sdk_threads(sdk_sandbox,monkeypatch):
    store=create();barrier=Barrier(2);seen=[]
    def send(request):
        state=r2._ATTEMPT.get()
        assert state is not None and state[1]==request.url and state[2]==1
        barrier.wait(timeout=10)
        seen.append((request.method,request.url))
        return response(request)
    monkeypatch.setattr(store._client._endpoint.http_session,'send',send)
    def worker(number):
        raw=RAW if number==0 else b'{"fixture":"PUBLIC_SYNTHETIC_SECOND"}'
        key='market/shards/'+sha256(raw).hexdigest()+'.json'
        result=store.put(key,raw,if_none_match='*')
        assert r2._ATTEMPT.get() is None
        return result.raw
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert len(list(pool.map(worker,range(2))))==2
        assert len(seen)==2 and len({url for _,url in seen})==2
        assert r2._ATTEMPT.get() is None
    finally:store.close()


def test_nested_sdk_call_restores_outer_attempt_token(sdk_sandbox,monkeypatch):
    outer=create();inner=create();seen=[]
    def inner_send(request):
        state=r2._ATTEMPT.get();assert state[1]==request.url and state[2]==1
        seen.append('inner');return response(request)
    def outer_send(request):
        token=r2._ATTEMPT.get();assert token[1]==request.url and token[2]==1
        inner.put(KEY,RAW,if_none_match='*')
        assert r2._ATTEMPT.get() is token
        seen.append('outer');return response(request)
    monkeypatch.setattr(inner._client._endpoint.http_session,'send',inner_send)
    monkeypatch.setattr(outer._client._endpoint.http_session,'send',outer_send)
    try:
        outer.put(KEY,RAW,if_none_match='*')
        assert seen==['inner','outer'] and r2._ATTEMPT.get() is None
    finally:inner.close();outer.close()


def test_sdk_exception_restores_context_and_redacts_text(sdk_sandbox,monkeypatch):
    store=create();calls=[]
    def send(request):
        calls.append(request.method)
        raise OSError('SYNTHETIC_SECRET_THAT_MUST_NOT_ESCAPE')
    monkeypatch.setattr(store._client._endpoint.http_session,'send',send)
    try:
        with pytest.raises(r2.OutcomeUnknown) as error:store.put(KEY,RAW,if_none_match='*')
        assert str(error.value)=='R2_PUBLICATION_WRITE_UNCERTAIN'
        assert calls==['PUT'] and r2._ATTEMPT.get() is None
    finally:store.close()


def test_missing_pinned_private_sdk_hook_fails_closed(sdk_sandbox,monkeypatch):
    import botocore.plugin
    prior=botocore.plugin.get_plugin_context()
    monkeypatch.delattr(botocore.plugin,'PluginContext')
    with pytest.raises(r2.OutcomeUnknown,match='CLIENT_CONFIGURATION_REJECTED'):create()
    assert botocore.plugin.get_plugin_context() is prior

