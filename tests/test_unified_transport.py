import json
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread

import httpx
import pytest

from scripts import collect_unified_data as collector

PARAMS = {'symbol': 'sh600036', 'scale': '5', 'ma': 'no', 'datalen': '2'}


def test_pass_transport_reuses_connections_across_batches_with_at_most_four(monkeypatch):
    accepted = []
    lock = Lock()

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def get_request(self):
            connection, address = super().get_request()
            with lock:
                accepted.append(address)
            return connection, address

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):
            body = b'[{"source":"exact response bytes"}]'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = Server(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(collector, 'KLINE', f'http://127.0.0.1:{server.server_port}/data')
    original_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client',
                        lambda **kwargs: original_client(trust_env=False, **kwargs))
    try:
        with collector.PooledSinaSource() as source:
            first = source('sina_klines', PARAMS)
            second = source('sina_klines', PARAMS)
            assert len(accepted) == 1
            assert first['body'] == second['body']
            for _ in range(2):
                with ThreadPoolExecutor(max_workers=4) as pool:
                    rows = list(pool.map(lambda _: source('sina_klines', PARAMS), range(12)))
                assert all(r['body_complete'] and r['transport_error'] is None for r in rows)
                assert len(accepted) <= 4
        assert source.client.is_closed
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize('status,body', [(429, b'vendor busy'), (200, b'x' * 2_000_000)],
                         ids=['rate-limit', 'exact-size-limit'])
def test_pooled_transport_preserves_bounded_http_body_without_retry(status, body):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers['accept-encoding'] == 'identity'
        return httpx.Response(status, stream=httpx.ByteStream(body))

    source = collector.PooledSinaSource()
    with httpx.Client(transport=httpx.MockTransport(handler),
                      headers={'Accept-Encoding': 'identity'}) as client:
        source.client = client
        capture = source('sina_klines', PARAMS)
    assert len(calls) == 1
    assert capture['status_code'] == status
    assert capture['body'] == body and capture['body_complete']
    assert capture['transport_error'] is None
    if status != 200:
        with pytest.raises(ValueError, match='SOURCE_HTTP_ERROR'):
            collector.require_download(capture)


def test_pooled_transport_stops_oversize_without_retry():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b'x' * 2_000_001))

    source = collector.PooledSinaSource()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source.client = client
        capture = source('sina_klines', PARAMS)
        with pytest.raises(ValueError, match='UNAPPROVED_SOURCE'):
            source('external', {})
    assert len(calls) == 1 and len(capture['body']) == 2_000_000
    assert capture['transport_error'] == 'ValueError' and not capture['body_complete']
    with pytest.raises(ValueError, match='SOURCE_TRANSPORT_ERROR'):
        collector.require_download(capture)


def test_pooled_transport_does_not_follow_a_provider_redirect():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={'Location': 'https://example.invalid/'},
                              stream=httpx.ByteStream(b'redirect body retained'))

    source = collector.PooledSinaSource()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source.client = client
        capture = source('sina_klines', PARAMS)
    assert len(calls) == 1
    assert capture['status_code'] == 302 and capture['body'] == b'redirect body retained'
    with pytest.raises(ValueError, match='SOURCE_HTTP_ERROR'):
        collector.require_download(capture)


def test_mid_body_disconnect_keeps_partial_bytes_and_is_not_a_download():
    class Partial(httpx.SyncByteStream):
        def __iter__(self):
            yield b'partial bytes'
            raise httpx.ReadError('not logged')

    source = collector.PooledSinaSource()
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=Partial()),
    )) as client:
        source.client = client
        capture = source('sina_klines', PARAMS)
    assert capture['body'] == b'partial bytes'
    assert capture['transport_error'] == 'ReadError'
    assert not capture['body_complete']
    assert 'not logged' not in json.dumps({k: v for k, v in capture.items() if k != 'body'})
