"""Fixed GET-only public source boundaries; all HTTP responses are local fakes."""
from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from email.message import Message
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import ProxyHandler

import pytest
from dulwich.protocol import pkt_line

from qtrade.api import unified_source as source

FIRST = 'a'*40
SECOND = 'b'*40


def advertisement(*, head=FIRST, main=FIRST, capabilities=b'symref=HEAD:refs/heads/main',
                  newline=b'\n', extra=b''):
    return (pkt_line(b'# service=git-upload-pack'+newline)+b'0000'
            +pkt_line(head.encode()+b' HEAD\0'+capabilities+newline)
            +pkt_line(main.encode()+b' refs/heads/main'+newline)+extra+b'0000')


@pytest.fixture(autouse=True)
def reset_cache():
    source._head.cache_clear()
    yield
    source._head.cache_clear()


@pytest.mark.parametrize('newline', [b'\n', b''])
def test_dulwich_packet_reader_accepts_complete_head_main_snapshot(newline):
    assert source.parse_refs(advertisement(newline=newline)) == FIRST


@pytest.mark.parametrize('raw', [
    b'', b'not git', b'0001', b'0002', b'0003', b'0004', b'ffff', b'000aabc',
    advertisement()[:-1], advertisement()+b'x', advertisement()+b'0000',
    advertisement().replace(b'0000', b'0001', 1),
    advertisement().replace(b'git-upload-pack', b'git-receive-pack', 1),
    advertisement().replace(b'# service=git-upload-pack', b'version 2', 1),
    advertisement(head='0'*40, main='0'*40), advertisement(main=SECOND),
    advertisement(head='A'*40, main='A'*40),
    advertisement(capabilities=b'symref=HEAD:refs/heads/other'),
    advertisement(capabilities=b'symref=HEAD:refs/heads/main agent=x agent=x'),
    advertisement(capabilities=b'symref=HEAD:refs/heads/main agent=x agent=y'),
    advertisement(capabilities=b'symref=HEAD:refs/heads/main\0agent=x'),
    advertisement(capabilities=b'agent=x\nmalformed'),
    advertisement(extra=pkt_line(FIRST.encode()+b' refs/heads/main\n')),
    advertisement(extra=pkt_line(FIRST.encode()+b' refs/heads/../escape\n')),
    advertisement(extra=pkt_line(FIRST.encode()+b' refs/heads/x\0agent=x\n')),
    pkt_line(b'# service=git-upload-pack\n')+b'0000'+pkt_line(
        FIRST.encode()+b' HEAD\0agent=x\n')+b'0000',
    pkt_line(b'# service=git-upload-pack\n')+b'0000'+pkt_line(
        FIRST.encode()+b' refs/heads/main\0agent=x\n')+b'0000',
    FIRST.encode()+b'\trefs/heads/main\n',
])
def test_malformed_or_ambiguous_advertisement_is_rejected(raw):
    with pytest.raises(ValueError, match='UNIFIED_SOURCE_'):
        source.parse_refs(raw)


def test_refs_size_bound_is_checked_before_packet_decode():
    with pytest.raises(ValueError, match='REFS_BOUND'):
        source.parse_refs(b'x'*(source.REFS_LIMIT+1))


def test_minute_head_cache_has_no_old_success_fallback_or_cached_failure(monkeypatch):
    clock = [datetime(2026, 10, 27, 5, 30, 10, tzinfo=UTC)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz or UTC)
    monkeypatch.setattr(source, 'datetime', Clock)
    available = [advertisement()]
    calls = []
    def get(url, limit, *, refs=False):
        assert url == source.REFS_URL and limit == 262_144 and refs
        calls.append(url)
        if available[0] is None:
            raise ValueError('fixture outage')
        return available[0]
    monkeypatch.setattr(source, '_get', get)
    assert source.current_revision() == FIRST
    available[0] = None
    clock[0] += timedelta(seconds=20)
    assert source.current_revision() == FIRST and len(calls) == 1
    clock[0] += timedelta(minutes=1)
    for _ in range(2):
        with pytest.raises(ValueError, match='fixture outage'):
            source.current_revision()
    assert len(calls) == 3
    available[0] = advertisement(head=SECOND, main=SECOND)
    assert source.current_revision() == SECOND and len(calls) == 4
    assert source._head.cache_info().currsize == 1


class Response(io.BytesIO):
    def __init__(self, raw=b'{}', *, url='', status=200, content_type='text/plain',
                 length='automatic', encoding=None):
        super().__init__(raw)
        self.url, self.status = url, status
        self.headers = Message()
        self.headers['Content-Type'] = content_type
        if length is not None:
            self.headers['Content-Length'] = str(len(raw)) if length == 'automatic' else length
        if encoding is not None:
            self.headers['Content-Encoding'] = encoding
    def geturl(self):
        return self.url


