import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

root = Path(__file__).parent
source = root / 'harness_f3495e4f.py'
assert hashlib.sha256(source.read_bytes()).hexdigest() == 'f3495e4f74d2407618d447491e9c2d062921c7f25ebb4dc7246e4cda8bd31126'
cases = ElementTree.parse(root / 'git-read-final-f3495-offline-v2.xml').findall('.//testcase')
assert len(cases) == 11 and all(not list(c) for c in cases)
files = {p.name: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size}
         for p in sorted(root.iterdir()) if p.is_file() and p.name != 'manifest.json'}
raw = (json.dumps({'at': datetime.now(timezone.utc).isoformat(), 'scope': 'OFFLINE_NARROW_GIT_READER_REVIEW',
                   'passed': 11, 'skipped': 0, 'network_used': False, 'production_changed': False,
                   'files': files}, sort_keys=True, indent=2) + '\n').encode()
with (root / 'manifest.json').open('xb') as f:
    f.write(raw)
print(json.dumps({'files': len(files), 'manifest_sha256': hashlib.sha256(raw).hexdigest()}))
