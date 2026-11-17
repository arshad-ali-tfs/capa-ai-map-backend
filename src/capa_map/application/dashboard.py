"""Source-backed presentation contract for the MAP workspace."""
from contextlib import closing
from datetime import datetime
from functools import lru_cache
import json
import os
from pathlib import Path
import posixpath
import re
import sqlite3
from . import annotations, runs
from ..infrastructure.storage import is_volume, local_file

UNKNOWN = 'Not provided'
SOURCE_LABELS = {
    'detailed_description': 'Description',
    'investigation': 'Investigation',
    'conclusion': 'Conclusion',
}


def compact_title(title, limit=64):
    """A display excerpt only; the full historical label remains authoritative."""
    primary = re.split(r'\s+[—–]\s+', title, maxsplit=1)[0]
    if len(primary) <= limit:
        return primary
    excerpt = primary[:limit - 1]
    if ' ' in excerpt:
        excerpt = excerpt.rsplit(' ', 1)[0]
    return excerpt.rstrip() + '…'


def date_value(value):
    try:
        return datetime.fromisoformat(str(value or '').strip().replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return None


def source_context(payload):
    """Resolve an immutable snapshot by embedded identity, including SDK caches."""
    snapshot_id = payload.get('snapshot_id') or payload.get('metadata', {}).get('source_snapshot_id')
    configured = os.getenv('CAPA_SOURCE_SNAPSHOT')
    empty = {'fields': {}, 'capas': [], 'metadata': {}, 'available': False}
    if not configured or not re.fullmatch(r'[a-f0-9]{64}', str(snapshot_id or '')):
        return empty
    sibling = (posixpath.dirname(configured) + '/' + snapshot_id + '.sqlite3') if is_volume(configured) else str(Path(configured).parent / (snapshot_id + '.sqlite3'))
    for location in dict.fromkeys([configured, sibling]):
        try:
            target = local_file(location)
        except (FileNotFoundError, OSError):
            continue
        with closing(sqlite3.connect(target.as_uri() + '?mode=ro', uri=True)) as connection:
            metadata = json.loads(connection.execute('SELECT payload FROM metadata').fetchone()[0])
            if metadata.get('snapshot_id') != snapshot_id:
                continue
            fields = {}
            ids = [row['id'] for row in payload['records']]
            for offset in range(0, len(ids), 400):
                batch = ids[offset:offset + 400]
                query = 'SELECT pr_id,fields FROM records WHERE pr_id IN (' + ','.join('?' for _ in batch) + ')'
                for record_id, values in connection.execute(query, batch):
                    fields[record_id] = json.loads(values)
            # Fetch only CAPA relationship/display fields, not every narrative.
            capas = []
            if ids:
                selected = set(ids)
                stamp = target.stat()
                for identifier, title, status, parent, root_parent in capa_rows(str(target), stamp.st_size, stamp.st_mtime_ns):
                    parents = [str(value).strip() for value in (parent, root_parent) if value is not None]
                    related = sorted(selected.intersection(parents))
                    if related:
                        capas.append({'id': identifier, 'description': title or UNKNOWN,
                                      'status': status or UNKNOWN, 'recordIds': related})
            return {'fields': fields, 'capas': capas, 'metadata': metadata, 'available': True}
    return empty


@lru_cache(maxsize=4)
def capa_rows(path, size, modified):
    """Cache only immutable relationship projections, invalidated by file identity."""
    with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)) as connection:
        return tuple(connection.execute("""SELECT pr_id, title, status,
            json_extract(fields, '$.parent_id'), json_extract(fields, '$.root_parent_id')
            FROM records WHERE lower(record_type) = 'capa'"""))


def source_fields(payload):
    return source_context(payload)['fields']


def cluster_context(group, run_id, record_count):
    """Present saved batch output without promoting it to a historical definition."""
    historical = {'title': group.get('historicalTitle') or '', 'summary': group.get('historicalSummary') or ''}
    historical = historical if any(value.strip() for value in historical.values()) else None
    analysis = None
    if group.get('proposalScope') == 'current_batch_only':
        title, summary = group.get('proposalTitle') or '', group.get('proposalSummary') or ''
        if title.strip() or summary.strip():
            analysis = {'title': title, 'summary': summary, 'recordCount': record_count, 'runId': run_id}
    # An explicit legacy source marker identifies older run-generated summaries
    # stored in the original summary columns. They are not historical profiles.
    if group.get('summarySource') == 'openai_generated_unreviewed' and not group.get('proposalScope'):
        analysis = {**historical, 'recordCount': record_count, 'runId': run_id} if historical else None
        historical = None
    historical_title = historical['title'] if historical and historical['title'].strip() else ''
    analysis_title = analysis['title'] if analysis and analysis['title'].strip() else ''
    title = historical_title or analysis_title or f'Cluster {group["id"]}'
    if analysis and analysis['summary'].strip():
        context, source = analysis['summary'], 'current_analysis'
    elif historical and historical['summary'].strip():
        context, source = historical['summary'], 'historical'
    else:
        context, source = 'No saved summary is available for this cluster.', 'unavailable'
    return {'title': title, 'shortTitle': compact_title(title), 'subtitle': title,
            'titleSource': 'historical' if historical_title else 'current_analysis' if analysis_title else 'unavailable',
            'context': context, 'contextSource': source, 'analysisContext': analysis, 'historicalContext': historical}


