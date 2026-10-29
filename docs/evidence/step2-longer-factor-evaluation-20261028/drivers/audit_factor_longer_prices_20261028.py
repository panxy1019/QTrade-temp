"""Independent actual-price oracle. No repository score/rank/metric functions."""
import gzip
import hashlib
import json
import math
import resource
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time as wall_time
from decimal import Decimal, getcontext
from itertools import combinations
from pathlib import Path
from statistics import mean, pstdev, stdev
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from qtrade.research.strategy_contract import ready_dataset

root, source = Path(sys.argv[1]), Path(sys.argv[2])
assert root.parent == Path('/root/qtrade-local/operator-evidence')
started = time.perf_counter()
getcontext().prec = 28
checks, max_error = Counter(), 0.0


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(',', ':'), allow_nan=False).encode())


def check(condition, label):
    assert condition, label
    checks[label] += 1


def compare(actual, expected):
    global max_error
    if isinstance(expected, (float, Decimal)):
        error = abs(float(actual) - float(expected))
        max_error = max(max_error, error)
        check(math.isclose(float(actual), float(expected), rel_tol=2e-11, abs_tol=2e-12),
              'independent_numeric_comparison')
    elif isinstance(expected, dict):
        check(set(actual) == set(expected), 'independent_fields')
        for key in expected:
            compare(actual[key], expected[key])
    elif isinstance(expected, list):
        check(len(actual) == len(expected), 'independent_list_length')
        for left, right in zip(actual, expected, strict=True):
            compare(left, right)
    else:
        check(actual == expected, 'independent_exact_comparison')


compressed = (root / 'report.json.gz').read_bytes()
report = json.loads(gzip.decompress(compressed))
docs = report['documents']
spec = docs['specification']
request = spec['request']
receipt = json.loads((root / 'receipt.json').read_bytes())
check(sha(compressed) == receipt['report_gzip_sha256'], 'report_compressed_bytes')
manifest = {name: digest(value) for name, value in docs.items()}
check(report['report_manifest'] == manifest
      and report['report_manifest_sha256'] == digest(manifest)
      and report['specification_sha256'] == manifest['specification'], 'report_all_manifests')
check(request == json.loads((root / 'request.json').read_bytes()), 'frozen_request')
check(request['knowledge_mode'] == 'RETROSPECTIVE_RECONSTRUCTION', 'retrospective_only')
for name, snapshot in docs['source_snapshot'].items():
    raw = (source / 'src/qtrade' / name).read_bytes()
    check(raw.decode() == snapshot['source'] and sha(raw) == snapshot['sha256'], 'actual_reader_source')
for definition in docs['factor_definitions']:
    for name, expected in definition['source']['files'].items():
        check(sha((source / name).read_bytes()) == expected, 'actual_factor_source')

base = Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
version_root = base / 'unified-data/qlib-versions/versions' / spec['data_binding']['data_version_id']
database = Path('/root/qtrade-local/qlib-versions.sqlite')
check(ready_dataset(version_root, database) == spec['data_binding'], 'actual_ready_before')
version = json.loads((version_root / 'version.json').read_bytes())
calendar = spec['calendar']
days = [row['session'] for row in request['decisions']]
needed = calendar[calendar.index(days[0]) - 20:calendar.index(days[-1]) + 2]
check(len(days) == 16 and len(request['instruments']) == 891 and len(needed) == 37,
      'exact_16_891_scope')
intervals = defaultdict(list)
for line in (version_root / 'provider/instruments/workflow_supported.txt').read_text().splitlines():
    instrument, first, last = line.split('\t')
    intervals[instrument].append((first, last))
rows, inputs = {}, {}
for instrument in request['instruments']:
    name = 'stocks/' + instrument[2:] + '/daily.parquet'
    path = version_root / name
    check(sha(path.read_bytes()) == version['files'][name], 'actual_parquet_hash_before')
    parquet = pq.ParquetFile(path)
    kind = parquet.schema_arrow.field('date').type
    lower, upper = (date.fromisoformat(x) if pa.types.is_date(kind) else x
                    for x in (needed[0], needed[-1]))
    columns = [x for x in ('date', 'close', 'available_at', 'observed_at')
               if x in parquet.schema_arrow.names]
    table = pq.read_table(path, columns=columns,
                          filters=[('date', '>=', lower), ('date', '<=', upper)])
    values = table.to_pylist()
    rows[instrument] = {str(row['date']): row for row in values}
    check(len(rows[instrument]) == len(values) <= 37, 'actual_unique_price_grid')
    check(sha(path.read_bytes()) == version['files'][name], 'actual_parquet_hash_after')
    inputs[name] = version['files'][name]