@pytest.fixture
def http(monkeypatch):
    calls = []
    response = [Response()]
    monkeypatch.setattr(source, 'getproxies', lambda: {})
    def opener(*handlers):
        assert len(handlers) == 2 and isinstance(handlers[0], ProxyHandler)
        assert isinstance(handlers[1], source._NoRedirect)
        def open_request(request, timeout):
            assert request.get_method() == 'GET' and timeout == source.SOCKET_TIMEOUT == 8
            assert request.get_header('Authorization') is None
            assert request.get_header('Cookie') is None
            assert request.get_header('Proxy-authorization') is None
            assert request.get_header('Accept-encoding') == 'identity'
            assert request.data is None
            calls.append(request)
            if isinstance(response[0], Exception):
                raise response[0]
            if not response[0].url:
                response[0].url = request.full_url
            return response[0]
        return SimpleNamespace(open=open_request)
    monkeypatch.setattr(source, 'build_opener', opener)
    return calls, response


def test_discovery_performs_only_fixed_get_without_extra_query_parameters(http):
    calls, response = http
    response[0] = Response(advertisement(),
                           content_type='application/x-git-upload-pack-advertisement')
    assert source.current_revision() == FIRST
    assert [request.full_url for request in calls] == [source.REFS_URL]
    assert calls[0].get_header('Accept') == 'application/x-git-upload-pack-advertisement'


@pytest.mark.parametrize('name', ['unified.json', 'unified-market.json'])
def test_explicit_commit_download_never_discovers_head_and_returns_actual_bytes(http, monkeypatch,
                                                                              name):
    calls, response = http
    response[0] = Response(b'{ "raw": true }\n', content_type='application/json; charset=utf-8')
    monkeypatch.setattr(source, 'current_revision', lambda: pytest.fail('revision is pinned'))
    assert source.download(name, 100, publication_revision=FIRST) == (b'{ "raw": true }\n', FIRST)
    assert calls[0].full_url == source.RAW_ROOT+FIRST+'/'+name


def test_default_download_binds_one_discovered_revision(http, monkeypatch):
    calls, _ = http
    heads = []
    def head():
        heads.append(FIRST)
        return FIRST
    monkeypatch.setattr(source, 'current_revision', head)
    assert source.download('unified.json', 100) == (b'{}', FIRST)
    assert heads == [FIRST] and calls[0].full_url == source.RAW_ROOT+FIRST+'/unified.json'


def test_shard_download_uses_only_same_commit_immutable_path_with_fixed_bound(http):
    calls, _ = http
    assert source.download_shard('c'*64, FIRST) == b'{}'
    assert calls[0].full_url == source.RAW_ROOT+FIRST+'/unified-market/objects/'+'c'*64+'.json'
    assert source.SHARD_LIMIT == 1_000_000


@pytest.mark.parametrize('digest', ['../x', 'c'*63, 'C'*64, 'c'*64+'?x=1'])
def test_shard_path_cannot_be_injected(http, digest):
    calls, _ = http
    with pytest.raises(ValueError, match='SHARD_IDENTITY_INVALID'):
        source.download_shard(digest, FIRST)
    assert calls == []


@pytest.mark.parametrize('url', ['https://other.invalid/', source.RAW_ROOT+'main/unified.json',
    source.REFS_URL+'&extra=1', source.RAW_ROOT+FIRST+'/unified.json?extra=1'])
def test_internal_transport_also_rejects_non_allowlisted_urls(http, url):
    calls, _ = http
    with pytest.raises(ValueError, match='PATH_OR_BOUND'):
        source._get(url, 100)
    assert calls == []


@pytest.mark.parametrize('name', ['../unified.json', 'https://other.invalid/unified.json',
                                '/unified.json', 'unified.json?x=1', 'unified.json#x',
                                'unified-market/objects/'+'a'*64+'.json'])
def test_caller_cannot_inject_a_source_path_or_url(http, name):
    calls, _ = http
    with pytest.raises(ValueError, match='PATH_OR_BOUND'):
        source.download(name, 100, publication_revision=FIRST)
    assert calls == []


@pytest.mark.parametrize('revision', ['main', '../x', 'a'*39, 'A'*40, '0'*40,
                                    FIRST+'?x=1', 'https://other.invalid'])
def test_caller_cannot_inject_a_revision_or_url(http, revision):
    calls, _ = http
    with pytest.raises(ValueError, match='REVISION_INVALID'):
        source.download('unified.json', 100, publication_revision=revision)
    assert calls == []


