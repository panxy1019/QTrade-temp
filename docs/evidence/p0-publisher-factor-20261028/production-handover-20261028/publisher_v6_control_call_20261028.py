"""One approved publisher-only advance, or observation; never retries RPC."""
import argparse
import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ATTEMPT=Path('/root/qtrade-local/operator-evidence/publisher-handover-fba51aad-20261028T113923Z')
TOOLS=ATTEMPT/'v6-tools-origins-final'
# Replaced with the independently reviewed final tool hashes before any upload.
OPERATOR_SHA='0fd5100d029e6234039c765639b4ddb89094795918d6c7779ce199efe98f68f2'
BRIDGE_SHA='689af7db40b33f804283068c3f6bd7756fafcd65160af674a2937b3c66c06bbf'
MANIFEST_SHA='006f5c2b122795c699477aff984bf23deaf54a80224b9c7c6397153a6949b01e'
GATES_SHA='ededd42b83dd6fac8c3fe2f909b865a4f3a57fc6ef619c5f3042197e3eec51d0'
FOUNDATION_SHA='a3efba4c6555c9988c0494061a8f26ecead797581106d1f13419189c5dc87ed5'
OLD_INVENTORY_SHA='8bd45210885526adaaebc920ef4d13f4a84d1281614393f5641211948288bee0'

def require(value,code):
 if not value:raise ValueError('PUBLISHER_CONTROL_'+code)

def load(name,path,expected):
 raw=path.read_bytes()
 require(not path.is_symlink() and hashlib.sha256(raw).hexdigest()==expected,'HELPER_HASH_CHANGED')
 spec=importlib.util.spec_from_file_location(name,path)
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 require(hashlib.sha256(path.read_bytes()).hexdigest()==expected,'HELPER_CHANGED_DURING_IMPORT')
 return module

def doc(name,expected):
 p=ATTEMPT/name;raw=p.read_bytes()
 require(not p.is_symlink() and hashlib.sha256(raw).hexdigest()==expected,'DOCUMENT_HASH_CHANGED')
 return json.loads(raw)

def main():
 parser=argparse.ArgumentParser()
 parser.add_argument('mode',choices=['prepare','advance','observe'])
 args=parser.parse_args()
 require(os.environ.get('LIVE_TRADING_ENABLED')=='false' and sys.dont_write_bytecode
  and sys.flags.optimize==0,'SAFE_INTERPRETER_AND_LIVE_OFF_REQUIRED')
 o=load('publisher_final_operator',TOOLS/'publisher_handover_v6_preparation_20261028.py',OPERATOR_SHA)
 b=load('publisher_final_bridge',TOOLS/'publisher_handover_bridge_v6_20261028.py',BRIDGE_SHA)
 require(b.OPERATOR_SHA256==OPERATOR_SHA,'BRIDGE_OPERATOR_MISMATCH')
 manifest=doc('manifest.json',MANIFEST_SHA);gates=doc('gates.json',GATES_SHA)
 require(manifest['attempt_root']==str(ATTEMPT),'ATTEMPT_PATH_MISMATCH')
 gate_result=o.check_gates(manifest,gates)
 foundation=doc('fresh-foundation.json',FOUNDATION_SHA)
 socket={key:foundation['socket'][key] for key in ('device','inode','uid')}
 reader=b.readonly_reader()
 rpc=b.UnixPublisherRPC(socket,allow_one_term=args.mode=='advance')
 adapter=o.PublisherOnlyAdapter(manifest,reader,rpc,
  running_probe=lambda pid:b.runtime_probe(o,pid,manifest['new_revision'],manifest['runtime_files']),
  mutation_stage_approved=args.mode=='advance',actual_gate_bundle=gates)
 if args.mode=='prepare':
  require(not list(ATTEMPT.glob('[0-9][0-9][0-9]-*.json')),'PREPARATION_HAS_PRIOR_PHASES')
  adapter.guard_old();adapter.check_release()
  inventory=Path('/root/qtrade-local/operator-evidence/publisher-old-runtime-20261028T065936Z/git-runtime-inventory.json')
  inventory_raw=inventory.read_bytes();require(hashlib.sha256(inventory_raw).hexdigest()==OLD_INVENTORY_SHA,'OLD_INVENTORY_CHANGED')
  old=json.loads(inventory_raw);require(old['source_revision']==o.OLD_REVISION and len(old['files'])==242,'OLD_INVENTORY_SCOPE_CHANGED')
  proof=b.runtime_probe(o,manifest['old_owner']['pid'],o.OLD_REVISION,old['files'])
  result={'state':'ACTUAL_READONLY_PREPARATION_PASS','controls':adapter.controls(),
   'old_runtime_proof':proof,'gate_state':gate_result['state'],'production_mutations':False}
 elif args.mode=='advance':
  # This caller can be re-entered only to observe journalled results. The operator
  # refuses WRAPPER_INTENT/TERM_INTENT uncertainty without any replay or retarget.
  journal=o.JsonPhaseJournal(ATTEMPT)
  result=o.advance(manifest,gates,adapter,journal,backup=ATTEMPT/'private/wrapper.old.py')
 else:
  # Observation never calls advance, constructs a journal, installs, or signals.
  rows=[]
  for p in sorted(ATTEMPT.glob('[0-9][0-9][0-9]-*.json')):
   value=json.loads(p.read_bytes());require(value['sha256']==o.digest(value['body']),'JOURNAL_HASH_CHANGED');rows.append(value['body'])
  require(any(row['phase']=='TERM_INTENT' for row in rows),'NO_PRIOR_TERM_INTENT')
  before=next(row['payload']['before'] for row in rows if row['phase']=='PREPARED')
  result=adapter.observe(before)
 print(json.dumps({'checked_at':datetime.now(timezone.utc).isoformat(),'mode':args.mode,
  'caller_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
  'operator_sha256':OPERATOR_SHA,'bridge_sha256':BRIDGE_SHA,'result':result},sort_keys=True))

if __name__=='__main__':main()
