"""Source-wide deviation rankings. Counts use recorded fields, never LLM output."""
from collections import Counter, defaultdict
from contextlib import closing
import csv
from datetime import date
from functools import lru_cache
import json
from pathlib import Path
import re
import sqlite3
from .deadlines import calendar_date, current_day

CLIENT_FIELDS = ('client', 'client_name', 'customer', 'customer_name')
PRODUCT_FIELDS = ('product', 'product_name', 'product_code', 'product_id', 'affected_product')
STAGE_FIELDS = ('stage', 'process_stage', 'manufacturing_stage', 'operational_stage')
DIMENSION_FIELDS = ('date_opened', *CLIENT_FIELDS, *PRODUCT_FIELDS, *STAGE_FIELDS, 'process_step', 'responsible_department')
EMPTY = {'', '-', '--', 'n/a', 'na', 'none', 'null', 'nan', 'unknown', 'not provided', 'not available', 'not applicable', 'unavailable'}


def usable(value):
    if not isinstance(value, str):
        return ''
    text = ' '.join(value.split())
    return '' if text.casefold() in EMPTY else text


def first(fields, names):
    return next((value for name in names if (value := usable(fields.get(name)))), '')


def department_stage(value):
    value = value.casefold()
    matches = []
    for label, pattern in [
        ('QC', r'\bqc\b|quality control|microbiology'),
        ('Packaging', r'packag|pick\s*(?:&|and)\s*pack'),
        ('Manufacturing', r'manufactur|\bproduction\b|\bprocessing\b'),
        ('Quality assurance', r'\bqa\b|quality assurance|compliance|regulatory'),
        ('Warehousing / logistics', r'warehouse|distribution|logistics|supply chain|\brepository\b'),
        ('Engineering / maintenance', r'engineering|maintenance|facilities|calibration'),
    ]:
        if re.search(pattern, value):
            matches.append(label)
    # Combined departments cannot establish a unique stage on their own.
    return matches[0] if len(matches) == 1 else ''


def stage_value(fields):
    explicit = first(fields, STAGE_FIELDS)
    if explicit:
        return department_stage(explicit) or explicit, 'recorded'
    step = first(fields, ('process_step',)).casefold()
    if re.search(r'packag|labell?ing', step):
        return 'Packaging', 'mapped'
    if re.search(r'analytical testing|laboratory|\bassays?\b|release testing|material testing|environmental monitoring|\bqc\b|stability', step):
        return 'QC', 'mapped'
    if re.search(r'warehouse|\bstorage\b|distribution|shipment|shipping', step):
        return 'Warehousing / logistics', 'mapped'
    if re.search(r'calibration|maintenance|equipment validation|validation - equipment', step):
        return 'Engineering / maintenance', 'mapped'
    if re.search(r'document issuance|batch release process|product batch release', step):
        return 'Quality assurance', 'mapped'
    if re.search(r'manufactur|\bfilling\b|formulation|cell expansion|cell culture|bioreactor|harvest|granulation|encapsulation|\bcoating\b|compression|\bfiltration\b|transfection|infection|buffer preparation|production culture|pre-culture|\bdrying\b|media preparation|dispensing|compounding|cell/viral banking|\bcharging\b|\bblending\b|lyophili', step):
        return 'Manufacturing', 'mapped'
    department = department_stage(first(fields, ('responsible_department',)))
    return (department, 'mapped') if department else ('', 'unavailable')


@lru_cache(maxsize=4)
def sqlite_dimensions(path, size, modified):
    """Cache only a minimal immutable projection, keyed by snapshot identity."""
    columns = ','.join("'$." + key + "'" for key in DIMENSION_FIELDS)
    rows, available, total = [], set(), 0
    with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)) as connection:
        total = connection.execute('SELECT COUNT(*) FROM records').fetchone()[0]
        query = "SELECT pr_id,json_extract(fields," + columns + ") FROM records WHERE lower(trim(record_type))='deviation' ORDER BY pr_id"
        for identifier, encoded in connection.execute(query):
            values = json.loads(encoded)
            available.update(key for key, value in zip(DIMENSION_FIELDS, values) if value is not None)
            rows.append((identifier, *values))
    return tuple(rows), tuple(sorted(available)), total


@lru_cache(maxsize=4)
def csv_dimensions(path, size, modified):
    rows, available, identifiers = [], set(), set()
    csv.field_size_limit(2**31 - 1)
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        for original in csv.DictReader(handle):
            fields = {re.sub(r'[^a-z0-9]+', '_', key.strip().lower()).strip('_'): value for key, value in original.items() if key is not None}
            identifier = str(fields.get('pr_id') or '').strip()
            if not identifier or identifier in identifiers:
                continue
            identifiers.add(identifier)
            if str(fields.get('record_type') or '').strip().casefold() != 'deviation':
                continue
            available.update(key for key in DIMENSION_FIELDS if key in fields)
            rows.append((identifier, *(fields.get(key) for key in DIMENSION_FIELDS)))
    return tuple(rows), tuple(sorted(available)), len(identifiers)


