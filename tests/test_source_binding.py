import csv
from copy import deepcopy
import hashlib
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from capa_map.application import dashboard, runs
from capa_map.infrastructure.data.importer import import_snapshot
from capa_map.infrastructure.data.repository import SqliteRecordRepository
from capa_map.main import app

RUN = 'b' * 32
ORIGINAL = 'f' * 64


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    monkeypatch.setenv('CAPA_DATA_DIR', str(tmp_path))
    source = tmp_path / 'accepted.csv'
    columns = ['PR_ID', 'SITE', 'RECORD_TYPE', 'RECORD_NAME', 'DETAILED_DESCRIPTION',
               'INVESTIGATION', 'CONCLUSION', 'DATE_OPENED', 'DATE_CLOSED', 'Criticality', 'PARENT_ID']
    with source.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerow(['001', 'Fictional Site', 'Deviation', 'Fictional event', 'Lot AB-123.',
                         'Fictional investigation.', 'Fictional conclusion.', '2026-01-03', '2026-01-08', 'Major', ''])
        writer.writerow(['CAPA-1', 'Fictional Site', 'CAPA', 'Fictional corrective action', '', '', '', '', '', '', '001'])
    snapshot = import_snapshot(source, tmp_path / 'sources')
    repository = SqliteRecordRepository(snapshot)
    metadata = repository.metadata()
    fields = repository.get_record('001').fields
    monkeypatch.setenv('CAPA_SOURCE_SNAPSHOT', str(snapshot))
    directory = runs.root() / RUN
    directory.mkdir()
    selected = {name: fields[name] for name in ('pr_id', 'site', 'record_type', 'detailed_description',
                'investigation', 'conclusion', 'date_opened', 'date_closed', 'criticality')}
    selected['description'] = fields['record_name']
    selected_file = directory / '01_dpd_closed_deviations.csv'
    with selected_file.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(selected))
        writer.writeheader()
        writer.writerow(selected)
    binding = {'schema_version': 1, 'analysis_snapshot_id': ORIGINAL,
               'resolved_snapshot_id': metadata['snapshot_id'], 'source_sha256': metadata['source_sha256'],
               'analysis_source_sha256': hashlib.sha256(selected_file.read_bytes()).hexdigest(),
               'record_count': 1, 'fields': list(selected)}
    payload = {'run_id': RUN, 'status': 'completed', 'snapshot_id': ORIGINAL,
               'records': [{'id': '001', 'finalCluster': 7, 'similarity': .91,
                            'source': {name: fields[name] for name in ('detailed_description', 'investigation', 'conclusion')}}],
               'clusters': [{'id': 7, 'historicalTitle': 'Historical title', 'historicalSummary': 'Historical context'}]}
    return directory, binding, payload, metadata


def write_binding(directory, binding):
    (directory / 'source_binding.json').write_text(json.dumps(binding), encoding='utf-8')


def test_recovery_requires_explicit_binding_and_preserves_original_provenance(recovery):
    directory, binding, payload, metadata = recovery
    before = deepcopy(payload)
    assert dashboard.source_context(payload)['available'] is False
    write_binding(directory, binding)
    with patch.object(runs, 'results', return_value=payload), \
         patch('map_core.llm.LiveLLM', side_effect=AssertionError('Recovery must not call a model')), TestClient(app) as client:
        response = client.get(f'/api/v1/map/runs/{RUN}/dataset')
    assert response.status_code == 200
    result = response.json()
    assert result['analysis']['sourceAvailable'] is True
    assert result['analysis']['sourceRecordCount'] == 2
    assert result['analysis']['snapshotId'] == ORIGINAL
    assert result['analysis']['resolvedSnapshotId'] == metadata['snapshot_id']
    assert result['analysis']['sourceResolution'] == 'verified_equivalent_export'
    assert result['records'][0]['criticality'] == 'Major'
    assert result['records'][0]['daysOpen'] == 5
    assert result['clusters'][0]['capas'][0]['id'] == 'CAPA-1'
    assert payload == before


@pytest.mark.parametrize('field,value', [
    ('analysis_snapshot_id', 'a' * 64), ('resolved_snapshot_id', 'a' * 64),
    ('source_sha256', 'a' * 64), ('analysis_source_sha256', 'a' * 64),
    ('record_count', 2), ('schema_version', 999),
])
def test_unmatched_or_stale_recovery_is_refused(recovery, field, value):
    directory, binding, payload, _ = recovery
    binding[field] = value
    write_binding(directory, binding)
    assert dashboard.source_context(payload)['available'] is False


@pytest.mark.parametrize('change', ['source_field', 'narrative', 'missing_record', 'duplicate_record'])
def test_recovery_refuses_incomplete_or_different_source_records(recovery, change):
    directory, binding, payload, _ = recovery
    if change == 'source_field':
        path = directory / '01_dpd_closed_deviations.csv'
        path.write_text(path.read_text().replace('2026-01-03', '2025-01-03'), encoding='utf-8')
        binding['analysis_source_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif change == 'narrative':
        payload['records'][0]['source']['investigation'] = 'Different source text.'
    elif change == 'missing_record':
        payload['records'][0]['id'] = 'MISSING'
    else:
        payload['records'].append(deepcopy(payload['records'][0]))
    write_binding(directory, binding)
    assert dashboard.source_context(payload)['available'] is False


def test_exact_original_snapshot_is_preferred_to_equivalent_export(recovery, tmp_path):
    directory, binding, payload, _ = recovery
    original_source = tmp_path / 'original.csv'
    original_source.write_text('PR_ID,SITE,RECORD_TYPE,Criticality\n001,Fictional Site,Deviation,Minor\n', encoding='utf-8')
    original = import_snapshot(original_source, tmp_path / 'sources')
    payload['snapshot_id'] = original.stem
    binding['analysis_snapshot_id'] = original.stem
    write_binding(directory, binding)
    context = dashboard.source_context(payload)
    assert context['resolution'] == 'original_snapshot'
    assert context['metadata']['snapshot_id'] == original.stem
    assert context['fields']['001']['criticality'] == 'Minor'
