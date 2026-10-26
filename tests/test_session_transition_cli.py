from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime

import pytest
from test_qlib_public_delivery import NOW, REVISION, runtime  # noqa: F401
from test_session_acceptance import transition  # noqa: F401

from scripts import audit_qlib_session_transition as cli


@pytest.fixture
def command(runtime, tmp_path, monkeypatch):  # noqa: F811
    previous, current, completion, prediction, signals, admission = runtime.documents
    database = tmp_path/'data.sqlite'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE data_events(event_id TEXT,payload TEXT)')
        connection.execute('INSERT INTO data_events VALUES(?,?)',
                           (admission['evidence_sha256'], json.dumps(admission)))
    output = tmp_path/'acceptance.json'
    values = {'previous-version': tmp_path/'previous', 'current-version': tmp_path/'current',
        'automatic-completion': tmp_path/'job/completed.json', 'prediction': tmp_path/'prediction',
        'signals': tmp_path/'signals', 'database': database, 'output': output}
    args = ['session-audit']
    for name, value in values.items():
        args += ['--'+name, str(value)]
    monkeypatch.setattr(cli, 'verify_version', lambda path: previous if path.name == 'previous'
                        else current)
    monkeypatch.setattr(cli, 'verify_prediction', lambda _: prediction)
    monkeypatch.setattr(cli, 'verify_signal_archive', lambda _: {'manifest': signals})
    monkeypatch.setattr(cli, 'load_signed', lambda path: completion if path.name == 'completed.json'
        else {'previous_version': previous['version_id'], 'day': current['actual_end']})
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or UTC)
    monkeypatch.setattr(cli, 'datetime', Clock)
    def write(path, value):
        path.write_text(json.dumps(value))
    monkeypatch.setattr(cli, 'write_json_immutable', write)
    return args, output, runtime


def test_default_cli_remains_private_only_without_any_http(command, monkeypatch, capsys):
    args, output, _ = command
    monkeypatch.setattr(sys, 'argv', args)
    monkeypatch.setattr(cli, 'verify_public_delivery', lambda *_: pytest.fail('unexpected HTTP'))
    cli.main()
    capsys.readouterr()
    value = json.loads(output.read_bytes())
    assert value['production_publication_verified'] is False
    assert 'public_delivery' not in value


@pytest.mark.parametrize('flag', [['--verify-production'],
                                ['--expected-production-revision', REVISION]])
def test_production_flags_are_required_as_a_pair_before_verification(command, monkeypatch, flag):
    args, output, _ = command
    monkeypatch.setattr(sys, 'argv', args+flag)
    with pytest.raises(SystemExit):
        cli.main()
    assert not output.exists()


def test_only_passed_private_chain_and_public_delivery_can_mark_its_scope(command, monkeypatch):
    args, output, _ = command
    monkeypatch.setattr(sys, 'argv', args+['--verify-production',
                                         '--expected-production-revision', REVISION])
    cli.main()
    result = json.loads(output.read_bytes())
    assert result['production_publication_verified'] is True
    assert result['public_delivery']['production_publication_verified'] is True
    assert result['whole_step1_accepted'] is False and result['browser_verified'] is False


@pytest.mark.parametrize('failure', ['private', 'public'])
def test_failure_never_creates_a_passed_output(command, monkeypatch, failure):
    args, output, _ = command
    monkeypatch.setattr(sys, 'argv', args+['--verify-production',
                                         '--expected-production-revision', REVISION])
    if failure == 'private':
        monkeypatch.setattr(cli, 'assess_transition', lambda *a, **k: (_ for _ in ()).throw(
            ValueError('private lineage invalid')))
        monkeypatch.setattr(cli, 'verify_public_delivery', lambda *_: pytest.fail('premature HTTP'))
    else:
        monkeypatch.setattr(cli, 'verify_public_delivery', lambda *_: (_ for _ in ()).throw(
            ValueError('public delivery invalid')))
    with pytest.raises(ValueError):
        cli.main()
    assert not output.exists()


def test_cli_has_no_clock_or_origin_override(command, monkeypatch):
    args, output, _ = command
    for option in ('--now', '--base-url'):
        monkeypatch.setattr(sys, 'argv', args+[option, 'https://invalid.example'])
        with pytest.raises(SystemExit):
            cli.main()
    assert not output.exists()
