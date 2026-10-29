"""Offline counterexamples: no credential helper, SSH, socket or public Git call.

v1 tests describe reproduced defects; a passing test records a rejection gap,
not transport acceptance. Contract validation is the unmodified local public
qtrade validator; Git batch bytes and the SFTP/transport are synthetic.
"""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import types
from datetime import datetime, timezone

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, 'E:/QTrade-git/src')
from qtrade.research import unified_market as contract
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE/filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def op():
    return load('relay_original_offline', 'publication_native_relay_operator_20261028.py')


@pytest.fixture
def desktop(monkeypatch):
    # Stub the module before import: never import or inspect credential helper.
    fake = types.ModuleType('authorized_cluster_read')
    fake.connect = lambda: pytest.fail('credential/network helper forbidden')
    monkeypatch.setitem(sys.modules, 'authorized_cluster_read', fake)
    return load('relay_desktop_v2_offline', 'run_publication_desktop_relay_v2_20261028.py')


def market_fixture():
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    calendar = Calendar(Path('E:/QTrade-git/configs/observer_calendar_2026.json'))
    entries = []
    for number in range(600000, 601500):
        for freq in ('day', '5min'):
            entries.append({'symbol': str(number), 'instrument_id': 'SSE.'+str(number),
                'security_type': 'STOCK', 'name': 'PUBLIC_SYNTHETIC', 'frequency': freq,
                'state': 'NO_DATA', 'issues': [], 'shards': []})
    bar = {'symbol': '601499', 'exchange': 'SSE', 'frequency': '5min',
        'timestamp': '2026-10-28T09:35:00+08:00', 'trading_day': '2026-10-28',
        'open': '10', 'high': '12', 'low': '9', 'close': '10', 'volume': '100',
        'amount': None, 'vwap': None, 'amount_basis': 'VENDOR_CLAIM_UNVERIFIED',
        'price_basis': contract.PRICE_BASIS, 'historical_available_at': None}
    shard = signed({**AUTHORITY, 'schema_version': 'qtrade.unified-market-shard.v1',
        'universe_version': '1'*64, 'symbol': '601499', 'security_type': 'STOCK',
        'instrument_id': 'SSE.601499', 'frequency': '5min',
        'price_basis': contract.PRICE_BASIS, 'bars': [bar],
        'source_batches': [{'batch_id': '2'*32, 'manifest_sha256': '3'*64,
            'raw_sha256': '4'*64, 'normalized_sha256': '5'*64}]})
    raw = contract.encoded(shard)
    ref = {'sha256': hashlib.sha256(raw).hexdigest(),
        'evidence_sha256': shard['evidence_sha256'], 'bytes': len(raw),
        'start': bar['timestamp'], 'end': bar['timestamp'], 'count': 1}
    entries[-1].update(state='AVAILABLE', shards=[ref])
    index = signed({**AUTHORITY, 'schema_version': 'qtrade.unified-market-index.v1',
        'universe_version': '1'*64, 'master_sha256': '2'*64,
        'published_at': now.isoformat(), 'calendar': calendar.document,
        'windows': {freq: contract.sessions(calendar,
            calendar.expected(now, 'daily' if freq=='day' else 'minute').date(),
            20 if freq=='day' else 3) for freq in ('day', '5min')},
        'entries': entries, 'total_bytes': len(raw), 'total_files': 1,
        'limitations': contract.LIMITATIONS,
        'implementation_sources': {'scripts/publish_unified_market.py':
            '64caab0023626ef0f531f563c3a392d4a2429a9a6c004c5789af17321bbd8d8a',
            'src/qtrade/research/unified_market.py': '3'*64}})
    contract.validate_index(index)
    contract.verify_shard_bytes(raw, ref, index, entries[-1], now)
    return index, raw, ref


