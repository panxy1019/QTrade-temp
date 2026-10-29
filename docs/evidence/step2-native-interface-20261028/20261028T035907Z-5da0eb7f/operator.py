import importlib.metadata as m, json, hashlib, ast, shutil
from datetime import datetime, timezone
dist=m.distribution("pyqlib")
paths=["qlib/contrib/data/handler.py", "qlib/contrib/data/loader.py", "qlib/data/ops.py", "qlib/contrib/eva/alpha.py", "qlib/workflow/record_temp.py", "qlib/data/data.py"]
result={"observed_at":datetime.now(timezone.utc).isoformat(),"pyqlib":dist.version,"free_gib":shutil.disk_usage("/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade").free/2**30,"sources":{}}
assert result["free_gib"]>=100
for relative in paths:
 p=dist.locate_file(relative); data=p.read_bytes(); source=data.decode(); tree=ast.parse(source)
 result["sources"][relative]={"sha256":hashlib.sha256(data).hexdigest(),"source":source,"classes":[{"name":n.name,"methods":[x.name for x in n.body if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef))]} for n in tree.body if isinstance(n,ast.ClassDef)]}
print(json.dumps(result,sort_keys=True))