def dataset(run_id):
    payload = runs.results(run_id)
    if payload.get('status') != 'completed':
        raise runs.RunBusyError('This MAP analysis has not completed.')
    context = source_context(payload)
    saved = annotations.read(run_id) if 'CAPA_DATA_DIR' in os.environ else {}
    records, months = [], set()
    known_clusters = {str(group['id']) for group in payload['clusters'] if group['id'] is not None}
    for row in payload['records']:
        fields = context['fields'].get(row['id'], {})
        opened_text = fields.get('date_opened') or ''
        opened, closed = date_value(opened_text), date_value(fields.get('date_closed'))
        month = opened.strftime('%Y-%m') if opened else None
        if month:
            months.add(month)
        source = row.get('source', {})
        cluster_id = str(row['finalCluster']) if row.get('finalCluster') is not None else ''
        warnings = []
        if row.get('missing'):
            warnings.append(f'Cluster {cluster_id or "unavailable"} has no historical name or description in the cluster catalogue. The cluster description needs to be completed; this does not indicate that record {row["id"]} is incorrectly assigned.')
        if not cluster_id or cluster_id not in known_clusters:
            warnings.append('No cluster assignment is available. Review this record separately.')
        signals = []
        if row.get('similarity') is not None:
            signals.append('Historical cluster similarity: ' + format(row['similarity'], '.6f') + '. Similarity is not a probability of correctness.')
        if row.get('secondCluster') is not None:
            signals.append('Second candidate: cluster ' + str(row['secondCluster']) + '.')
        signals.extend(warnings)
        records.append({
            'id': row['id'], 'clusterId': cluster_id if cluster_id in known_clusters else '',
            'site': row.get('site') or UNKNOWN, 'division': fields.get('psg_division') or UNKNOWN,
            'department': fields.get('responsible_department') or UNKNOWN, 'year': str(opened.year) if opened else UNKNOWN,
            'criticality': fields.get('criticality') or fields.get('initial_criticality') or UNKNOWN,
            'status': fields.get('status') or UNKNOWN, 'opened': opened_text,
            'createdLabel': fields.get('date_created') or UNKNOWN,
            'drType': row.get('type') or UNKNOWN,
            **{key: fields.get(column) or UNKNOWN for key, column in [('building','building'),('room','room'),('suite','suite'),('productionArea','production_area'),('productionLine','production_line')]},
            'shortDescription': fields.get('record_name') or row.get('fields', {}).get('what') or UNKNOWN,
            'sourceField': ', '.join(key.replace('_',' ') for key,value in source.items() if value) or 'Unavailable',
            'sourceNarrative': '\n\n'.join(key.upper() + ':\n' + str(value) for key,value in source.items() if value),
            'sourceSections': [
                {'key': key, 'label': SOURCE_LABELS.get(key, key.replace('_', ' ').capitalize()), 'text': str(value)}
                for key, value in source.items() if value
            ],
            # Read the six saved outputs for this record/run; never re-extract
            # or substitute source metadata when a category is unavailable.
            'fiveWh': {key: row.get('fields', {}).get(key) or '' for key in runs.FIVEWH},
            'matchSignals': signals, 'similarity': row.get('similarity'), 'warnings': warnings,
            'annotation': saved.get(row['id'], annotations.empty()), 'volume': 1,
            'daysOpen': (closed-opened).days if opened and closed and closed >= opened else None,
            'monthCounts': {month:1} if month else {},
        })
    clusters = []
    for group in payload['clusters']:
        if group['id'] is None:
            continue
        members = [row for row in records if row['clusterId'] == str(group['id'])]
        scores = [row['similarity'] for row in members if row['similarity'] is not None]
        ids = {row['id'] for row in members}
        clusters.append({'id': str(group['id']), **cluster_context(group, run_id, len(members)),
                         'match': round(sum(scores) / len(scores) * 100, 2) if scores else None,
                         'capas': [item for item in context['capas'] if ids.intersection(item['recordIds'])]})
    return {'clusters': clusters, 'records': records, 'months': sorted(months),
            'refreshedAt': payload.get('updated_at') or payload.get('created_at') or 'Not available',
            'analysis': {'runId': run_id, 'recordCount': len(records),
                         'sourceRecordCount': context['metadata'].get('accepted_rows'),
                         'snapshotId': payload.get('snapshot_id') or payload.get('metadata', {}).get('source_snapshot_id'),
                         'sourceAvailable': context['available'], 'sourceMode': payload.get('source_mode', 'historical'),
                         'selection': 'configured' if os.getenv('MAP_DASHBOARD_RUN_ID', '').strip() else 'latest_completed'}}


def current_dataset():
    selected = os.getenv('MAP_DASHBOARD_RUN_ID', '').strip()
    if not selected:
        completed = [row for row in runs.history(100000) if row.get('status') == 'completed' and row.get('source_mode') != 'synthetic']
        if not completed:
            raise FileNotFoundError('No completed MAP analysis is available. Run an analysis through the MAP API first.')
        selected = completed[0]['run_id']
    return dataset(selected)
