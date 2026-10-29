"""Read existing immutable collector responses and plan only; no source fetch or queue writer."""
import importlib.util
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.qlib_publication import verify_signed
from scripts.repair_incremental_gaps import _owner_available, repair
from scripts.unified_store import UnifiedBatchStore
from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES

BASE = Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
LOCAL = Path('/root/qtrade-local')
SOURCE = Path('/root/qtrade-local/operator-evidence/forward-handover-9846a7f-20261028/source')
REVISION = '9846a7f30774d6ae4b4d41f02b493c5246be87d9'
STARTED = datetime.now(UTC)
OUT = LOCAL/'operator-evidence'/('current-source-gaps-'+STARTED.strftime('%Y%m%dT%H%M%SZ'))
OUT.mkdir(exist_ok=False)
helper = importlib.util.spec_from_file_location('handover_read_helpers',
    LOCAL/'operator-evidence/forward-handover-9846a7f-20261028/directed_handover.py')
h = importlib.util.module_from_spec(helper)
helper.loader.exec_module(h)
DATABASE = LOCAL/'unified-data.sqlite'


def save_raw(name, value):
    with (OUT/name).open('xb') as stream:
        stream.write(value)


def save(name, value):
    save_raw(name, (json.dumps(value, sort_keys=True, indent=2)+'\n').encode())


assert os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() == 'false'
with closing(h.ro(DATABASE)) as db:
    db.row_factory = sqlite3.Row
    owner = dict(db.execute('SELECT * FROM process_owner WHERE singleton=1').fetchone())
    day = db.execute('SELECT max(trading_day) FROM collection_tasks').fetchone()[0]
    assert day == '2026-10-27'
    counts = [list(r) for r in db.execute('SELECT frequency,state,count(*) FROM collection_tasks '
        'WHERE trading_day=? GROUP BY frequency,state', (day,))]
    tasks = [dict(r) for r in db.execute("SELECT * FROM collection_tasks WHERE trading_day=? "
        "AND state='FAILED' ORDER BY frequency,symbol", (day,))]
    batches = []
    for task in tasks:
        row = db.execute("SELECT * FROM collection_batches WHERE substr(json_extract(spec,'$.target_end'),1,10)=? "
            "AND json_extract(spec,'$.frequency')=? AND EXISTS "
            "(SELECT 1 FROM json_each(json_extract(spec,'$.symbols')) WHERE value=?) "
            'ORDER BY completed_at DESC LIMIT 1', (day, task['frequency'], task['symbol'])).fetchone()
        assert row is not None
        batches.append(dict(row))
assert len(tasks) == 7
assert {(r['frequency'], r['symbol']) for r in tasks} == {
    ('day','002827'),('day','600621'),('day','605258'),
    ('5min','002827'),('5min','512580'),('5min','600621'),('5min','605258')}
process = Path('/proc')/str(owner['process_id'])
assert process.exists() and b'scripts.run_research_maintenance' in (process/'cmdline').read_bytes()
guard_code = None
try:
    _owner_available(DATABASE)
except ValueError as error:
    guard_code = str(error)
assert guard_code == 'REPAIR_CURRENT_COLLECTOR_OWNER_ALIVE'
store = UnifiedBatchStore(BASE/'unified-data/lake')
rows = []
for task, batch in zip(tasks, batches, strict=True):
    value = store.load(batch['batch_id'])
    verify_signed(value)
    assert value['evidence_sha256'] == batch['manifest_sha256']
    assert value['spec'] == json.loads(batch['spec'])
    assert value['outcomes'][task['symbol']]['state'] == 'FAILED'
    assert value['outcomes'][task['symbol']]['error_code'] == task['last_error']
    prefix = task['frequency']+'-'+task['symbol']+'-'+batch['batch_id']
    save_raw(prefix+'-manifest.json', store.batch_path(batch['batch_id']).read_bytes())
    save_raw(prefix+'-raw.jsonl.gz', store.read_blob(value['raw']))
    if value['normalized'] is not None:
        save_raw(prefix+'-normalized.parquet', store.read_blob(value['normalized']))
    captures = []
    for capture in store.read_captures(value['raw']):
        info = {k: capture[k] for k in ['source_id','request','received_at','status_code',
                                       'transport_error','body_complete','body_sha256']}
        info['body_bytes'] = len(capture['body'])
        if capture['source_id'] == 'sina_klines':
            data = json.loads(capture['body'])
            assert isinstance(data, list) and all(isinstance(x, dict) for x in data)
            days = sorted({str(x.get('day',''))[:10] for x in data})
            current = [x for x in data if str(x.get('day','')).startswith(day)]
            info.update(decoded_rows=len(data), dates=days, requested_day_rows=len(current))
            if task['frequency'] == '5min':
                expected = {t.isoformat(timespec='minutes') for t in SINA_FIVE_MINUTE_SESSION_TIMES}
                present = {datetime.fromisoformat(x['day']).strftime('%H:%M') for x in current}
                info['missing_requested_day_grid'] = sorted(expected-present)
                info['present_requested_day_grid'] = sorted(present)
        captures.append(info)
    rows.append({'task': task, 'batch_id': batch['batch_id'], 'batch_state': batch['state'],
        'batch_completed_at': batch['completed_at'], 'batch_manifest_sha256': value['evidence_sha256'],
        'collection_source_revision': value['spec']['code_revision'],
        'outcome': value['outcomes'][task['symbol']], 'captures': captures})