def rank(values, total, fields, *, stage=False):
    counts, labels, modes = Counter(), defaultdict(Counter), Counter()
    for label, mode in values:
        label = usable(label)
        if not label:
            continue
        key = label.casefold()
        counts[key] += 1
        labels[key][label] += 1
        modes[mode] += 1
    display = {key: sorted(candidates, key=lambda name: (-candidates[name], name.casefold(), name))[0] for key, candidates in labels.items()}
    ordered = sorted(counts, key=lambda key: (-counts[key], display[key].casefold(), display[key]))
    top = ordered[:3]
    known = sum(counts.values())
    return {
        'rows': [{'label': display[key], 'count': counts[key], 'percent': round(100 * counts[key] / total, 1) if total else 0} for key in top],
        'knownCount': known, 'missingCount': total - known, 'distinctCount': len(counts),
        'topCount': sum(counts[key] for key in top),
        'topShare': round(100 * sum(counts[key] for key in top) / total, 1) if total else 0,
        'cutoffTied': len(ordered) > 3 and counts[ordered[2]] == counts[ordered[3]],
        'fields': list(fields), 'recordedCount': modes['recorded'], 'mappedCount': modes['mapped'],
        'basis': 'Recorded stage, otherwise grouped process step / department' if stage else 'Recorded source field',
    }


def summarize(rows, available, total, *, as_of, run_id, snapshot_id):
    try:
        start = as_of.replace(year=as_of.year - 1)
    except ValueError:
        start = as_of.replace(year=as_of.year - 1, day=28)
    in_window, undated, future = [], 0, 0
    latest = None
    for row in rows:
        fields = dict(zip(DIMENSION_FIELDS, row[1:]))
        opened = calendar_date(fields.get('date_opened'))
        if opened is None:
            undated += 1
            continue
        if opened > as_of:
            future += 1
            continue
        latest = max(latest, opened) if latest else opened
        if start <= opened <= as_of:
            in_window.append(fields)
    count = len(in_window)
    clients = rank([(first(fields, CLIENT_FIELDS), 'recorded') for fields in in_window], count, [key for key in CLIENT_FIELDS if key in available])
    products = rank([(first(fields, PRODUCT_FIELDS), 'recorded') for fields in in_window], count, [key for key in PRODUCT_FIELDS if key in available])
    stages = rank([stage_value(fields) for fields in in_window], count, [key for key in (*STAGE_FIELDS, 'process_step', 'responsible_department') if key in available], stage=True)
    for name, item in [('client', clients), ('product', products), ('stage', stages)]:
        item['unavailableReason'] = (
            f'No structured {name} field is available in this source.' if not item['fields'] else
            'No deviations were opened in this period.' if not count else
            ('No usable stage could be established from the recorded stage, process step or department.' if name == 'stage' else f'All recorded {name} values are blank or unavailable in this period.') if not item['knownCount'] else ''
        )
    return {
        'runId': run_id, 'scope': 'source_snapshot', 'available': True, 'snapshotId': snapshot_id,
        'asOf': as_of.isoformat(), 'dateFrom': start.isoformat(), 'dateTo': as_of.isoformat(),
        'sourceRecordCount': total, 'deviationCount': len(rows), 'windowDeviationCount': count,
        'undatedCount': undated, 'futureDatedCount': future, 'latestOpened': latest.isoformat() if latest else None,
        'clients': clients, 'stages': stages, 'products': products,
    }


def for_run(run_id, as_of=None):
    from . import dashboard, runs
    payload = runs.results(run_id)
    if payload.get('status') != 'completed':
        raise runs.RunBusyError('This MAP analysis has not completed.')
    context = dashboard.source_context(payload)
    as_of = as_of or current_day()
    if not context['available']:
        result = summarize((), (), 0, as_of=as_of, run_id=run_id, snapshot_id=None)
        result.update(available=False, sourceRecordCount=None, deviationCount=None, windowDeviationCount=None)
        for key in ['clients', 'stages', 'products']:
            result[key]['unavailableReason'] = 'The verified source snapshot for this analysis is unavailable.'
        return result
    location = context.get('_snapshot_path') or context.get('_csv_path')
    if not location:
        raise ValueError('The verified source does not provide a ranking projection.')
    target = Path(location)
    stamp = target.stat()
    projection = sqlite_dimensions if context.get('_snapshot_path') else csv_dimensions
    rows, available, total = projection(str(target), stamp.st_size, stamp.st_mtime_ns)
    return summarize(rows, available, total, as_of=as_of, run_id=run_id, snapshot_id=context['metadata'].get('snapshot_id'))