@pytest.mark.parametrize('limit', [0, -1, True, 12_000_001])
def test_download_limit_cannot_be_relaxed(http, limit):
    calls, _ = http
    with pytest.raises(ValueError, match='PATH_OR_BOUND'):
        source.download('unified.json', limit, publication_revision=FIRST)
    assert calls == []


@pytest.mark.parametrize('damage', ['type', 'encoding', 'length', 'short', 'oversize',
                                  'missing', 'redirect', 'status', 'duplicate_length'])
def test_http_envelope_or_body_failure_never_yields_source(http, damage):
    _, response = http
    value = Response()
    if damage == 'type':
        value.headers.replace_header('Content-Type', 'text/html')
    elif damage == 'encoding':
        value.headers['Content-Encoding'] = 'gzip'
    elif damage == 'length':
        value.headers.replace_header('Content-Length', '-1')
    elif damage == 'short':
        value.headers.replace_header('Content-Length', '3')
    elif damage == 'oversize':
        value = Response(b'x'*101, length=None)
    elif damage == 'missing':
        value = Response(b'', length=None)
    elif damage == 'redirect':
        value.url = 'https://other.invalid/'
    elif damage == 'status':
        value.status = 302
    else:
        value.headers['Content-Length'] = '2'
    response[0] = value
    with pytest.raises(ValueError, match='UNIFIED_SOURCE_HTTP_'):
        source.download('unified.json', 100, publication_revision=FIRST)


@pytest.mark.parametrize('status', [401, 403, 404, 429, 500])
def test_http_errors_never_fall_back_or_retry(http, status):
    calls, response = http
    response[0] = HTTPError(source.REFS_URL, status, 'fixture', {}, None)
    with pytest.raises(ValueError, match='HTTP_FETCH_FAILED'):
        source.current_revision()
    assert len(calls) == 1


def test_delayed_final_eof_cannot_accept_a_response_past_deadline(http, monkeypatch):
    _, response = http
    clock = [0]
    class DelayedEOF(Response):
        def read1(self, size):
            value = super().read1(size)
            if not value:
                clock[0] = source.CATALOG_BODY_TIMEOUT+1
            return value
    response[0] = DelayedEOF(length=None)
    monkeypatch.setattr(source.time, 'monotonic', lambda: clock[0])
    with pytest.raises(ValueError, match='HTTP_DEADLINE_EXCEEDED'):
        source.download('unified.json', 100, publication_revision=FIRST)


@pytest.mark.parametrize(('resource', 'eof_at', 'accepted'), [
    ('catalog', 9, True), ('catalog', 16, False), ('refs', 9, False),
    ('index', 9, False), ('shard', 9, False)])
def test_only_catalog_has_fifteen_second_body_budget_with_eight_second_socket_timeout(
    http, monkeypatch, resource, eof_at, accepted,
):
    _, response = http
    clock = [0]
    raw = advertisement() if resource == 'refs' else b'{ "fixture": true }\n'
    class Progress(Response):
        reads = 0
        def read1(self, size):
            value = super().read1(min(size, max(1, len(raw)//2)))
            self.reads += 1
            # Each data read makes progress within the unchanged 8s socket
            # timeout; the complete transfer nevertheless needs nine seconds.
            clock[0] = min(9, self.reads*4.5) if value else eof_at
            return value
    response[0] = Progress(raw, content_type=(
        'application/x-git-upload-pack-advertisement' if resource == 'refs' else 'text/plain'))
    monkeypatch.setattr(source.time, 'monotonic', lambda: clock[0])
    def fetch():
        if resource == 'refs':
            return source.current_revision()
        if resource == 'shard':
            return source.download_shard('c'*64, FIRST)
        name = 'unified.json' if resource == 'catalog' else 'unified-market.json'
        return source.download(name, source.LIMITS[name], publication_revision=FIRST)
    if accepted:
        assert fetch() == (raw, FIRST)
    else:
        with pytest.raises(ValueError, match='HTTP_DEADLINE_EXCEEDED'):
            fetch()


@pytest.mark.parametrize('route', ['http://user:secret@proxy:8080', 'http://proxy:bad',
                                 'http://proxy:8080/path', 'http://proxy:8080?x=1'])
def test_proxy_credentials_and_non_endpoint_routes_are_rejected_before_http(http, monkeypatch,
                                                                          route):
    calls, _ = http
    monkeypatch.setattr(source, 'getproxies', lambda: {'https': route})
    with pytest.raises(ValueError, match='PROXY_INVALID'):
        source.current_revision()
    assert calls == []


def test_redirect_handler_never_creates_a_followup_request():
    assert source._NoRedirect().redirect_request(None, None, 302, '', {},
                                                 'https://other.invalid') is None
