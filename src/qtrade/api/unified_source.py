"""Fixed-origin, GET-only public Git discovery and immutable unified pointers."""
from __future__ import annotations

import io
import re
import time
from datetime import UTC, datetime
from functools import lru_cache
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, getproxies

from dulwich.errors import GitProtocolError, HangupException
from dulwich.protocol import Protocol
from dulwich.refs import check_ref_format

REFS_URL = ('https://github.com/panxy1019/QTrade-observations.git/'
            'info/refs?service=git-upload-pack')
RAW_ROOT = 'https://raw.githubusercontent.com/panxy1019/QTrade-observations/'
REFS_LIMIT = 262_144
LIMITS = {'unified.json': 12_000_000, 'unified-market.json': 4_000_000}
SHARD_LIMIT = 1_000_000
TIMEOUT = 8


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError('UNIFIED_SOURCE_'+code)


def validate_revision(value: str) -> str:
    _require(isinstance(value, str) and re.fullmatch('[a-f0-9]{40}', value) is not None
             and value != '0'*40, 'REVISION_INVALID')
    return value


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


def _proxy() -> ProxyHandler:
    """Match the existing acceptance transport's credential-free proxy policy."""
    try:
        route = getproxies().get('https')
        if route is None:
            return ProxyHandler({})
        _require(isinstance(route, str) and bool(route) and route == route.strip()
                 and not any(character.isspace() or ord(character) < 32 for character in route),
                 'PROXY_INVALID')
        parsed = urlsplit(route)
        _require(parsed.scheme.lower() in {'http', 'https'} and bool(parsed.hostname)
                 and parsed.username is None and parsed.password is None
                 and not any(character in parsed.netloc for character in ('@', '%', '\\'))
                 and parsed.path in {'', '/'} and '?' not in route and '#' not in route
                 and (parsed.port is None or 1 <= parsed.port <= 65535), 'PROXY_INVALID')
        return ProxyHandler({'https': route})
    except (OSError, ValueError, TypeError, AttributeError):
        raise ValueError('UNIFIED_SOURCE_PROXY_INVALID') from None


def _get(url: str, limit: int, *, refs: bool = False) -> bytes:
    maximum = REFS_LIMIT if refs and url == REFS_URL else 0
    if not refs:
        match = re.fullmatch(re.escape(RAW_ROOT)+r'([a-f0-9]{40})/(unified\.json|'
            r'unified-market\.json|unified-market/objects/[a-f0-9]{64}\.json)', url)
        if match:
            validate_revision(match[1])
            maximum = LIMITS.get(match[2], SHARD_LIMIT)
    _require(type(limit) is int and 0 < limit <= maximum, 'PATH_OR_BOUND')
    deadline = time.monotonic()+TIMEOUT
    request = Request(url, method='GET', headers={
        'Accept': 'application/x-git-upload-pack-advertisement' if refs else 'application/json',
        'Accept-Encoding': 'identity', 'Cache-Control': 'no-cache',
        'User-Agent': 'QTrade-readonly-unified-source/1'})
    try:
        with build_opener(_proxy(), _NoRedirect()).open(request, timeout=TIMEOUT) as response:
            _require(response.status == 200 and response.geturl() == url,
                     'HTTP_STATUS_OR_REDIRECT_INVALID')
            content_type = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
            allowed = ({'application/x-git-upload-pack-advertisement'} if refs
                       else {'application/json', 'text/plain'})
            _require(content_type in allowed, 'HTTP_CONTENT_TYPE_INVALID')
            _require(response.headers.get('Content-Encoding', 'identity').strip().lower()
                     == 'identity', 'HTTP_CONTENT_ENCODING_INVALID')
            lengths = response.headers.get_all('Content-Length', [])
            _require(len(lengths) <= 1, 'HTTP_LENGTH_INVALID')
            length = lengths[0] if lengths else None
            _require(length is None or re.fullmatch('[0-9]+', length) is not None
                     and 0 < int(length) <= limit, 'HTTP_LENGTH_INVALID')
            chunks, size = [], 0
            while True:
                _require(time.monotonic() < deadline, 'HTTP_DEADLINE_EXCEEDED')
                chunk = response.read1(min(65_536, limit+1-size))
                _require(time.monotonic() <= deadline, 'HTTP_DEADLINE_EXCEEDED')
                if not chunk:
                    break
                size += len(chunk)
                _require(size <= limit, 'HTTP_BODY_BOUND')
                chunks.append(chunk)
            _require(size > 0 and (length is None or size == int(length)), 'HTTP_BODY_INCOMPLETE')
            _require(time.monotonic() <= deadline, 'HTTP_DEADLINE_EXCEEDED')
            return b''.join(chunks)
    except (HTTPError, URLError, HTTPException, OSError):
        raise ValueError('UNIFIED_SOURCE_HTTP_FETCH_FAILED') from None


