"""Safe post-handover observation. No wrapper reads, signals, or Git mutation."""
import hashlib
import importlib.util
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
ATTEMPT = Path('/root/qtrade-local/operator-evidence/publisher-handover-fba51aad-20261028T113923Z')
TOOLS = ATTEMPT / 'v6-tools-origins-final'
OPERATOR_SHA = '0fd5100d029e6234039c765639b4ddb89094795918d6c7779ce199efe98f68f2'
BRIDGE_SHA = '689af7db40b33f804283068c3f6bd7756fafcd65160af674a2937b3c66c06bbf'
MANIFEST_SHA = '006f5c2b122795c699477aff984bf23deaf54a80224b9c7c6397153a6949b01e'
NEW_PID = 647839
NEW_TICKS = '1022098698'
OLD_PID = 294214


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load(name, path, expected):
    raw = path.read_bytes()
    assert sha(raw) == expected and not path.is_symlink()
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert sha(path.read_bytes()) == expected
    return module


def safe_json(path, max_bytes=4_000_000):
    if not path.is_file():
        return {'exists': False}
    raw = path.read_bytes()
    assert len(raw) <= max_bytes and not path.is_symlink()
    return {'exists': True, 'raw_sha256': sha(raw), 'bytes': len(raw), 'value': json.loads(raw)}


def process_scan(operator, old_root):
    matches = []
    failures = 0
    processes = {}
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmd = (p / 'cmdline').read_bytes()
            fields = (p / 'stat').read_text().rsplit(')', 1)[1].split()
            ppid = int(fields[1])
            processes[int(p.name)] = (p, cmd, fields)
            if old_root.encode() in cmd or ppid in (OLD_PID, NEW_PID):
                owner = operator.PublisherOnlyAdapter.owner(int(p.name))
                matches.append({'owner': owner, 'old_release_in_argv': old_root.encode() in cmd,
                    'private_shard_worker': b'--private-shard-worker' in cmd,
                    'publisher_module': b'scripts.publish_research_snapshot' in cmd})
        except (FileNotFoundError, ProcessLookupError):
            failures += 1
        except PermissionError:
            failures += 1
    descendants = {NEW_PID}
    for _ in range(32):
        added = {pid for pid, (_, _, fields) in processes.items() if int(fields[1]) in descendants}
        if added <= descendants:
            break
        descendants |= added
    stages = []
    for pid in sorted(descendants - {NEW_PID}):
        p, cmd, fields = processes[pid]
        args = [x for x in cmd.split(b'\0') if x]
        executable = Path(os.fsdecode(args[0])).name if args else ''
        stage = next((x.decode() for x in (b'pack-objects', b'index-pack', b'send-pack', b'push', b'commit', b'add') if x in args), None)
        if '--private-shard-worker'.encode() in args:
            stage = 'market_shard_worker'
        io = {}
        try:
            for line in (p / 'io').read_text().splitlines():
                k, v = line.split(':', 1)
                if k in ('rchar', 'wchar', 'syscr', 'syscw', 'read_bytes', 'write_bytes', 'cancelled_write_bytes'):
                    io[k] = int(v)
            wchan = (p / 'wchan').read_text().strip()
        except FileNotFoundError:
            continue
        stages.append({'pid': pid, 'ppid': int(fields[1]), 'pgrp': int(fields[2]),
            'session': int(fields[3]), 'start_ticks': fields[19], 'state': fields[0],
            'utime_ticks': int(fields[11]), 'stime_ticks': int(fields[12]),
            'executable_basename': executable, 'stage': stage, 'cmdline_sha256': sha(cmd),
            'io': io, 'wchan': wchan if re.fullmatch(r'[a-zA-Z0-9_]+', wchan) else None})
    return {'matches': matches, 'descendants': stages, 'raced_or_unreadable': failures,
        'scope': 'CURRENT_ARGV_OLD_RELEASE_AND_DIRECT_CHILDREN_NOT_HISTORICAL_CHILD_IDENTITIES'}