etf = next(r for r in rows if r['task']['symbol'] == '512580')
bars = next(c for c in etf['captures'] if c['source_id'] == 'sina_klines')
assert bars['requested_day_rows'] == 45 and bars['missing_requested_day_grid'] == ['10:00','11:30','14:30']
for row in rows:
    if row['task']['symbol'] != '512580':
        bars = next(c for c in row['captures'] if c['source_id'] == 'sina_klines')
        assert bars['requested_day_rows'] == 0 and max(bars['dates']) == '2026-10-23'
plan = repair(BASE, DATABASE, revision=REVISION, apply=False)
verify_signed(plan)
assert plan['state'] == 'PLAN' and plan['apply'] is False
source_files = {name: sha256((SOURCE/name).read_bytes()).hexdigest() for name in [
    'scripts/repair_incremental_gaps.py', 'scripts/unified_store.py',
    'src/qtrade/market_data/sina_minute.py', 'configs/observer_calendar_2026.json']}
receipt = signed({**AUTHORITY, 'kind': 'CURRENT_FAILED_TASK_RAW_SOURCE_REPLAY',
    'state': 'DIAGNOSIS_VERIFIED_SOURCE_GAPS_REMAIN', 'started_at': STARTED.isoformat(),
    'checked_at': datetime.now(UTC).isoformat(), 'trading_day': day, 'counts': counts,
    'failed_tasks_replayed': len(rows), 'source_revision': REVISION, 'source_files': source_files,
    'operator_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
    'maintenance_owner_pid': owner['process_id'], 'owner_process_confirmed_live': True,
    'apply_guard_code': guard_code, 'read_only_plan_candidates': plan['candidate_count'],
    'etf_symbol': '512580', 'etf_missing_times': ['10:00','11:30','14:30'],
    'etf_contiguous_completed_end': etf['task']['completed_end'],
    'stale_source_symbols': ['002827','600621','605258'], 'stale_source_last_day': '2026-10-23',
    'source_gap_repaired': False, 'queue_or_service_mutated': False,
    'suspension_or_no_trade_inferred': False, 'full_pool_sla_certified': False,
    'historical_pit_certified': False, 'whole_step1_accepted': False,
    'limitations': ['RAW_RESPONSE_REPLAY_NOT_INDEPENDENT_EXCHANGE_STATUS',
        'MISSING_GRID_IS_NOT_ZERO_VOLUME_OR_SUSPENSION', 'NO_FILL_FORWARD_OR_SOURCE_STITCHING',
        'READ_ONLY_PLAN_DOES_NOT_AUTHORIZE_APPLY_WHILE_OWNER_IS_ALIVE']})
save('verification.json', receipt)
save('failed-tasks.json', rows)
save('repair-plan.json', plan)
save_raw('operator-source.py', Path(__file__).read_bytes())
print(json.dumps({'output': str(OUT), 'state': receipt['state'], 'failed_tasks': len(rows),
    'etf_missing_times': receipt['etf_missing_times'], 'stale_source_last_day': '2026-10-23',
    'plan_candidates': plan['candidate_count'], 'apply': False}))
