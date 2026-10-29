import io
import json
import sys
import time
from importlib.util import find_spec
from pathlib import Path
sys.path[:0]=['E:/QTrade-git/src','E:/QTrade-git']
import pyarrow as pa
import pyarrow.parquet as pq
from scripts.unified_store import FIELDS

def typed_rows(table):
    if find_spec('numpy') is None:
        return table.to_pylist()
    try:
        table.validate(full=True)
    except pa.ArrowInvalid:
        # Preserve the original value or exact decode failure on malformed tables.
        return table.to_pylist()
    columns=[column.to_pylist() if name=='source_hashes' else
             column.to_numpy(zero_copy_only=False)
             for name,column in zip(FIELDS.names,table.columns,strict=True)]
    return [dict(zip(FIELDS.names,row,strict=True)) for row in zip(*columns,strict=True)]

def tree(value):
    if isinstance(value,list): return type(value),[tree(item) for item in value]
    if isinstance(value,dict): return type(value),[(key,tree(item)) for key,item in value.items()]
    return type(value),value

def outcome(fn):
    try:return 'OK',tree(fn())
    except Exception as error:return type(error).__name__,str(error)

comparisons=0
timings=[]
for count in (0,1,25,50,512,2400):
    strings=['10.0000','1e999999','NaN','示例','\n\t"\\','',None]
    rows=[{key: ([None,'a'*64,'示例'] if index%3 else None) if key=='source_hashes'
                 else strings[(index+column)%len(strings)]
           for column,key in enumerate(FIELDS.names)} for index in range(count)]
    for chunks in (1,3):
        tables=[pa.Table.from_pylist(rows[start::chunks],schema=FIELDS) for start in range(chunks)]
        table=pa.concat_tables(tables)
        assert tree(typed_rows(table))==tree(table.to_pylist())
        comparisons+=1
    buffer=io.BytesIO();pq.write_table(table,buffer,compression='zstd');raw=buffer.getvalue()
    case={'rows':count,'bytes':len(raw)}
    for threaded in (True,False):
        for fast in (False,True):
            started=time.perf_counter()
            for _ in range(100):
                decoded=pq.ParquetFile(io.BytesIO(raw)).read(use_threads=threaded)
                result=typed_rows(decoded) if fast else decoded.to_pylist()
            case[str(threaded)+'_'+str(fast)]=time.perf_counter()-started
            assert tree(result)==tree(table.to_pylist())
            comparisons+=1
    timings.append(case)
bad=pa.array([b'\xff'],type=pa.binary()).cast(pa.string(),safe=False)
arrays=[bad if key=='close' else pa.array([[]],type=pa.list_(pa.string()))
        if key=='source_hashes' else pa.array(['10'],type=pa.string()) for key in FIELDS.names]
malicious=pa.Table.from_arrays(arrays,schema=FIELDS)
assert outcome(lambda:typed_rows(malicious))==outcome(malicious.to_pylist)
comparisons+=1
original=find_spec
globals()['find_spec']=lambda _:None
assert tree(typed_rows(table))==tree(table.to_pylist())
globals()['find_spec']=original
comparisons+=1
report={'scope':'LOCAL_SYNTHETIC_ARROW_API_MICROBENCH_NOT_EXPORT_ACCEPTANCE',
        'exact_value_and_type_comparisons':comparisons,'timings_seconds_100_iterations':timings,
        'malicious_utf8_result':outcome(malicious.to_pylist),
        'new_mandatory_dependency':False,'fallback':'ORIGINAL_DECODER_IF_NUMPY_ABSENT_OR_FULL_ARROW_VALIDATION_FAILS'}
path=Path('E:/QTrade-temp/market-arrow-validated-prototype-20261028.json')
assert not path.exists();path.write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