def current_context():
    from qtrade.research.prediction_registry import active_approval
    local = Path('/root/qtrade-local')
    connection = sqlite3.connect((local / 'online-models.sqlite').as_uri() + '?mode=ro', uri=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON'); connection.execute('BEGIN')
        approval = active_approval(connection)
        states = dict(connection.execute('SELECT model_id,state FROM models'))
        events = []
        for row in connection.execute("SELECT sequence,payload FROM model_events WHERE kind='CONTROL_APPLIED' ORDER BY sequence DESC LIMIT 6"):
            payload = json.loads(row['payload']); details = payload['details']
            events.append({'sequence': row['sequence'], 'payload_sha256': sha(row['payload'].encode()),
                **{key: details.get(key) for key in ('at', 'operation', 'model_id', 'previous_active',
                    'active_model', 'actor', 'state', 'evidence_sha256', 'command_sha256')}})
    finally:
        connection.close()
    connection = sqlite3.connect((local / 'qlib-versions.sqlite').as_uri() + '?mode=ro', uri=True)
    try:
        connection.execute('PRAGMA query_only=ON'); connection.execute('BEGIN')
        head = connection.execute("SELECT version_id FROM data_heads WHERE frequency='day'").fetchone()[0]
        event = connection.execute('SELECT event_id FROM data_events WHERE version_id=? ORDER BY sequence DESC LIMIT 1', (head,)).fetchone()[0]
    finally:
        connection.close()
    return {'active': approval['model_id'], 'approval_sha256': approval['evidence_sha256'],
        'data_version': head, 'data_event_id': event, 'model_states': states}, events


def logs(native, supervisor_pid):
    found = {}
    for role in ('stdout_logfile', 'stderr_logfile'):
        if native.get(role):
            found[role] = Path(native[role])
    for fd in (Path('/proc') / str(supervisor_pid) / 'fd').iterdir():
        try:
            name = os.readlink(fd)
            if name.startswith('/') and 'supervisor' in Path(name).name.lower() and '.log' in name:
                found['supervisor:' + Path(name).name] = Path(name)
        except FileNotFoundError:
            pass
    # Known standard location is read only if present; no configuration is read.
    known = Path('/var/log/supervisor/supervisord.log')
    if known.is_file():
        found['supervisor:supervisord.log'] = known
    result = {}
    for role, path in found.items():
        if not path.is_file() or path.is_symlink():
            continue
        info = path.stat()
        with path.open('rb') as handle:
            handle.seek(max(0, info.st_size - 8_000_000))
            raw = handle.read(8_000_001)
        assert len(raw) <= 8_000_000
        events = []
        for number, line in enumerate(raw.splitlines()):
            text = line.decode('utf-8', errors='replace')
            if role.startswith('supervisor:'):
                if 'qtrade-research-publication' not in text:
                    continue
                try:
                    fields = dict(word.split('=', 1) for word in shlex.split(text) if '=' in word)
                except ValueError:
                    fields = {}
                message = fields.get('msg', '')
                if re.fullmatch(r'(?:program stopped with error(?:: exit status [0-9]+)?|program exited|program started|process exited|process started|success to start program|start program|stop program)', message):
                    events.append({'line': number, 'line_sha256': sha(line),
                        'timestamp': fields.get('time'), 'event': message,
                        'numeric_fields': {k: int(v) for k, v in fields.items()
                            if re.fullmatch(r'[a-zA-Z_]+', k) and re.fullmatch(r'[0-9]+', v)},
                        'exit_status': fields.get('error') if re.fullmatch(r'exit status [0-9]+', fields.get('error', '')) else None,
                        'field_names': sorted(fields)})
                    continue
                # Only Supervisor-generated lifecycle text may leave this process.
                match = re.search(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+).*?(spawned: 'qtrade-research-publication' with pid \d+|success: qtrade-research-publication entered RUNNING state, process has stayed up for > than \d+ seconds \(startsecs\)|exited: qtrade-research-publication \((?:exit status \d+; (?:not )?expected|terminated by SIG[A-Z]+(?:; (?:not )?expected)?)\)|stopped: qtrade-research-publication \((?:exit status \d+|terminated by SIG[A-Z]+)\)|waiting for qtrade-research-publication to stop|killing 'qtrade-research-publication' \(\d+\) with a signal SIG[A-Z]+)", text)
                if match:
                    events.append({'line': number, 'utc_or_server_clock': match[1], 'event': match[2], 'line_sha256': sha(line)})
            elif text in ('PUBLICATION_SUCCEEDED', 'UNIFIED_MARKET_EXPORT_FAILED_RETAINING_LAST_SUCCESS', 'UNIFIED_MARKET_COMMIT_UNCERTAIN_STOPPED') or re.fullmatch(r'PUBLICATION_FAILED:[A-Za-z_][A-Za-z_0-9]*', text):
                events.append({'line': number, 'event': text, 'line_sha256': sha(line)})
            else:
                commit = re.fullmatch(r'\[[^\]\r\n]+ ([0-9a-f]{7,40})\] Publish research observation ([0-9T:+.\-Z]+)', text)
                push = re.fullmatch(r'\s*([0-9a-f]{7,40})\.\.([0-9a-f]{7,40})\s+HEAD -> main', text)
                if commit:
                    events.append({'line': number, 'event': 'PUBLICATION_COMMIT', 'commit_abbrev': commit[1], 'published_at': commit[2], 'line_sha256': sha(line)})
                elif push:
                    events.append({'line': number, 'event': 'PUSH_MAIN', 'from_abbrev': push[1], 'to_abbrev': push[2], 'line_sha256': sha(line)})
        result[role] = {'path_sha256': sha(str(path).encode()), 'bytes_at_stat': info.st_size,
            'mtime_ns': info.st_mtime_ns, 'tail_bytes': len(raw), 'tail_sha256': sha(raw),
            'tail_truncated': info.st_size > len(raw), 'events': events[-400:]}
    return result


def git(publication):
    result = {}
    commands = {'head': ['rev-parse', 'HEAD'], 'local_origin_main': ['rev-parse', 'refs/remotes/origin/main'],
        'status': ['status', '--porcelain=v1', '--untracked-files=no'],
        'recent_commits': ['log', '-16', '--format=%H%x09%cI'],
        'ahead_count': ['rev-list', '--count', 'refs/remotes/origin/main..HEAD'],
        'count_objects': ['count-objects', '-v'],
        'remote_main': ['ls-remote', '--refs', 'origin', 'refs/heads/main']}
    for role, args in commands.items():
        try:
            run = subprocess.run(['git', '-C', str(publication), *args], capture_output=True,
                env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'}, timeout=30)
            raw = run.stdout
            item = {'returncode': run.returncode, 'stdout_sha256': sha(raw), 'stderr_sha256': sha(run.stderr)}
            if role in ('head', 'local_origin_main') and re.fullmatch(rb'[0-9a-f]{40}\n', raw):
                item['revision'] = raw.decode().strip()
            elif role == 'remote_main' and re.fullmatch(rb'[0-9a-f]{40}\trefs/heads/main\n', raw):
                item['revision'] = raw.decode().split()[0]
            elif role == 'status':
                item.update(clean=not raw, changed_tracked_lines=len(raw.splitlines()))
            elif role == 'recent_commits':
                item['commits'] = [dict(zip(('revision', 'committer_time'), line.decode().split('\t')))
                    for line in raw.splitlines() if re.fullmatch(rb'[0-9a-f]{40}\t[0-9T:+\-Z]+', line)]
            elif role == 'ahead_count' and re.fullmatch(rb'[0-9]+\n', raw):
                item['count'] = int(raw)
            elif role == 'count_objects':
                item['values'] = {k.decode(): int(v) for k, v in
                    (line.split(b': ', 1) for line in raw.splitlines()) if re.fullmatch(rb'[a-z-]+', k) and v.isdigit()}
            result[role] = item
        except subprocess.TimeoutExpired:
            result[role] = {'timeout': True, 'seconds': 30}
    packroot = publication / '.git/objects/pack'
    result['pack_files'] = [{'filename': p.name, 'bytes': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns}
        for p in sorted(packroot.iterdir()) if p.is_file()
        and re.fullmatch(r'(?:pack-[0-9a-f]{40}\.(?:pack|idx|rev|bitmap|keep)|tmp_[a-zA-Z0-9_]+)', p.name)] if packroot.is_dir() else []
    return result


def main():
    assert os.environ.get('LIVE_TRADING_ENABLED') == 'false' and sys.dont_write_bytecode
    started = datetime.now(timezone.utc).isoformat()
    raw_manifest = (ATTEMPT / 'manifest.json').read_bytes()
    assert sha(raw_manifest) == MANIFEST_SHA
    manifest = json.loads(raw_manifest)
    release = BASE / 'releases' / manifest['new_revision']
    assert str(release) == manifest['release_root']
    # The reviewed read-only reader imports validators from the pinned release.
    sys.path[:0] = [str(release / 'src'), str(release)]
    operator = load('post_handover_operator', TOOLS / 'publisher_handover_v6_preparation_20261028.py', OPERATOR_SHA)
    bridge = load('post_handover_bridge', TOOLS / 'publisher_handover_bridge_v6_20261028.py', BRIDGE_SHA)
    sock = bridge.SOCKET.stat()
    rpc = bridge.UnixPublisherRPC({'device': sock.st_dev, 'inode': sock.st_ino, 'uid': sock.st_uid})
    native = rpc('supervisor.getProcessInfo', (bridge.PROGRAM,))
    before = operator.PublisherOnlyAdapter.owner(NEW_PID)
    assert before is not None and before['start_ticks'] == NEW_TICKS and not before['live_trading_enabled']
    early_process_scan = process_scan(operator, str(BASE / 'releases' / manifest['old_revision']))
    args = (Path('/proc') / str(NEW_PID) / 'cmdline').read_bytes().split(b'\0')
    def arg(flag):
        return Path(os.fsdecode(args[args.index(flag.encode()) + 1]))
    root, publication = arg('--root'), arg('--publication')
    assert root.is_relative_to(BASE) and publication.is_relative_to(BASE)
    reader = bridge.readonly_reader()
    context, approval_events = current_context()
    controls = {'owners': reader.controls(), 'frozen_controls': reader.frozen_controls(), 'context': context}
    proof = bridge.runtime_probe(operator, NEW_PID, manifest['new_revision'], manifest['runtime_files'])
    phases = []
    for p in sorted(ATTEMPT.glob('[0-9][0-9][0-9]-*.json')):
        raw = p.read_bytes(); value = json.loads(raw)
        assert value['sha256'] == operator.digest(value['body'])
        phases.append({'filename': p.name, 'raw_sha256': sha(raw), 'phase': value['body']['phase']})
    result = {'schema_version': 'qtrade.publisher-post-handover-readonly.v1', 'started_at': started,
        'native': {key: native[key] for key in ('name', 'pid', 'start', 'stop', 'now', 'state', 'statename', 'exitstatus')},
        'old_owner': operator.PublisherOnlyAdapter.owner(OLD_PID), 'new_owner_before': before,
        'controls': controls, 'approval_events': approval_events,
        'runtime_proof': {'source_revision': proof['source_revision'],
            'runtime_file_count': len(proof['runtime_files']), 'runtime_files_digest': operator.digest(proof['runtime_files']),
            'origins': proof['origin_resolution_files'], 'fixed_market_state_root_checked': proof['fixed_market_state_root_checked'],
            'live_trading_enabled': proof['live_trading_enabled'], 'scope': proof['import_proof_scope']},
        'journal': phases, 'early_process_scan': early_process_scan,
        'process_scan': process_scan(operator, str(BASE / 'releases' / manifest['old_revision'])),
        'logs': logs(native, before['ppid']), 'publication_state': safe_json(root / 'publication-state.json'),
        'market_export_attempt': safe_json(BASE / 'unified-data/market-export-attempt.json'),
        'market_export_state': safe_json(BASE / 'unified-data/market-export-state.json'),
        'git': git(publication), 'new_owner_after': operator.PublisherOnlyAdapter.owner(NEW_PID),
        'completed_at': datetime.now(timezone.utc).isoformat(), 'production_mutations': False,
        'private_wrapper_read': False, 'private_configuration_body_archived': False, 'control_rpc_calls': 0}
    assert result['new_owner_after'] == before
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
