import json,os,runpy,sys
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
owned=Path(sys.argv[1]).resolve();events=[]
def local_path(value):
    if isinstance(value, int):
        return True
    return Path(value).resolve().is_relative_to(owned)

def guard(event, args):
    if event == 'open' and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
        assert local_path(args[0]), ('OUTSIDE_WRITE_BLOCKED', str(args[0]))
    if event in {'os.mkdir', 'os.remove', 'os.rmdir'}:
        assert local_path(args[0]), ('OUTSIDE_MUTATION_BLOCKED', event)
    if event in {'os.rename', 'os.link', 'os.symlink'}:
        assert local_path(args[0]) and local_path(args[1]), ('OUTSIDE_MUTATION_BLOCKED', event)
    if event == 'sqlite3.connect':
        name = str(args[0])
        read_only = name.startswith('file:') and parse_qs(urlsplit(name).query).get('mode') == ['ro']
        assert read_only or local_path(name), ('OUTSIDE_SQLITE_WRITE_BLOCKED', name)
        events.append({'event': event, 'read_only_uri': read_only, 'database': name})

sys.addaudithook(guard)
sys.argv=['scripts.run_factor_strategy_registration',*sys.argv[2:]]
try:
 runpy.run_module('scripts.run_factor_strategy_registration',run_name='__main__')
finally:
 (owned/'actual-child-write-guard.json').write_text(json.dumps({'sqlite':events,'writes_confined_to':str(owned)},indent=2)+'\n')
