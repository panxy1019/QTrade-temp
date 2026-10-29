from pathlib import Path
from hashlib import sha256
from datetime import datetime,timezone
import json
r=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/online-research/strategy-comparisons')
pointer=(r/'latest.json').read_bytes();assert len(pointer)<50000;p=json.loads(pointer);identity=p['comparison_id'];assert len(identity)==64 and all(x in '0123456789abcdef' for x in identity)
root=r/identity;raw=(root/'comparison.json').read_bytes();assert len(raw)<3000000;d=json.loads(raw);manifest=(root/'manifest.json').read_bytes();assert len(manifest)<1000000
print(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'root':str(root),'comparison_id':identity,'comparison_raw_sha256':sha256(raw).hexdigest(),'manifest_raw_sha256':sha256(manifest).hexdigest(),'pointer_raw_sha256':sha256(pointer).hexdigest(),'state':d['state'],'signal_start':d['signal_start'],'signal_end':d['signal_end'],'common_rows':d['common_rows'],'strategies':{k:{'metrics':v['metrics'],'signal_rows':v['signal_rows'],'allocation':v['allocation'],'curve_rows':len(v['curve'])} for k,v in d['strategies'].items()},'bindings':d['bindings'],'limitations':d['limitations'],'full_verify_comparison_this_turn':False,'read_only':True,'jobs_submitted':False},sort_keys=True))
