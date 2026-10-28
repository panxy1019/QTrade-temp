import json,tempfile
from pathlib import Path
import pytest
from test_unified_market import source as fixture
from scripts import publish_unified_market as publisher

root=Path(__file__).parent
mp=pytest.MonkeyPatch()
try:
 with tempfile.TemporaryDirectory(dir=root/'tmp',prefix='public-uncertain-') as temporary:
  source=fixture.__wrapped__(Path(temporary),mp)
  base,database,*_,add=source;add()
  transport=base/'transport';state=base/'local/state'
  publisher.publish_market_files(base,transport,database,state_root=state)
  head=base/'unified-data/market-public/unified-market.json'
  old=json.loads(head.read_bytes())['evidence_sha256']
  add(counter=2,minute=40)
  original=publisher.write_json_atomic
  def fail_after_replace(path,value,**kwargs):
   original(path,value,**kwargs)
   if path==head:raise OSError('INDEPENDENT_PUBLIC_HEAD_DIRECTORY_FSYNC_UNCERTAIN')
  mp.setattr(publisher,'write_json_atomic',fail_after_replace)
  files=publisher.publish_market_files(base,transport,database,state_root=state)
  current=json.loads(head.read_bytes())['evidence_sha256']
  result={'old_head':old,'head_after':current,'head_moved':old!=current,
          'public_transport_head':json.loads((transport/'unified-market.json').read_bytes())['evidence_sha256'],
          'attempt':json.loads((base/'unified-data/market-export-attempt.json').read_bytes()),
          'returned_transport_files':len(files)}
  (root/'public-head-uncertainty-receipt.json').write_text(json.dumps(result,indent=2))
  print(json.dumps(result,indent=2))
finally:mp.undo()