print('PHASE independent_actual_prices_loaded', flush=True)


def price(instrument, session):
    row = rows[instrument].get(session)
    if row is None or row.get('close') is None:
        return None
    value = Decimal(str(row['close']))
    return value if value.is_finite() and value > 0 else None


def eligible(instrument, session):
    return any(first <= session <= last for first, last in intervals[instrument])


def ranks(values, percentile=False):
    ordered = sorted(values, key=lambda instrument: (values[instrument], instrument))
    result, first = {}, 0
    while first < len(ordered):
        last = first + 1
        while last < len(ordered) and values[ordered[last]] == values[ordered[first]]:
            last += 1
        rank = Decimal(first + 1 + last) / 2
        if percentile:
            rank = (rank - 1) / (len(ordered) - 1) if len(ordered) > 1 else Decimal('0.5')
        for instrument in ordered[first:last]:
            result[instrument] = rank
        first = last
    return result


def pearson(left, right):
    x, y = [float(v) for v in left], [float(v) for v in right]
    mx, my = mean(x), mean(y)
    xx, yy = [v - mx for v in x], [v - my for v in y]
    return sum(a * b for a, b in zip(xx, yy, strict=True)) / math.sqrt(
        sum(a * a for a in xx) * sum(b * b for b in yy))


def correlation(left, right):
    common = sorted(set(left) & set(right))
    x, y = [left[s] for s in common], [right[s] for s in common]
    reason = ('INSUFFICIENT_PAIRS' if len(common) < request['min_pairs'] else
              'CONSTANT_LEFT' if len(set(x)) < 2 else
              'CONSTANT_RIGHT' if len(set(y)) < 2 else None)
    if reason:
        return len(common), None, None, reason
    # Ties must be ranked on the same paired universe, not each full feature set.
    rx, ry = ranks({s: left[s] for s in common}), ranks({s: right[s] for s in common})
    return len(common), pearson(x, y), pearson([rx[s] for s in common], [ry[s] for s in common]), None


def series(values):
    valid = [float(value) for value in values if value is not None]
    deviation = stdev(valid) if len(valid) >= 2 else None
    reason = ('INSUFFICIENT_DAYS' if len(valid) < 2 else
              'ZERO_SAMPLE_STANDARD_DEVIATION' if deviation == 0 else None)
    return {'valid_days': len(valid), 'mean': mean(valid) if valid else None,
        'sample_std_ddof1': deviation, 'ir': mean(valid) / deviation if reason is None else None,
        'ir_reason': reason, 'positive_fraction': sum(v > 0 for v in valid) / len(valid)
            if valid else None}


def summary(daily):
    spreads = [row['long_short_spread'] for row in daily if row['long_short_spread'] is not None]
    turnover = [row['top_group_turnover'] for row in daily if row['top_group_turnover'] is not None]
    return {'requested_days': len(daily), 'ic': series([row['ic'] for row in daily]),
        'rank_ic': series([row['rank_ic'] for row in daily]),
        'mean_group_spread': mean(spreads) if spreads else None,
        'mean_top_group_turnover': mean(turnover) if turnover else None,
        'valid_pairs': sum(row['pairs'] for row in daily),
        'candidate_pairs': sum(row['candidates'] for row in daily)}