def setup_prepare(op, monkeypatch, tmp_path, *, forged=False, attempt_change=None):
    index, raw, ref = market_fixture()
    if forged:
        # The pure index validator permits ref reuse. The first occurrence is
        # wrong for its symbol; only verify_shard_bytes detects the mismatch.
        index['entries'][1].update(state='AVAILABLE', shards=[copy.deepcopy(ref)])
        index = signed({k:v for k,v in index.items() if k!='evidence_sha256'})
        contract.validate_index(index)
        with pytest.raises(ValueError, match='SHARD_INDEX_MISMATCH'):
            contract.verify_shard_bytes(raw, ref, index, index['entries'][1],
                datetime(2026,10,28,12,tzinfo=timezone.utc))
    attempt = signed({**AUTHORITY, 'schema_version':'qtrade.unified-market-export-attempt.v1',
        'started_at':'2026-10-28T11:59:50+00:00',
        'completed_at':'2026-10-28T12:00:10+00:00', 'state':'SUCCEEDED',
        'index_id':index['evidence_sha256'], 'error_class':None, 'error_code':None,
        'elapsed_seconds':20.0})
    if attempt_change:
        attempt_change(attempt)
    base = tmp_path/'base'; (base/'unified-data').mkdir(parents=True)
    (base/'unified-data/market-export-attempt.json').write_text(json.dumps(attempt))
    monkeypatch.setattr(op, 'BASE', base)
    monkeypatch.setattr(op, 'ROOT', tmp_path/'operator')
    op.ROOT.mkdir()
    oid = b'7'*40
    responses = {('rev-parse','HEAD'):b'6'*40+b'\n',
        ('show','6'*40+':unified-market.json'):contract.encoded(index),
        ('cat-file','--batch-check'):oid+b' blob '+str(len(raw)).encode()+b'\n',
        ('cat-file','--batch'):oid+b' blob '+str(len(raw)).encode()+b'\n'+raw+b'\n'}
    def source(args, **kwargs):
        assert tuple(args) in responses
        return responses[tuple(args)]
    monkeypatch.setattr(op, 'source', source)
    captured = {}
    monkeypatch.setattr(op, 'save', lambda name,value: captured.update({name:value}))
    return captured


def test_v1_cross_entry_duplicate_digest_only_last_occurrence_is_verified(op, monkeypatch, tmp_path):
    captured = setup_prepare(op, monkeypatch, tmp_path, forged=True)
    result = op.prepare()
    assert result['all_frozen_git_shards_verified'] is True
    assert 'target.json' in captured


@pytest.mark.parametrize('damage', ['unsigned','bad_checksum','negative_elapsed',
    'boolean_elapsed','wrong_schema','future_unordered_clocks','nonnull_error'])
def test_v1_malformed_export_attempt_is_accepted(op, monkeypatch, tmp_path, damage):
    def change(value):
        if damage=='unsigned': value.pop('evidence_sha256')
        elif damage=='bad_checksum': value['evidence_sha256']='0'*64
        elif damage=='negative_elapsed': value['elapsed_seconds']=-999
        elif damage=='boolean_elapsed': value['elapsed_seconds']=True
        elif damage=='wrong_schema': value['schema_version']='UNTRUSTED'
        elif damage=='future_unordered_clocks':
            value.update(started_at='2099-01-02T00:00:00', completed_at='2099-01-01T00:00:00')
        elif damage=='nonnull_error': value['error_code']='ACTUAL_FAILURE'
    captured=setup_prepare(op, monkeypatch, tmp_path, attempt_change=change)
    assert op.prepare()['all_frozen_git_shards_verified'] is True
    assert 'target.json' in captured


def test_clean_env_drops_git_override_and_trace(op, monkeypatch):
    for key in ['GIT_DIR','GIT_OBJECT_DIRECTORY','GIT_ALTERNATE_OBJECT_DIRECTORIES',
        'GIT_TRACE_PACKET','GIT_CONFIG_COUNT','GIT_SSH_COMMAND','SECRET_SYNTHETIC']:
        monkeypatch.setenv(key,'SYNTHETIC_DO_NOT_PROPAGATE')
    result=op.clean_env()
    assert all(v!='SYNTHETIC_DO_NOT_PROPAGATE' for v in result.values())
    assert result['GIT_NO_REPLACE_OBJECTS']=='1'
    assert result['LIVE_TRADING_ENABLED']=='false'