def parse_refs(raw: bytes) -> str:
    """Reuse Dulwich's packet reader; reject v1 delimiters and dirty/truncated v0 framing."""
    _require(0 < len(raw) <= REFS_LIMIT, 'REFS_BOUND')
    stream = io.BytesIO(raw)
    protocol = Protocol(stream.read, lambda data: 0)

    def packet() -> bytes | None:
        prefix = raw[stream.tell():stream.tell()+4]
        _require(re.fullmatch(b'[a-fA-F0-9]{4}', prefix) is not None, 'PACKET_INVALID')
        size = int(prefix, 16)
        _require(size == 0 or 5 <= size <= 65_520, 'PACKET_INVALID')
        try:
            value = protocol.read_pkt_line()
        except (GitProtocolError, HangupException, ValueError):
            raise ValueError('UNIFIED_SOURCE_PACKET_INVALID') from None
        return value.removesuffix(b'\n') if value is not None else None

    _require(packet() == b'# service=git-upload-pack' and packet() is None, 'SERVICE_INVALID')
    refs: dict[bytes, bytes] = {}
    while True:
        line = packet()
        if line is None:
            break
        if not refs:
            pieces = line.split(b'\0')
            _require(len(pieces) == 2 and bool(pieces[1]) and all(
                32 <= character <= 126 for character in pieces[1]), 'CAPABILITIES_INVALID')
            line, capabilities = pieces
            tokens = capabilities.split(b' ')
            _require(all(tokens) and len(tokens) == len({token.split(b'=')[0] for token in tokens})
                and all(
                not token.startswith(b'symref=HEAD:') or token == b'symref=HEAD:refs/heads/main'
                for token in tokens), 'CAPABILITIES_INVALID')
        else:
            _require(b'\0' not in line, 'CAPABILITIES_INVALID')
        parts = line.split(b' ')
        _require(len(parts) == 2, 'REF_INVALID')
        digest, name = parts
        _require(re.fullmatch(b'[a-f0-9]{40}', digest) is not None and digest != b'0'*40
                 and name not in refs and (name == b'HEAD' if not refs else
                    check_ref_format(name.removesuffix(b'^{}'))), 'REF_INVALID')
        refs[name] = digest
    _require(stream.tell() == len(raw) and b'HEAD' in refs and b'refs/heads/main' in refs
             and refs[b'HEAD'] == refs[b'refs/heads/main'], 'HEAD_MAIN_MISMATCH')
    return refs[b'HEAD'].decode('ascii')


@lru_cache(maxsize=1)
def _head(minute: int) -> str:
    return parse_refs(_get(REFS_URL, REFS_LIMIT, refs=True))


def current_revision() -> str:
    return _head(int(datetime.now(UTC).timestamp())//60)


def download(name: str, limit: int, *,
             publication_revision: str | None = None) -> tuple[bytes, str]:
    _require(name in LIMITS and type(limit) is int and 0 < limit <= LIMITS[name], 'PATH_OR_BOUND')
    revision = (current_revision() if publication_revision is None
                else validate_revision(publication_revision))
    return _get(RAW_ROOT+revision+'/'+name, limit), revision


def download_shard(digest: str, publication_revision: str) -> bytes:
    _require(isinstance(digest, str) and re.fullmatch('[a-f0-9]{64}', digest) is not None,
             'SHARD_IDENTITY_INVALID')
    revision = validate_revision(publication_revision)
    return _get(RAW_ROOT+revision+'/unified-market/objects/'+digest+'.json', SHARD_LIMIT)
