"""Synthetic model metadata with real raw/Parquet/hash replay; not production evidence."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from test_collect_unified_data import batch, fake_source

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_versions import SCHEMA, content_id, file_hash
from qtrade.research.forward_prediction import inference_contract
from qtrade.research.rolling_policy import default_policy, plan
from qtrade.research.source_constraints import capture_source
from scripts import audit_qlib_candidate_transition as module
from scripts.collect_unified_data import collect_batch
from scripts.qlib_restore_bundle import BUNDLE_FILES
from scripts.unified_store import UnifiedBatchStore

NOW = datetime(2026, 10, 27, 10, tzinfo=UTC)
ACTIVE = 'a'*64


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def resign(value):
    return signed({key: item for key, item in value.items() if key != 'evidence_sha256'})


@pytest.fixture
def runtime(monkeypatch):
    directory = Path(module.REPO.anchor) if os.name == 'nt' else None
    with TemporaryDirectory(prefix='cand-', dir=directory) as temporary:
        root = Path(temporary)
        base = root/'base'
        calendar = Calendar({'valid_from': '2026-10-01', 'valid_through': '2026-10-31',
                             'weekday_closures': []})
        monkeypatch.setenv('LIVE_TRADING_ENABLED', 'false')
        monkeypatch.setattr('scripts.unified_store._fsync_directory', lambda _: None)
        if os.name == 'nt':
            writer = __import__('scripts.unified_store', fromlist=['write_json_immutable'])
            monkeypatch.setattr(writer, 'write_json_immutable', write)

        def version(day, previous=None):
            stamp = datetime(2026, 10, day, 9, tzinfo=UTC)

            def change(capture):
                capture['body'] = capture['body'].replace(b'2026-10-26', str(stamp.date()).encode())
                capture['received_at'] = stamp.isoformat()
                return capture

            source, _ = fake_source(change)
            store = UnifiedBatchStore(base/'unified-data/lake')
            receipt = collect_batch(store, batch(target=f'2026-10-{day}T15:00:00+08:00'),
                                    calendar, source=source)
            stage = base/f'stage-{day}'
            request = signed({**AUTHORITY, 'day': str(stamp.date()), 'universe_version': 'a'*64,
                              'batches': {'600036': receipt['batch_id']}})
            write(stage/'preparation-request.json', request)
            partition = signed({'symbol': '600036', 'end': str(stamp.date()),
                                'batch_sha256': receipt['evidence_sha256']})
            write(stage/'stocks/600036/manifest.json', partition)
            dataset = signed({**AUTHORITY, 'end': str(stamp.date()), 'accepted_symbols': 1,
                'stocks': [partition], 'incremental': {'current_symbols': ['600036'],
                    'blocked_symbols': [], 'observation_universe_version': 'a'*64},
                **({'parent_dataset_sha256': previous['evidence_sha256']} if previous else {})})
            write(stage/'dataset.json', dataset)
            days = []
            current = date(2017, 4, 5)
            while current <= stamp.date():
                if current.weekday() < 5:
                    days.append(str(current))
                current += timedelta(days=1)
            boundary = '2026-10-26' if day == 23 else '2026-10-27'
            for name in ('day.txt', 'day_future.txt'):
                path = stage/'provider/calendars'/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('\n'.join([*days, boundary])+'\n')
            for name in ('all.txt', 'workflow_supported.txt'):
                path = stage/'provider/instruments'/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('SH600036\t2017-04-05\t'+str(stamp.date())+'\n')
            files = {p.relative_to(stage).as_posix(): file_hash(p)
                     for p in stage.rglob('*') if p.is_file()}
            reload = signed({**AUTHORITY, 'schema_version': 'qtrade.qlib-provider-reload.v1',
                'dataset_sha256': files['dataset.json'], 'checked_current_symbols': 1,
                'provider_sha256': content_id({k: v for k, v in files.items()
                                              if k.startswith('provider/')}, {}),
                'actual_end': str(stamp.date()), 'checks_passed': True,
                'placeholder_all_missing': True, 'unknown_current_prices_missing': True})
            write(stage/'provider-reload.json', reload)
            files['provider-reload.json'] = file_hash(stage/'provider-reload.json')
            bindings = {'preparation_sha256': request['evidence_sha256']}
            identity = content_id(files, bindings)
            value = signed({**AUTHORITY, 'schema_version': SCHEMA, 'version_id': identity,
                'state': 'RESEARCH_READY_QUALIFIED_SUBSET', 'frequency': 'day',
                'historical_pit_certified': False, 'external_execution_ready': False,
                'created_at': (stamp+timedelta(minutes=5)).isoformat(),
                'actual_end': str(stamp.date()), 'current_symbols': ['600036'],
                'historical_symbols': ['600036'], 'blocked_symbols': [],
                'calendar_boundaries': [boundary], 'files': files, 'bindings': bindings})
            write(stage/'version.json', value)
            target = base/'unified-data/qlib-versions/versions'/identity
            target.parent.mkdir(parents=True, exist_ok=True)
            stage.rename(target)
            return target, value, dataset, receipt

        trained_root, trained, trained_data, training_receipt = version(23)
        later_root, later, current, later_receipt = version(26, trained_data)
        archive = base/'archives/candidate'
        snapshot = capture_source(None)
        snapshot.freeze(archive/'pit-sources')
        request = signed({'config': {'name': 'rolling-alpha158-lightgbm'},
            'source_revision': 'b'*40,
            'data_manifest_sha256': file_hash(trained_root/'dataset.json'),
            'source_snapshot_sha256': snapshot.identity})
        run_id = request['config']['name']+'-'+request['evidence_sha256'][:16]
        write(archive/'request.json', request)
        shutil.copyfile(trained_root/'dataset.json', archive/'data-manifest.json')
        shutil.copytree(trained_root/'provider', archive/'provider')
        for name in ('scripts/run_qlib_baseline.py', 'scripts/run_qlib_rolling.py',
                     'scripts/qlib_restore_bundle.py'):
            path = archive/'source'/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# Synthetic source identity fixture; never executed.\n')
        bundle_root = archive/'online-restore'
        bundle_root.mkdir()
        for name in BUNDLE_FILES:
            (bundle_root/name).write_bytes(b'No executable pickle is deserialized by the auditor')
        bundle = signed({**AUTHORITY, 'schema_version': 'qtrade.qlib-restore-bundle.v1',
            'code_revision': 'b'*40, 'data_sha256': file_hash(archive/'data-manifest.json'),
            'source_snapshot_sha256': snapshot.identity,
            'feature_order': [f'f{n:03}' for n in range(158)], 'feature_dtypes': ['float64']*158,
            'handler_start': '2017-04-05', 'reference_start': '2026-10-22',
            'reference_end': '2026-10-23', 'reference_rows': 2,
            'files': {name: file_hash(bundle_root/name) for name in BUNDLE_FILES},
            'provider_files': {p.relative_to(archive/'provider').as_posix(): file_hash(p)
                               for p in (archive/'provider').rglob('*') if p.is_file()},
            'model_activated': False, 'historical_pit_certified': False})
        write(bundle_root/'manifest.json', bundle)
        model_id = bundle['evidence_sha256']
        restore = signed({**AUTHORITY, 'state': 'PARITY_VERIFIED_NOT_ACTIVATED',
            'bundle_sha256': model_id, 'fitted_processors_unchanged': True,
            'fit_methods_forbidden': True, 'model_activated': False,
            'verified_at': '2026-10-23T09:20:00+00:00', 'max_absolute_score_difference': 0.0,
            'feature_count': 158, **{key: bundle[key] for key in (
                'reference_rows', 'reference_start', 'reference_end', 'handler_start')}})
        write(archive/'independent-restore.json', restore)
        rolling = plan({**default_policy(), 'train_sessions': 252, 'valid_sessions': 60,
                        'test_sessions': 20},
                       (trained_root/'provider/calendars/day.txt').read_text().splitlines(),
                       trained['actual_end'], data_version_id=trained['version_id'],
                       source_revision='b'*40)
        summary = signed({**AUTHORITY, 'run_id': run_id, 'model': 'Alpha158 + LightGBM',
            'feature_count': 158, 'data_end': trained['actual_end'], 'source_revision': 'b'*40,
            'dataset_sha256': bundle['data_sha256'], 'source_snapshot_sha256': snapshot.identity,
            'completed_at': '2026-10-23T09:30:00+00:00', 'rolling_plan': rolling})
        write(archive/'summary.json', summary)
        write(archive/'checksums.json', signed({'run_id': run_id, 'files': {
            p.relative_to(archive).as_posix(): file_hash(p)
            for p in archive.rglob('*') if p.is_file()}}))
        definition = signed({**AUTHORITY, 'model_id': model_id, 'model_run_id': run_id,
            'schema_version': 'qtrade.online-model.v1', 'feature_count': 158,
            'training_data_end': summary['data_end'],
            'training_data_sha256': summary['dataset_sha256'],
            'training_code_revision': 'b'*40, 'training_completed_at': summary['completed_at'],
            'restore_proof_sha256': restore['evidence_sha256'],
            'source_snapshot_sha256': snapshot.identity, 'fitted_restore_verified': True,
            'historical_pit_certified': False})
        write(base/'online-research/control/models'/(model_id+'.json'), signed({**AUTHORITY,
            'model_id': model_id, 'archive': 'archives/candidate', 'definition': definition}))
        bindings = {'kind': 'QLIB_MODEL_VALIDATION', 'model_id': model_id,
                    'data_version_id': later['version_id'], 'source_revision': 'd'*40}
        job_id = signed(bindings)['evidence_sha256'][:32]
        job_request = signed({**AUTHORITY, **bindings, 'job_id': job_id})
        folder = base/'workflow/jobs'/job_id
        write(folder/'request.json', job_request)
        contract = inference_contract(bundle, trained_data, current, later, calendar,
                                       now=datetime(2026, 10, 26, 9, 12, tzinfo=UTC))
        proof_bindings = {'model': model_id, 'data': later['version_id'], 'code': {}}
        partial = root/'validation'
        for name in ('scripts/validate_qlib_forward.py', 'scripts/qlib_restore_bundle.py',
                     'src/qtrade/research/forward_prediction.py'):
            path = partial/'source'/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# Synthetic source identity fixture; never executed.\n')
            proof_bindings['code'][name] = file_hash(path)
        validation_id = signed(proof_bindings)['evidence_sha256']
        write(partial/'request.json', contract)
        pq.write_table(pa.Table.from_pylist([{'instrument': 'SH600036', 'score': .2, 'rank': 1}]),
                       partial/'scores.parquet')
        pq.write_table(pa.Table.from_pylist([{**{name: .1 for name in bundle['feature_order']},
            'datetime': datetime(2026, 10, 26), 'instrument': 'SH600036'}]),
            partial/'features.parquet')
        proof = signed({**AUTHORITY, 'schema_version': 'qtrade.qlib-forward-validation.v1',
            'state': 'FORWARD_INPUT_VALIDATED_NOT_ACTIVATED', 'validation_id': validation_id,
            'bindings': proof_bindings, 'contract': contract, 'model_bundle_sha256': model_id,
            'model_run_id': run_id, 'feature_count': 158, 'score_count': 1,
            'fitted_processors_unchanged': True, 'fit_methods_forbidden': True,
            'model_activated': False, 'auto_predict_enabled': False,
            'max_reference_score_difference': 0.0, 'historical_reference_rows': 2,
            'files': {p.relative_to(partial).as_posix(): file_hash(p)
                      for p in partial.rglob('*') if p.is_file()}})
        write(partial/'manifest.json', proof)
        proof_root = folder/'output/validations'/validation_id
        proof_root.parent.mkdir(parents=True)
        partial.rename(proof_root)
        write(folder/'output/result.json', proof)
        write(base/'online-research/control/validations'/(validation_id+'.json'), signed({
            **AUTHORITY, 'model_id': model_id, 'validation_id': validation_id,
            'archive': proof_root.relative_to(base).as_posix(),
            'manifest_sha256': proof['evidence_sha256']}))
        command = {'request_id': '1'*32, 'model_id': ACTIVE, 'operation': 'ACTIVATE',
                   'actor': 'fixture-owner', 'expected_active': None,
                   'data_version_id': trained['version_id'], 'validation_id': 'e'*64}
        approval = signed({**AUTHORITY, 'request_id': command['request_id'], 'model_id': ACTIVE,
            'active_model': ACTIVE, 'state': 'APPLIED', 'at': '2026-10-22T10:00:00+00:00',
            'command_sha256': signed(command)['evidence_sha256']})
        active_definition = signed({**AUTHORITY, 'model_id': ACTIVE,
            'schema_version': 'qtrade.online-model.v1', 'fitted_restore_verified': True,
            'historical_pit_certified': False})
        models, workflow, data = (root/'models.sqlite', root/'workflow.sqlite', root/'data.sqlite')
        with closing(sqlite3.connect(models)) as connection, connection:
            connection.executescript('''
                CREATE TABLE models(model_id TEXT,definition TEXT,state TEXT,registered_at TEXT,
                                    activated_at TEXT,activation_count INTEGER);
                CREATE TABLE active_model(singleton INTEGER,model_id TEXT);
                CREATE TABLE model_events(sequence INTEGER PRIMARY KEY,model_id TEXT,at TEXT,
                                          kind TEXT,payload TEXT);
                CREATE TABLE model_commands(request_id TEXT,command TEXT,result TEXT);
                CREATE TABLE model_validations(validation_id TEXT,model_id TEXT,
                                               data_version_id TEXT,proof TEXT);
            ''')
            connection.executemany('INSERT INTO models VALUES(?,?,?,?,?,?)', [
                (model_id, json.dumps(definition), 'STAGED', '2026-10-23T09:40:00+00:00', None, 0),
                (ACTIVE, json.dumps(active_definition), 'ACTIVE', '2026-10-22T09:00:00+00:00',
                 approval['at'], 1)])
            connection.execute('INSERT INTO active_model VALUES(1,?)', (ACTIVE,))
            connection.execute('INSERT INTO model_commands VALUES(?,?,?)',
                (command['request_id'], json.dumps(command), json.dumps(approval)))
            events = [(ACTIVE, '2026-10-22T09:00:00+00:00', 'TRAINED',
                       {'definition_sha256': active_definition['evidence_sha256']}),
                (ACTIVE, approval['at'], 'CONTROL_APPLIED', approval),
                (model_id, '2026-10-23T09:40:00+00:00', 'TRAINED',
                 {'definition_sha256': definition['evidence_sha256']})]
            events += [(model_id, '2026-10-26T09:13:00+00:00', state,
                        {'imported_verified_validation': validation_id})
                       for state in ('VALIDATING', 'VALIDATED', 'STAGED')]
            for index, (identity, at, kind, details) in enumerate(events, 1):
                payload = signed({**AUTHORITY, 'model_id': identity, 'at': at,
                                  'kind': kind, 'details': details})
                connection.execute('INSERT INTO model_events VALUES(?,?,?,?,?)',
                    (index, identity, at, kind, json.dumps(payload)))
            connection.execute('INSERT INTO model_validations VALUES(?,?,?,?)',
                               (validation_id, model_id, later['version_id'], json.dumps(proof)))
        with closing(sqlite3.connect(workflow)) as connection, connection:
            connection.executescript(
                'CREATE TABLE jobs(id TEXT,request TEXT,state TEXT,updated TEXT,'
                'result TEXT,error TEXT); CREATE TABLE events(id TEXT,at TEXT,state TEXT);')
            connection.execute('INSERT INTO jobs VALUES(?,?,?,?,?,NULL)',
                (job_id, json.dumps(job_request), 'COMPLETE', '2026-10-26T09:14:00+00:00',
                 json.dumps({'validation_id': validation_id, 'model_id': model_id,
                             'data_version_id': later['version_id']})))
            connection.executemany('INSERT INTO events VALUES(?,?,?)',
                [(job_id, at, state) for state, at in (
                    ('QUEUED', '2026-10-26T09:10:00+00:00'),
                    ('RUNNING', '2026-10-26T09:11:00+00:00'),
                    ('COMPLETE', '2026-10-26T09:14:00+00:00'))])
        with closing(sqlite3.connect(data)) as connection, connection:
            connection.executescript(
                'CREATE TABLE data_versions(version_id TEXT,manifest_sha256 TEXT,'
                'actual_end TEXT,admitted_at TEXT); CREATE TABLE data_heads(frequency TEXT,'
                'version_id TEXT); CREATE TABLE data_events('
                'sequence INTEGER PRIMARY KEY,event_id TEXT,'
                'version_id TEXT,kind TEXT,payload TEXT);')
            connection.execute("INSERT INTO data_heads VALUES('day',?)", (later['version_id'],))
            for index, value in enumerate((trained, later), 1):
                at = value['actual_end']+'T09:06:00+00:00'
                payload = signed({**AUTHORITY, 'kind': 'QLIB_DATA_READY',
                    'version_id': value['version_id'], 'actual_end': value['actual_end'],
                    'manifest_sha256': value['evidence_sha256'], 'admitted_at': at})
                connection.execute('INSERT INTO data_versions VALUES(?,?,?,?)',
                    (value['version_id'], value['evidence_sha256'], value['actual_end'], at))
                connection.execute('INSERT INTO data_events VALUES(?,?,?,?,?)',
                    (index, payload['evidence_sha256'], value['version_id'],
                     payload['kind'], json.dumps(payload)))

        def run(now=NOW):
            return module.audit(base, models, workflow, data, model_id, later['version_id'],
                                ACTIVE, approval['evidence_sha256'], calendar=calendar, now=now)

        yield SimpleNamespace(base=base, models=models, workflow=workflow, data=data, run=run,
            model_id=model_id, later=later, trained=trained, proof=proof, folder=folder,
            approval=approval, proof_root=proof_root, archive=archive, calendar=calendar,
            training_receipt=training_receipt, later_receipt=later_receipt)


def test_actual_sqlite_and_raw_parquet_replay_verify_candidate_without_activation(runtime):
    before = {path: path.read_bytes() for path in (runtime.models, runtime.workflow, runtime.data)}
    value = runtime.run()
    assert value['errors'] == [], value['errors']
    assert value['state'] == 'VERIFIED_LATER_INPUT_CANDIDATE_NOT_ACTIVATED'
    assert value['evidence']['training_origin'] == 'UNKNOWN'
    assert value['evidence']['training_origin_certified'] is False
    assert value['evidence']['training_job_verified'] is False
    assert value['evidence']['later_raw_day']['symbols'] == 1
    assert all(value['checks'].values())
    assert value['whole_step1_accepted'] is False and value['automatic_training_accepted'] is False
    assert value['model_activated_by_auditor'] is False
    assert value['clock_source'] == 'CONTROLLED_TEST_ONLY'
    assert before == {path: path.read_bytes() for path in before}
    assert runtime.run() == value


@pytest.mark.parametrize(('table', 'sql', 'parameters', 'code'), [
    ('models', "UPDATE models SET state='ACTIVE' WHERE model_id=?", 'model', 'UNACTIVATED_STAGED'),
    ('models', 'DELETE FROM model_validations', None, 'UNIQUE_LATER_VALIDATION'),
    ('workflow', "UPDATE jobs SET state='RUNNING'", None, 'JOB_NOT_COMPLETE'),
    ('workflow', "DELETE FROM events WHERE state='RUNNING'", None, 'JOB_EVENT_CHAIN'),
    ('models', 'UPDATE models SET activation_count=1 WHERE model_id=?',
     'model', 'UNACTIVATED_STAGED'),
    ('data', "UPDATE data_versions SET manifest_sha256='changed'", None, 'CATALOG_BINDING'),
])
def test_pending_failed_or_mismatched_ledgers_cannot_pass(runtime, table, sql, parameters, code):
    with closing(sqlite3.connect(getattr(runtime, table))) as connection, connection:
        connection.execute(sql, (runtime.model_id,) if parameters else ())
    value = runtime.run()
    assert value['state'] == 'BLOCKED' and any(code in error for error in value['errors'])


def test_future_later_session_never_passes(runtime):
    value = runtime.run(now=datetime(2026, 10, 25, 10, tzinfo=UTC))
    assert value['state'] == 'BLOCKED'
    assert 'LATER_SESSION_NOT_COMPLETE' in value['errors'][0]


def test_raw_receipt_corruption_blocks_without_repair(runtime):
    store = UnifiedBatchStore(runtime.base/'unified-data/lake')
    path = store._blob_path(runtime.later_receipt['raw']['sha256'], '.jsonl.gz')
    path.write_bytes(b'corrupt')
    before = path.read_bytes()
    value = runtime.run()
    assert value['state'] == 'BLOCKED'
    assert path.read_bytes() == before


def test_validation_artifact_tamper_and_fake_parquet_cannot_pass(runtime):
    (runtime.proof_root/'scores.parquet').write_bytes(b'not actual scores')
    assert runtime.run()['state'] == 'BLOCKED'


def test_same_active_head_with_new_approval_is_not_unchanged(runtime):
    with closing(sqlite3.connect(runtime.models)) as connection, connection:
        row = connection.execute(
            "SELECT * FROM model_events WHERE kind='CONTROL_APPLIED'").fetchone()
        value = json.loads(row[-1])
        value['details']['at'] = '2026-10-26T09:13:00+00:00'
        value['details'] = resign(value['details'])
        value['at'] = value['details']['at']
        value = resign(value)
        connection.execute('INSERT INTO model_events VALUES(?,?,?,?,?)',
            (7, ACTIVE, value['at'], 'CONTROL_APPLIED', json.dumps(value)))
    assert 'ACTIVE_APPROVAL_CHANGED' in runtime.run()['errors'][0]


def test_metadata_change_during_archive_replay_is_detected(runtime, monkeypatch):
    original = module.raw_day
    changed = [False]

    def race(*args):
        value = original(*args)
        if not changed[0]:
            with closing(sqlite3.connect(runtime.workflow)) as connection, connection:
                connection.execute("UPDATE jobs SET updated='2026-10-26T09:15:00+00:00'")
            changed[0] = True
        return value

    monkeypatch.setattr(module, 'raw_day', race)
    assert 'CRITICAL_SNAPSHOT_CHANGED' in runtime.run()['errors'][0]


def test_auditor_does_not_construct_writers_fit_or_run_jobs(runtime, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Read-only audit attempted a writer or execution constructor')

    monkeypatch.setattr('qtrade.research.model_registry.ModelRegistry.__init__', forbidden)
    monkeypatch.setattr('qtrade.research.data_versions.DataVersionCatalog.__init__', forbidden)
    monkeypatch.setattr('scripts.online_model_control.OnlineModels.__init__', forbidden)
    monkeypatch.setattr('scripts.qlib_restore_bundle.FrozenRecorder.load_object', forbidden)
    assert runtime.run()['errors'] == []


def test_cli_has_no_time_or_action_override(capsys):
    with pytest.raises(SystemExit) as error:
        module.main(['--help'])
    assert error.value.code == 0
    value = capsys.readouterr().out
    assert '--now' not in value and '--activate' not in value and '--train' not in value


def test_readonly_connections_are_short_query_only_snapshots(runtime, monkeypatch):
    original = module.readonly
    connections = []

    def track(path):
        connection = original(path)
        assert connection.execute('PRAGMA query_only').fetchone()[0] == 1
        connections.append(connection)
        return connection

    monkeypatch.setattr(module, 'readonly', track)
    raw = module.raw_day

    def verify_closed(*args):
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match='closed'):
                connection.execute('SELECT 1')
        return raw(*args)

    monkeypatch.setattr(module, 'raw_day', verify_closed)
    assert runtime.run()['errors'] == []
    assert len(connections) == 6


def test_future_validation_job_event_is_not_accepted(runtime):
    with closing(sqlite3.connect(runtime.workflow)) as connection, connection:
        connection.execute(
            "UPDATE events SET at='2026-10-28T09:14:00+00:00' WHERE state='COMPLETE'")
    assert 'FUTURE_OR_NAIVE_CLOCK' in runtime.run()['errors'][0]


def test_same_training_version_cannot_substitute_for_later_version(runtime):
    value = module.audit(runtime.base, runtime.models, runtime.workflow, runtime.data,
        runtime.model_id, runtime.trained['version_id'], ACTIVE,
        runtime.approval['evidence_sha256'], calendar=runtime.calendar, now=NOW)
    assert 'DATA_NOT_GENUINELY_LATER' in value['errors'][0]


def test_missing_validation_job_is_not_recreated(runtime):
    with closing(sqlite3.connect(runtime.workflow)) as connection, connection:
        connection.execute('DELETE FROM jobs')
    assert 'UNIQUE_LATER_VALIDATION' in runtime.run()['errors'][0]
    with closing(sqlite3.connect(runtime.workflow)) as connection:
        assert connection.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0


def test_training_artifact_tamper_blocks_without_overwrite(runtime):
    path = runtime.archive/'online-restore/params.pkl'
    path.write_bytes(b'corrupt model artifact')
    assert runtime.run()['state'] == 'BLOCKED'
    assert path.read_bytes() == b'corrupt model artifact'


def test_claimed_state_transition_clock_before_validation_is_rejected(runtime):
    with closing(sqlite3.connect(runtime.models)) as connection, connection:
        for sequence in (4, 5, 6):
            payload = json.loads(connection.execute(
                'SELECT payload FROM model_events WHERE sequence=?', (sequence,)).fetchone()[0])
            payload['at'] = '2026-10-26T09:00:00+00:00'
            connection.execute('UPDATE model_events SET at=?,payload=? WHERE sequence=?',
                (payload['at'], json.dumps(resign(payload)), sequence))
    assert 'VALIDATION_STAGE_CLOCK_INVALID' in runtime.run()['errors'][0]


def test_active_full_definition_and_count_change_during_replay_is_detected(runtime, monkeypatch):
    original = module.raw_day
    changed = [False]

    def race(*args):
        value = original(*args)
        if not changed[0]:
            with closing(sqlite3.connect(runtime.models)) as connection, connection:
                connection.execute('UPDATE models SET definition=?,activation_count=999 '
                                   'WHERE model_id=?', ('{"changed":true}', ACTIVE))
            changed[0] = True
        return value

    monkeypatch.setattr(module, 'raw_day', race)
    assert 'CRITICAL_SNAPSHOT_CHANGED_DURING_REPLAY' in runtime.run()['errors'][0]


def test_unsigned_fake_training_job_cannot_upgrade_training_origin(runtime):
    summary = json.loads((runtime.archive/'summary.json').read_bytes())
    fake = {'kind': 'QLIB_ROLLING_JOB', 'plan': summary['rolling_plan']}
    identity = 'f'*32
    with closing(sqlite3.connect(runtime.workflow)) as connection, connection:
        connection.execute('INSERT INTO jobs VALUES(?,?,?,?,?,NULL)',
            (identity, json.dumps(fake), 'COMPLETE', '2026-10-23T09:35:00+00:00',
             json.dumps({'run_id': summary['run_id']})))
        connection.executemany('INSERT INTO events VALUES(?,?,?)',
            [(identity, '2026-10-23T09:35:00+00:00', state)
             for state in ('QUEUED', 'RUNNING', 'COMPLETE')])
    value = runtime.run()
    assert value['errors'] == []
    assert value['evidence']['training_origin'] == 'UNKNOWN'
    assert value['evidence']['training_origin_certified'] is False
    assert value['evidence']['training_job_verified'] is False
    assert value['automatic_training_accepted'] is False


def test_uncontrolled_audit_uses_system_clock_marker(runtime):
    value = module.audit(runtime.base, runtime.models, runtime.workflow, runtime.data,
        runtime.model_id, runtime.later['version_id'], ACTIVE, runtime.approval['evidence_sha256'],
        calendar=runtime.calendar)
    assert value['clock_source'] == 'SYSTEM_UTC'
    assert value['current_serving_admission_verified'] is False


def test_future_active_registration_even_with_matching_resigned_event_is_rejected(runtime):
    future = '2030-01-01T00:00:00+00:00'
    with closing(sqlite3.connect(runtime.models)) as connection, connection:
        payload = json.loads(connection.execute('SELECT payload FROM model_events '
            "WHERE model_id=? AND kind='TRAINED'", (ACTIVE,)).fetchone()[0])
        payload['at'] = future
        connection.execute('UPDATE models SET registered_at=? WHERE model_id=?', (future, ACTIVE))
        connection.execute('UPDATE model_events SET at=?,payload=? '
            "WHERE model_id=? AND kind='TRAINED'", (future, json.dumps(resign(payload)), ACTIVE))
    assert 'FUTURE_OR_NAIVE_CLOCK' in runtime.run()['errors'][0]


def test_preexisting_active_counter_must_match_applied_control_history(runtime):
    with closing(sqlite3.connect(runtime.models)) as connection, connection:
        connection.execute('UPDATE models SET activation_count=999 WHERE model_id=?', (ACTIVE,))
    assert 'ACTIVE_CONTROL_COUNT_MISMATCH' in runtime.run()['errors'][0]