class SFTP:
    def __init__(self, modes=None):
        self.modes=modes or {};self.data={};self.opens=[];self.chmods=[]
    def lstat(self,path):
        if path not in self.modes:raise FileNotFoundError(path)
        return types.SimpleNamespace(st_mode=self.modes[path],st_size=len(self.data.get(path,b'')))
    def open(self,path,mode):
        self.opens.append((path,mode))
        if mode=='wx':
            if path in self.modes:raise FileExistsError(path)
            owner=self
            class Sink(io.BytesIO):
                def close(self):
                    owner.data[path]=self.getvalue();owner.modes[path]=stat.S_IFREG|0o600
                    super().close()
            return Sink()
        assert mode=='rb';return io.BytesIO(self.data[path])
    def chmod(self,path,mode):self.chmods.append((path,mode))


@pytest.mark.parametrize('mode',[stat.S_IFLNK|0o777,stat.S_IFREG|0o600,stat.S_IFIFO|0o600])
def test_desktop_v2_rejects_non_directory_ancestor(desktop,mode):
    s=SFTP({'/a':stat.S_IFDIR|0o700,'/a/b':mode})
    with pytest.raises(ValueError,match='REMOTE_DIRECTORY_NOT_REAL'):
        desktop.remote_directory(s,'/a/b/c')
    assert s.opens==[]


@pytest.mark.parametrize('mode',[stat.S_IFLNK|0o777,stat.S_IFDIR|0o700,stat.S_IFIFO|0o600])
def test_desktop_v2_rejects_nonregular_source_before_open(desktop,tmp_path,monkeypatch,mode):
    monkeypatch.setattr(desktop,'LOCAL',tmp_path)
    (tmp_path/'tool.py').write_bytes(b'SYNTHETIC_PUBLIC_TOOL')
    target=desktop.REMOTE+'/tool.py';s=SFTP({target:mode})
    with pytest.raises(ValueError,match='REMOTE_SOURCE_NOT_REGULAR'):
        desktop.checked_source(s,'tool.py',hashlib.sha256(b'SYNTHETIC_PUBLIC_TOOL').hexdigest())
    assert s.opens==[]


def test_desktop_v2_exclusive_new_source_and_after_upload_hash(desktop,tmp_path,monkeypatch):
    monkeypatch.setattr(desktop,'LOCAL',tmp_path)
    raw=b'SYNTHETIC_PUBLIC_TOOL';(tmp_path/'tool.py').write_bytes(raw)
    s=SFTP();target=desktop.REMOTE+'/tool.py'
    desktop.checked_source(s,'tool.py',hashlib.sha256(raw).hexdigest())
    assert s.opens==[(target,'wx'),(target,'rb')]
    assert s.data[target]==raw
    desktop.checked_source(s,'tool.py',hashlib.sha256(raw).hexdigest())
    assert [mode for _,mode in s.opens].count('wx')==1


def test_desktop_v2_rejects_existing_changed_source_without_overwrite(desktop,tmp_path,monkeypatch):
    monkeypatch.setattr(desktop,'LOCAL',tmp_path)
    (tmp_path/'tool.py').write_bytes(b'GOOD');target=desktop.REMOTE+'/tool.py'
    s=SFTP({target:stat.S_IFREG|0o600});s.data[target]=b'EVIL'
    with pytest.raises(ValueError,match='REMOTE_SOURCE_CHANGED'):
        desktop.checked_source(s,'tool.py',hashlib.sha256(b'GOOD').hexdigest())
    assert s.data[target]==b'EVIL';assert s.opens==[(target,'rb')]


def test_paramiko_wx_has_write_create_and_exclusive_flags(monkeypatch):
    import paramiko.sftp_client as client
    import paramiko.sftp as constants
    captured=[]
    class Message:
        def get_binary(self):return b'synthetic-handle'
    fake=types.SimpleNamespace(_adjust_cwd=lambda p:p,_log=lambda *a:None,
        _request=lambda *a:(captured.append(a) or (constants.CMD_HANDLE,Message())))
    monkeypatch.setattr(client,'SFTPFile',lambda *a:a)
    client.SFTPClient.open(fake,'/owned/new.py','wx')
    flags=captured[0][2]
    assert flags & constants.SFTP_FLAG_WRITE
    assert flags & constants.SFTP_FLAG_CREATE
    assert flags & constants.SFTP_FLAG_EXCL


