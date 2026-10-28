import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

root = max(Path('E:/QTrade-temp').glob('factor-strategy-s2c-*'), key=lambda p: p.name)
source = Path('E:/QTrade-temp') / ('c-' + root.name[-8:]) / 's'
extract = not source.exists()
source.mkdir(parents=True, exist_ok=True)
prefix = 'windows-final'
manifest = json.loads((root / 'package-manifest.json').read_bytes())

def h(raw):
    return hashlib.sha256(raw).hexdigest()

assert shutil.disk_usage(root).free >= 2 * 1024 ** 3
for name, key in [('baseline.tar', 'baseline_sha256'), ('overlay.tar', 'overlay_sha256')]:
    assert h((root / name).read_bytes()) == manifest[key]
    with tarfile.open(root / name) as archive:
        for member in archive.getmembers():
            assert (member.isdir() or member.isfile()) and (source / member.name).resolve().is_relative_to(source.resolve())
        if extract:
            archive.extractall(source, filter='data')

def verify(name):
    actual = {p.relative_to(source).as_posix(): {'sha256': h(p.read_bytes()), 'bytes': p.stat().st_size}
              for p in source.rglob('*') if p.is_file()}
    (root / name).write_text(json.dumps({'matches': actual == manifest['expected_files'],
                                       'files': actual}, indent=2))
    assert actual == manifest['expected_files']

verify(prefix + '-source-before.json')
env = {**os.environ, 'PYTHONPATH': str(source / 'src') + os.pathsep + str(source),
       'LIVE_TRADING_ENABLED': 'false', 'PYTHONDONTWRITEBYTECODE': '1'}
cmd = [sys.executable, '-B', '-m', 'pytest', '-p', 'no:cacheprovider',
       '--basetemp=' + str(source.parent / 't2'), *manifest['tests'],
       '--junitxml=' + str(root / (prefix + '-regression.xml'))]
started = time.perf_counter()
with (root / (prefix + '-stdout.log')).open('w') as out, (root / (prefix + '-stderr.log')).open('w') as err:
    result = subprocess.run(cmd, cwd=source, env=env, stdout=out, stderr=err, timeout=180)
verify(prefix + '-source-after.json')
receipt = {'returncode': result.returncode, 'elapsed_seconds': time.perf_counter() - started,
           'command': cmd, 'python': sys.version, 'overlays': manifest['overlays']}
(root / (prefix + '-result.json')).write_text(json.dumps(receipt, indent=2))
print(str(root), flush=True)
print(json.dumps(receipt, indent=2), flush=True)
print((root / (prefix + '-stdout.log')).read_text()[-2500:], flush=True)
