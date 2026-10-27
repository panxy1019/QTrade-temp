"""One bounded anonymous vendor read; never modifies historical checkpoints or admission."""
import importlib.metadata
import json
import os
import socket
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import baostock as bs

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.pit_quality import status_quality
from scripts.collect_pit_evidence import FIELDS, rows, verified_status
from scripts.vendor_session import baostock_session

BASE = Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
STARTED = datetime.now(UTC)
OUT = Path('/root/qtrade-local/operator-evidence')/('recent-status-probe-'+STARTED.strftime('%Y%m%dT%H%M%SZ'))
OUT.mkdir(exist_ok=False)
SOURCE = Path('/root/qtrade-local/operator-evidence/forward-handover-9846a7f-20261028/source')
assert os.environ.get('LIVE_TRADING_ENABLED','false').lower() == 'false'
calendar = Calendar(SOURCE/'configs/observer_calendar_2026.json')
end = calendar.expected(STARTED, 'daily').date().isoformat()
assert end == '2026-10-27'
dataset_path = BASE/'qlib-data-20261023/dataset.json'
dataset_bytes = dataset_path.read_bytes()
dataset = json.loads(dataset_bytes)
old = {}
for code in ['002827','600621','605258']:
    path = BASE/'pit-repair/status'/(code+'.json')
    value = verified_status(path,end=dataset['end'])
    old[code] = {'path':str(path),'file_sha256':sha256(path.read_bytes()).hexdigest(),
                 'observed_at':value['observed_at'],'last_date':value['rows'][-1]['date']}
state, observations = 'NOT_STARTED', []
socket.setdefaulttimeout(20)
try:
    with baostock_session(blocking=False):
        login = bs.login()
        if login.error_code != '0':
            raise ValueError('ANONYMOUS_VENDOR_LOGIN_FAILED')
        try:
            for symbol in old:
                request = {'code':('sh.' if symbol.startswith('6') else 'sz.')+symbol,
                    'fields':FIELDS,'start_date':'2026-10-26','end_date':end,
                    'frequency':'d','adjustflag':'3'}
                began = datetime.now(UTC).isoformat()
                result = bs.query_history_k_data_plus(**request)
                values = rows(result)
                captured = datetime.now(UTC)
                document = signed({**AUTHORITY,'kind':'RECENT_VENDOR_STATUS_SDK_OBSERVATION',
                    'source':'BaoStock raw daily status backfill','symbol':symbol,
                    'request':request,'started_at':began,'observed_at':captured.isoformat(),
                    'historical_available_at':None,'certified_at_decision':False,
                    'sdk_fields':result.fields,'sdk_error_code':str(result.error_code),
                    'rows':values,'raw_transport_bytes_captured':False,
                    'historical_pit_certified':False})
                with (OUT/(symbol+'.json')).open('x') as file:
                    json.dump(document,file,sort_keys=True,indent=2);file.write('\n')
                quality = status_quality(document,symbol,captured,start='2026-10-26',end=end) if values else None
                observations.append({'symbol':symbol,'rows':len(values),'dates':[v['date'] for v in values],
                    'vendor_trade_flags':[{k:v.get(k) for k in ['date','tradestatus','isST']} for v in values],
                    'quality':quality,'observation_sha256':document['evidence_sha256']})
            state = 'BOUNDED_READ_COMPLETE_NOT_ADMITTED'
        finally:
            bs.logout()
except BlockingIOError:
    state = 'VENDOR_LEASE_BUSY_NO_QUERY_SENT'
for code, meta in old.items():
    assert sha256(Path(meta['path']).read_bytes()).hexdigest() == meta['file_sha256']
assert dataset_path.read_bytes() == dataset_bytes
proof = signed({**AUTHORITY,'kind':'RECENT_PUBLIC_STATUS_WINDOW_PROBE',
    'state':state,'started_at':STARTED.isoformat(),'checked_at':datetime.now(UTC).isoformat(),
    'legacy_dataset_end':dataset['end'],'legacy_dataset_sha256':sha256(dataset_bytes).hexdigest(),
    'legacy_status_checkpoints':old,'legacy_files_unchanged':True,'requested_end':end,
    'requested_symbols':3,'observations':observations,'vendor_sdk_version':importlib.metadata.version('baostock'),
    'operator_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),
    'business_database_or_dataset_written':False,'raw_transport_bytes_captured':False,
    'current_exchange_state_certified':False,'historical_pit_certified':False,
    'new_admission_or_paper_authority':False,'whole_step1_accepted':False})
with (OUT/'verification.json').open('x') as file:
    json.dump(proof,file,sort_keys=True,indent=2);file.write('\n')
with (OUT/'operator-source.py').open('xb') as file:
    file.write(Path(__file__).read_bytes())
print(json.dumps({'output':str(OUT),'state':state,'legacy_end':dataset['end'],
    'results':[{k:r[k] for k in ['symbol','rows','dates','vendor_trade_flags']} for r in observations]}))
