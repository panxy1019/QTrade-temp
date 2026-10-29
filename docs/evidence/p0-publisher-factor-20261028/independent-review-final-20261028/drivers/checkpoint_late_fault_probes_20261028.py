import json, tempfile
from pathlib import Path
from datetime import timedelta
import pytest
from test_unified_market import source as fixture, NOW
from scripts import publish_unified_market as publisher
from scripts import unified_market_checkpoint as checkpoints

R=Path(__file__).parent
results=[]
for mode in ('deadline_during_prepare','new_conflict_during_prepare'):
    mp=pytest.MonkeyPatch()
    try:
        with tempfile.TemporaryDirectory(dir=R/'tmp',prefix='late-probe-') as temporary:
            source=fixture.__wrapped__(Path(temporary),mp)
            base,database,*_,add=source
            add()
            options={'receipt_database':base/'local/receipts.sqlite',
                     'checkpoint_root':base/'checkpoints'}
            publisher.export_market(base,database,now=NOW,**options)
            pointer=base/'unified-data/market-public/unified-market.json'
            before=pointer.read_bytes();original=checkpoints.prepare
            clock=[100.0]
            if mode=='deadline_during_prepare':
                mp.setattr(publisher.time,'monotonic',lambda:clock[0])
            def race(*args,**kwargs):
                result=original(*args,**kwargs)
                if mode=='deadline_during_prepare':clock[0]+=2
                else:add(counter=2,close='11')
                return result
            mp.setattr(checkpoints,'prepare',race)
            observation={'mode':mode}
            try:
                result=publisher.export_market(base,database,now=NOW+timedelta(seconds=1),
                                               seconds=1 if mode=='deadline_during_prepare' else 45,
                                               **options)
            except Exception as error:
                observation.update(returned=False,error_type=type(error).__name__,error=str(error))
            else:
                pair=next(row for row in result['index']['entries']
                          if row['symbol']=='301001' and row['frequency']=='5min')
                observation.update(returned=True,state=pair['state'],statistics=result['statistics'])
            observation['head_unchanged']=pointer.read_bytes()==before
            if mode=='new_conflict_during_prepare':
                plain=publisher.export_market(base,database,now=NOW+timedelta(seconds=2))
                pair=next(row for row in plain['index']['entries']
                          if row['symbol']=='301001' and row['frequency']=='5min')
                observation['current_full_replay_state']=pair['state']
            results.append(observation)
    finally:mp.undo()
(R/'late-fault-receipt.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,indent=2))
