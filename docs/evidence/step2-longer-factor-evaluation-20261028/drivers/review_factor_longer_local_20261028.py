import gc
import gzip
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

root = Path('E:/QTrade-temp/factor-longer-s2b-20261028T112943Z-86cd84da')
old_path = Path('E:/QTrade-git/docs/evidence/p0-publisher-factor-20261028/real-daily-evaluation/actual-891-stock/report.json.gz')
old_hash = hashlib.sha256(old_path.read_bytes()).hexdigest()
assert old_hash == '47bed3add84aff16f901531115c17ac4534cfd667b6716595a1ce098b9af37dd'
old = json.loads(gzip.decompress(old_path.read_bytes()))
old_samples = {(r['session'], r['factor_index'], r['instrument']):
               (r['factor_value'], r['raw_label']) for r in old['documents']['samples']}
old_daily = old['documents']['daily_metrics']
old_redundancy = old['documents']['redundancy_daily']
old_request = old['documents']['specification']['request']
del old
gc.collect()
raw = gzip.decompress((root / 'report.json.gz').read_bytes())
report = json.loads(raw)
receipt = json.loads((root / 'receipt.json').read_bytes())
assert hashlib.sha256(raw).hexdigest() == receipt['report_raw_sha256']
del raw
samples = {(r['session'], r['factor_index'], r['instrument']):
           (r['factor_value'], r['raw_label']) for r in report['documents']['samples']}
assert all(samples[key] == value for key, value in old_samples.items())
for factor, days in old_daily.items():
    for day in days:
        new = next(r for r in report['documents']['daily_metrics'][factor]
                   if r['session'] == day['session'])
        assert {k: v for k, v in new.items() if k not in {'top_group_turnover', 'turnover_reason'}} == {
            k: v for k, v in day.items() if k not in {'top_group_turnover', 'turnover_reason'}}
for row in old_redundancy:
    assert row in report['documents']['redundancy_daily']
request = report['documents']['specification']['request']
changed = [key for key in request if request[key] != old_request[key]]
assert sorted(changed) == ['decisions', 'revalidates_evaluation_id', 'source_revision']
assert hashlib.sha256(old_path.read_bytes()).hexdigest() == old_hash
clocks, rejections = {}, {}
for index in range(4):
    rows = [r for r in report['documents']['eligibility_ledger'] if r['factor_index'] == index]
    clocks[str(index)] = {
        'known_feature_dependencies': sum(r['dependencies_known_at_barrier'] for r in rows),
        'unknown_clock_rows': sum(r['clock_reason'] == 'DEPENDENCY_AVAILABILITY_UNKNOWN' for r in rows),
        'future_clock_rows': sum(r['clock_reason'] == 'DEPENDENCY_NOT_KNOWN_AT_DECISION' for r in rows),
    }
    rejections[str(index)] = {
        'feature': dict(Counter(r['feature_reason'] for r in rows if r['feature_reason'])),
        'label': dict(Counter(r['label_reason'] for r in rows if r['label_reason'])),
    }
result = {'state': 'PASS_LOCAL_FROZEN_REPORT_COMPARISON', 'reviewed_at': datetime.now(UTC).isoformat(),
    'new_reference': receipt['reference'], 'new_report_gzip_sha256': receipt['report_gzip_sha256'],
    'new_report_raw_sha256': receipt['report_raw_sha256'], 'old_report_gzip_sha256': old_hash,
    'overlapping_sample_values_and_labels_unchanged': len(old_samples),
    'overlapping_daily_ic_rankic_groups_counts_unchanged': 20,
    'overlapping_daily_redundancy_rows_unchanged': len(old_redundancy),
    'turnover_scope_note': 'Original first-day turnover has no prior top; extension includes the preceding requested day. Not compared as identical.',
    'request_changed_fields_only': changed, 'clock_disclosure': clocks, 'rejections': rejections,
    'limits': 'Frozen local capture comparison; actual price verification is the separate independent oracle receipt.'}
with (root / 'local-review.json').open('x') as out:
    json.dump(result, out, indent=2)
print(json.dumps(result, indent=2))