sample_map = {(x['session'], x['factor_index'], x['instrument']): x for x in docs['samples']}
ledger_map = {(x['session'], x['factor_index'], x['instrument']): x for x in docs['eligibility_ledger']}
check(len(sample_map) == len(ledger_map) == 16 * 891 * 4, 'complete_saved_sample_scope')
all_scores, all_labels, daily_expected = {}, {}, defaultdict(list)
barrier = datetime.fromisoformat(spec['label']['maturity_barrier'])
for day in days:
    position = calendar.index(day)
    target = calendar[position + 1]
    labels = {}
    check(datetime.combine(date.fromisoformat(target), wall_time(15), ZoneInfo('Asia/Shanghai'))
          <= barrier <= datetime.now(UTC), 'actual_label_session_maturity')
    for instrument in request['instruments']:
        entry, exit_price = price(instrument, day), price(instrument, target)
        reason = ('LABEL_OUTSIDE_FROZEN_ELIGIBILITY_INTERVAL' if not eligible(instrument, target)
                  else 'LABEL_CLOSE_MISSING_OR_INVALID' if entry is None or exit_price is None
                  else None)
        for row in (rows[instrument].get(day), rows[instrument].get(target)):
            for field in ('available_at', 'observed_at'):
                stamp = None if row is None else row.get(field)
                if stamp is not None:
                    instant = stamp if isinstance(stamp, datetime) else datetime.fromisoformat(stamp)
                    check(instant.tzinfo is not None, 'actual_label_clock_aware')
                    if reason is None and instant > barrier:
                        reason = 'LABEL_NOT_AVAILABLE_AT_EVALUATION'
        for factor in range(4):
            check(ledger_map[(day, factor, instrument)]['label_reason'] == reason,
                  'actual_label_rejection')
        if reason is None:
            labels[instrument] = exit_price / entry - 1
    all_labels[day] = labels
    for factor, length in enumerate((21, 6, 21, 21)):
        window = calendar[position - length + 1:position + 1]
        histories = {}
        for instrument in request['instruments']:
            values = [price(instrument, session) for session in window]
            reason = ('OUTSIDE_FROZEN_ELIGIBILITY_INTERVAL' if not all(
                eligible(instrument, session) for session in window) else
                'MISSING_COMMON_SESSION_CLOSE' if any(session not in rows[instrument]
                    for session in window) else 'INVALID_CLOSE' if any(v is None for v in values)
                else None)
            check(ledger_map[(day, factor, instrument)]['feature_reason'] == reason,
                  'actual_feature_rejection')
            fact = ledger_map[(day, factor, instrument)]
            clocks = []
            for session in window:
                row = rows[instrument].get(session)
                values_at_session = {}
                for field in ('available_at', 'observed_at'):
                    value = None if row is None else row.get(field)
                    values_at_session[field] = value.isoformat() if isinstance(value, datetime) else value
                clocks.append({'session': session, **values_at_session})
            check(fact['history_sessions'] == window and fact['dependency_clocks'] == clocks
                  and fact['label_exit_session'] == target, 'actual_dependency_grid_and_clocks')
            known, clock_reason = False, None
            if reason is None:
                known = True
                for clock in clocks:
                    values_at_session = [clock[field] for field in ('available_at', 'observed_at')]
                    if any(value is None for value in values_at_session):
                        known, clock_reason = False, 'DEPENDENCY_AVAILABILITY_UNKNOWN'
                        break
                    if any(datetime.fromisoformat(value) > datetime.fromisoformat(
                            day + 'T15:30:00+08:00') for value in values_at_session):
                        known, clock_reason = False, 'DEPENDENCY_NOT_KNOWN_AT_DECISION'
                        break
            check(fact['dependencies_known_at_barrier'] == known
                  and fact['clock_reason'] == clock_reason, 'actual_dual_clock_admission')
            if reason is None:
                histories[instrument] = values
        if factor == 3:
            components = [{}, {}, {}]
            for instrument, prices in histories.items():
                components[0][instrument] = prices[-1] / prices[0] - 1
                components[1][instrument] = -(prices[-1] / prices[-6] - 1)
                components[2][instrument] = -Decimal(str(pstdev(
                    float(prices[i] / prices[i - 1] - 1) for i in range(1, 21))))
            percentiles = [ranks(values, percentile=True) for values in components]
            scores = {s: sum((r[s] for r in percentiles), Decimal(0)) / 3 for s in histories}
        else:
            scores = {}
            for instrument, prices in histories.items():
                scores[instrument] = (prices[-1] / prices[0] - 1 if factor == 0 else
                    -(prices[-1] / prices[0] - 1) if factor == 1 else
                    -Decimal(str(pstdev(float(prices[i] / prices[i - 1] - 1)
                                        for i in range(1, len(prices))))))
        all_scores[(day, factor)] = scores
        for instrument in request['instruments']:
            saved = sample_map[(day, factor, instrument)]
            expected_score, expected_label = scores.get(instrument), labels.get(instrument)
            check((saved['factor_value'] is None) == (expected_score is None), 'actual_score_presence')
            check((saved['raw_label'] is None) == (expected_label is None), 'actual_label_presence')
            if expected_score is not None:
                check(abs(Decimal(saved['factor_value']) - expected_score) <= Decimal('1e-24'),
                      'actual_price_factor_formula')
            if expected_label is not None:
                check(Decimal(saved['raw_label']) == expected_label, 'actual_price_label_formula')
        pairs, ic, rank_ic, reason = correlation(scores, labels)
        ordered = sorted(set(scores) & set(labels), key=lambda s: (scores[s], s))
        groups = []
        if pairs >= request['min_pairs']:
            for index in range(request['quantiles']):
                members = ordered[index * pairs // request['quantiles']:
                                  (index + 1) * pairs // request['quantiles']]
                groups.append({'group': index + 1, 'count': len(members), 'instruments': members,
                               'mean_label': sum((labels[s] for s in members), Decimal(0)) / len(members)})
        top = set(groups[-1]['instruments']) if groups else None
        prior_top = daily_expected[factor][-1]['top_instruments'] if daily_expected[factor] else None
        previous = set(prior_top) if prior_top is not None else None
        expected = {'session': day, 'candidates': 891, 'feature_valid': len(scores),
            'label_valid': len(labels), 'pairs': pairs, 'ic': ic, 'rank_ic': rank_ic,
            'correlation_reason': reason, 'groups': groups,
            'groups_reason': None if groups else 'INSUFFICIENT_PAIRS',
            'long_short_spread': groups[-1]['mean_label'] - groups[0]['mean_label'] if groups else None,
            'top_group_turnover': len(top - previous) / len(top) if previous is not None and top else None,
            'turnover_reason': None if previous is not None and top else 'NO_ADJACENT_VALID_TOP_GROUP',
            'top_instruments': sorted(top) if top else None}
        compare(docs['daily_metrics'][str(factor)][len(daily_expected[factor])], expected)
        daily_expected[factor].append(expected)
print('PHASE independent_factor_label_daily_metrics_passed', flush=True)
for factor, daily in daily_expected.items():
    compare(docs['summary']['factors'][str(factor)], summary(daily))
    size = request['stability_block_sessions']
    blocks = [{'start': daily[offset]['session'], 'end': daily[min(offset + size, len(daily)) - 1]['session'],
               **summary(daily[offset:offset + size])} for offset in range(0, len(daily), size)]
    compare(docs['stability'][str(factor)], blocks)
for left, right in combinations(range(4), 2):
    pair = []
    for day in days:
        a, b = all_scores[(day, left)], all_scores[(day, right)]
        pairs, p, s, reason = correlation(a, b)
        expected = {'session': day, 'left_factor_index': left, 'right_factor_index': right,
            'instruments': sorted(set(a) & set(b)), 'pairs': pairs,
            'pearson': p, 'spearman': s, 'reason': reason}
        actual = next(row for row in docs['redundancy_daily'] if row['session'] == day
            and row['left_factor_index'] == left and row['right_factor_index'] == right)
        compare(actual, expected)
        pair.append(expected)
    actual = next(row for row in docs['summary']['redundancy']
        if row['left_factor_index'] == left and row['right_factor_index'] == right)
    compare(actual, {'left_factor_index': left, 'right_factor_index': right,
        'pearson': series([row['pearson'] for row in pair]),
        'spearman': series([row['spearman'] for row in pair])})
check(ready_dataset(version_root, database) == spec['data_binding'], 'actual_ready_after')
check(sha((root / 'report.json.gz').read_bytes()) == sha(compressed), 'report_bytes_unchanged')
result = {'state': 'PASS_INDEPENDENT_ACTUAL_PRICE_ORACLE',
    'reviewed_at': datetime.now(UTC).isoformat(), 'evaluation_id': report['evaluation_id'],
    'report_manifest_sha256': report['report_manifest_sha256'],
    'report_gzip_sha256': sha(compressed), 'oracle_source_sha256': sha(Path(__file__).read_bytes()),
    'source_price_files': inputs, 'checks_by_kind': dict(checks), 'checks_total': sum(checks.values()),
    'max_metric_absolute_error': max_error, 'relative_metric_tolerance': 2e-11,
    'absolute_metric_tolerance': 2e-12, 'factor_formula_absolute_tolerance': '1e-24',
    'elapsed_seconds': time.perf_counter() - started,
    'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    'limitations': ['RETROSPECTIVE_CURRENT_SELECTED_UNIVERSE_NOT_HISTORICAL_PIT',
                    'GROSS_CLOSE_LABEL_NOT_EXECUTED_NET_PORTFOLIO',
                    'NO_OOS_OR_SIGNIFICANCE_CLAIM', 'NO_PRODUCTION_STATE_VERIFICATION']}
with (root / 'independent-actual-price-oracle.json').open('x') as handle:
    json.dump(result, handle, indent=2)
print(json.dumps({k: result[k] for k in ('state', 'checks_total', 'max_metric_absolute_error',
    'elapsed_seconds', 'peak_rss_kib')}), flush=True)
