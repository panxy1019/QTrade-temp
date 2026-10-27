"""Reproduce current proposal admission on isolated SQLite backups; no production writes."""
import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from qtrade.domain import RuntimeMode
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_epoch import current_epoch
from qtrade.research.prediction_registry import active_approval
from scripts.run_qlib_forward_service import ForwardService, load, readonly

BASE = Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
PRODUCTION = Path('/root/qtrade-local/forward-research')
OPERATOR = Path('/root/qtrade-local/operator-evidence/forward-slot-ddebaa1-20261027')
REVISION = 'ddebaa1a5b1564c3dd641d9a33aa60a63cc4389a'
OUTPUT = OPERATOR / ('copy-replay-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ'))
assert not OUTPUT.exists() and not OUTPUT.is_relative_to(BASE) and not OUTPUT.is_relative_to(PRODUCTION)
assert os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() == 'false'
OUTPUT.mkdir()


def raw_hash(raw):
    return hashlib.sha256(raw).hexdigest()


def backup(source, dest):
    assert not dest.exists() and dest.is_relative_to(OUTPUT)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as original:
        with closing(sqlite3.connect(dest)) as copy:
            original.backup(copy)


def snapshot(root, roster):
    result = {}
    for identity in sorted(roster):
        book = root / identity
        row = {}
        for name, tables in [('forward.sqlite', ['config', 'targets', 'sessions']), ('operations.sqlite', ['fill_journal'])]:
            with closing(sqlite3.connect((book/name).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
                conn.execute('PRAGMA query_only=ON')
                for table in tables:
                    values = conn.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()
                    row[name + ':' + table] = {'rows':len(values), 'sha256':raw_hash(json.dumps(values, separators=(',',':'), default=str).encode())}
        row['admissions'] = {p.name:raw_hash(p.read_bytes()) for p in sorted((book/'admissions').glob('*.json'))}
        row['archive-link'] = raw_hash((book/'archive-link.json').read_bytes())
        result[identity] = row
    return result


attempt = {'kind':'ACTUAL_CURRENT_BOOKS_ISOLATED_PROPOSAL_REPLAY', 'revision':REVISION,
           'started_at':datetime.now(UTC).isoformat(), 'services_changed':False,
           'production_recovered':False, 'whole_step1_accepted':False, 'state':'RUNNING'}
try:
    roster_raw = (PRODUCTION/'roster.json').read_bytes()
    roster = load(PRODUCTION/'roster.json')['books']
    assert len(roster) == 14 and Path('/proc/175982').is_dir()
    original_before = snapshot(PRODUCTION, roster)
    copies = OUTPUT/'books'; copies.mkdir()
    (copies/'roster.json').write_bytes(roster_raw)
    for identity in roster:
        original = PRODUCTION/identity
        for name in ['forward.sqlite', 'operations.sqlite']:
            backup(original/name, copies/identity/name)
        for name in ['archive-link.json']:
            shutil.copyfile(original/name, copies/identity/name)
        admissions = copies/identity/'admissions'; admissions.mkdir()
        for p in (original/'admissions').glob('*.json'):
            shutil.copyfile(p, admissions/p.name)
    dbs = {'workflow':'/root/qtrade-local/workflow.sqlite',
           'models':'/root/qtrade-local/online-models.sqlite',
           'data':'/root/qtrade-local/qlib-versions.sqlite'}
    for name, source in dbs.items():
        backup(Path(source), OUTPUT/(name+'.sqlite'))
    copy_before = snapshot(copies, roster)
    assert copy_before == original_before
    # A byte-faithful backup carries the live owner's process lease. Detach only
    # these newly created copies; never clear or claim a production runtime.
    detached = []
    for identity in roster:
        path = copies/identity/'operations.sqlite'
        assert path.resolve().is_relative_to(OUTPUT) and not path.is_symlink()
        with sqlite3.connect(path) as conn:
            owners = conn.execute('SELECT process_id FROM process_owner').fetchall()
            assert owners == [(175982,)] and Path('/proc/175982').is_dir()
            conn.execute('DELETE FROM process_owner WHERE singleton=1')
            detached.append({'book_id':identity,'copied_owner_pid':175982})
    instance = ForwardService(BASE, copies, OUTPUT/'workflow.sqlite', OUTPUT/'models.sqlite',
                              OUTPUT/'data.sqlite', REVISION,
                              modes=(RuntimeMode.SHADOW, RuntimeMode.PAPER), initialize_paper=None)
    assert not instance.startup_errors
    assert all(not b.bundle.runtime.status.running for b in instance.books.values())
    targets = instance.current_targets(datetime.now(UTC))
    assert not instance.discovery_errors and len(targets) == 6
    for target, link in targets:
        instance.register(target, link, now=datetime.now(UTC))
    receipts = {p.relative_to(copies).as_posix():raw_hash(p.read_bytes())
                for p in copies.glob('*/rejected-targets/*.json')}
    assert len(receipts) == 12
    for target, link in targets:
        instance.register(target, link, now=datetime.now(UTC))
    assert receipts == {p.relative_to(copies).as_posix():raw_hash(p.read_bytes())
                        for p in copies.glob('*/rejected-targets/*.json')}
    assert snapshot(copies, roster) == copy_before
    assert all(not b.bundle.runtime.status.running for b in instance.books.values())
    for p in copies.glob('*/rejected-targets/*.json'):
        receipt = load(p)
        assert receipt['state'] == 'RETAINED_FIRST_ADMITTED_EXECUTION_TARGET'
        assert receipt['proposal_admitted'] is False
    with closing(readonly(Path(dbs['models']))) as original, closing(readonly(OUTPUT/'models.sqlite')) as copy:
        assert active_approval(original) == active_approval(copy)
    for target, link in targets:
        assert current_epoch(Path(dbs['data']), target.data_version) == link['data_event_id']
    original_after = snapshot(PRODUCTION, roster)
    assert original_after == original_before
    assert (PRODUCTION/'roster.json').read_bytes() == roster_raw and Path('/proc/175982').is_dir()
    # Do not call cycle()/close(): their publication destinations are intentionally production paths.
    for book in instance.books.values():
        book.db.close()
    proof = signed({**AUTHORITY, 'kind':attempt['kind'], 'source_revision':REVISION,
                    'verified_at':datetime.now(UTC).isoformat(), 'state':'PASSED_ISOLATED_COPY_REPLAY',
                    'roster_count':len(roster), 'proposal_families':len(targets),
                    'immutable_proposal_receipts':receipts, 'books_before':copy_before,
                    'frozen_rows_admissions_links_sessions_fills_unchanged':True,
                    'production_fingerprints_unchanged':True, 'owner_175982_unchanged':True,
                    'copied_books_recovered':False, 'publication_written':False,
                    'isolated_copy_process_leases_detached':detached,
                    'production_fix_deployed':False, 'whole_step1_accepted':False})
    (OUTPUT/'verification.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    attempt.update(state=proof['state'],verification_sha256=proof['evidence_sha256'])
except Exception as error:
    attempt.update(state='FAILED',error_type=type(error).__name__)
    raise
finally:
    attempt['finished_at'] = datetime.now(UTC).isoformat()
    (OUTPUT/'attempt.json').write_text(json.dumps(attempt),encoding='utf-8')
    print(json.dumps({'root':str(OUTPUT),'state':attempt['state'],'error_type':attempt.get('error_type')}))