@pytest.mark.parametrize('scenario', ['zero_channels','zero_outbound','zero_inbound',
    'pump_error','pump_alive','wrong_origin','wrong_server','wrong_port','duplicate'])
def test_desktop_v2_rejects_git_success_without_complete_relay_attestation(
    desktop,tmp_path,monkeypatch,scenario):
    """Entire main wiring with fake transport. No actual connect/socket/thread."""
    raw=b'SYNTHETIC_PUBLIC_TOOL';(tmp_path/'tool.py').write_bytes(raw)
    (tmp_path/'cluster-evidence').mkdir()
    monkeypatch.setattr(desktop,'LOCAL',tmp_path)
    monkeypatch.setattr(desktop,'FILES',{'tool.py':hashlib.sha256(raw).hexdigest()})
    monkeypatch.setattr(desktop,'remote_directory',lambda *a:None)
    monkeypatch.setattr(desktop,'checked_source',lambda *a:None)
    class Channel:
        closed=False
        def close(self):self.closed=True
        def recv_exit_status(self):return 0
        def shutdown_write(self):pass
    class Stream(io.BytesIO):
        channel=Channel()
    class Transport:
        cancelled=[]
        def request_port_forward(self,host,port,handler):
            assert (host,port)==('127.0.0.1',0)
            self.handler=handler;return 44000
        def cancel_port_forward(self,host,port):self.cancelled.append((host,port))
    transport=Transport()
    class Connection:
        commands=[];closed=False
        def get_transport(self):return transport
        def open_sftp(self):return types.SimpleNamespace(mkdir=lambda *a:None,close=lambda:None)
        def exec_command(self,command,timeout):
            self.commands.append(command)
            if len(self.commands)==1:
                output=b'{"loopback_only":true,"listeners":1}'
            else:
                if scenario!='zero_channels':
                    origin=('0.0.0.0' if scenario=='wrong_origin' else '127.0.0.1',9)
                    server=('0.0.0.0' if scenario=='wrong_server' else '127.0.0.1',
                        44001 if scenario=='wrong_port' else 44000)
                    transport.handler(Channel(),origin,server)
                    if scenario=='duplicate':
                        transport.handler(Channel(),origin,server)
                output=b'{"state":"OPERATOR_ASSISTED_NATIVE_PUSH_ACKNOWLEDGED"}'
            return Stream(),Stream(output),Stream()
        def close(self):self.closed=True
    connection=Connection();monkeypatch.setattr(desktop,'connect',lambda:connection)
    class Thread:
        def __init__(self,*,target,args,daemon):self.target=target
        def start(self):
            closure=dict(zip(self.target.__code__.co_freevars,
                (cell.cell_contents for cell in self.target.__closure__),strict=True))
            stats=closure['stats']
            stats.update(to_github_bytes=0 if scenario=='zero_outbound' else 10,
                from_github_bytes=0 if scenario=='zero_inbound' else 10)
            if scenario=='pump_error':stats['pump_error_types'].append('SyntheticError')
        def join(self,timeout):pass
        def is_alive(self):return scenario=='pump_alive'
    monkeypatch.setattr(desktop.threading,'Thread',Thread)
    monkeypatch.setattr(desktop.socket,'create_connection',
        lambda *a,**k:pytest.fail('actual socket forbidden'))
    monkeypatch.setattr(sys,'argv',['offline-desktop','push','--expected-plan-sha256','a'*64])
    with pytest.raises(ValueError,match='REAL_RELAY_NOT_ATTESTED_NO_RETRY_AFTER_PUSH'):
        desktop.main()
    assert connection.closed
    assert transport.cancelled==[('127.0.0.1',44000)]
    destinations=list((tmp_path/'cluster-evidence').iterdir())
    assert len(destinations)==1
    evidence=destinations[0]
    assert json.loads((evidence/'transport-attestation.json').read_text())['relay_attested'] is False
    assert (evidence/'result.stdout').read_bytes().startswith(b'{"state"')
    assert (evidence/'relay-safe-metadata.json').exists()
    assert len(connection.commands)==2  # one listener proof + one invocation, no retry

