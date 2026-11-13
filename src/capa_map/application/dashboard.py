"""Project retained MAP results into the frontend developer's dataset contract."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
from . import runs

UNKNOWN = 'Not provided'


def date_value(value):
    text = str(value or '').strip()
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return None


def source_fields(payload):
    snapshot_id = payload.get('snapshot_id') or payload.get('metadata', {}).get('source_snapshot_id')
    configured = os.getenv('CAPA_SOURCE_SNAPSHOT')
    if not configured or not re.fullmatch(r'[a-f0-9]{64}', str(snapshot_id or '')):
        return {}
    base = Path(configured).resolve().parent
    target = (base / (snapshot_id + '.sqlite3')).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        return {}
    result = {}
    ids = [row['id'] for row in payload['records']]
    with sqlite3.connect(target.as_uri() + '?mode=ro', uri=True) as connection:
        metadata = json.loads(connection.execute('SELECT payload FROM metadata').fetchone()[0])
        if metadata.get('snapshot_id') != snapshot_id:
            return {}
        for offset in range(0, len(ids), 500):
            batch = ids[offset:offset + 500]
            query = 'SELECT pr_id,fields FROM records WHERE pr_id IN (' + ','.join('?' for _ in batch) + ')'
            for record_id, fields in connection.execute(query, batch):
                result[record_id] = json.loads(fields)
    return result


def dataset(run_id):
    payload = runs.results(run_id)
    if payload.get('status') != 'completed':
        raise runs.RunBusyError('This MAP analysis has not completed.')
    metadata = source_fields(payload)
    records, months = [], set()
    for row in payload['records']:
        fields = metadata.get(row['id'], {})
        opened_text = fields.get('date_opened') or ''
        opened, closed = date_value(opened_text), date_value(fields.get('date_closed'))
        month = opened.strftime('%Y-%m') if opened else None
        if month:
            months.add(month)
        source = row.get('source', {})
        signals = []
        if row.get('similarity') is not None:
            signals.append('Historical cluster similarity: ' + format(row['similarity'], '.6f') + '. Similarity is not a probability of correctness.')
        if row.get('secondCluster') is not None:
            signals.append('Second candidate: cluster ' + str(row['secondCluster']) + '.')
        if row.get('close'):
            signals.append('The two leading candidates have similar scores; inspect the source independently.')
        records.append({
            'id': row['id'], 'clusterId': str(row['finalCluster']) if row.get('finalCluster') is not None else '',
            'site': row.get('site') or UNKNOWN, 'division': fields.get('psg_division') or UNKNOWN,
            'department': fields.get('responsible_department') or UNKNOWN, 'year': str(opened.year) if opened else UNKNOWN,
            'criticality': fields.get('criticality') or fields.get('initial_criticality') or UNKNOWN,
            'status': fields.get('status') or UNKNOWN, 'opened': opened_text, 'createdLabel': opened_text or UNKNOWN,
            'drType': row.get('type') or UNKNOWN,
            **{key: fields.get(column) or UNKNOWN for key, column in [('building','building'),('room','room'),('suite','suite'),('productionArea','production_area'),('productionLine','production_line')]},
            'shortDescription': fields.get('record_name') or row.get('fields', {}).get('what') or UNKNOWN,
            'sourceField': ', '.join(key.replace('_',' ') for key,value in source.items() if value) or 'Unavailable',
            'sourceNarrative': '\n\n'.join(key.upper() + ':\n' + str(value) for key,value in source.items() if value),
            'matchSignals': signals, 'volume': 1,
            'daysOpen': (closed-opened).days if opened and closed and closed >= opened else None,
            'monthCounts': {month:1} if month else {},
        })
    clusters = []
    for group in payload['clusters']:
        if group['id'] is None:
            continue
        scores = [row['similarity'] for row in payload['records'] if row.get('finalCluster') == group['id'] and row.get('similarity') is not None]
        title = group.get('historicalTitle') or 'Historical label unavailable'
        clusters.append({'id': str(group['id']), 'title': title, 'shortTitle': title, 'subtitle': title,
                         'match': round(sum(scores) / len(scores) * 100, 2) if scores else None,
                         'context': group.get('historicalSummary') or 'No historical summary was retained for this cluster.'})
    return {'clusters': clusters, 'records': records, 'months': sorted(months),
            'refreshedAt': payload.get('updated_at') or payload.get('created_at') or 'Not available'}


def current_dataset():
    selected = os.getenv('MAP_DASHBOARD_RUN_ID', '').strip()
    if not selected:
        completed = [row for row in runs.history(100000) if row.get('status') == 'completed' and row.get('source_mode') != 'synthetic']
        if not completed:
            raise FileNotFoundError('No completed MAP analysis is available. Run an analysis through the MAP API first.')
        selected = completed[0]['run_id']
    return dataset(selected)
