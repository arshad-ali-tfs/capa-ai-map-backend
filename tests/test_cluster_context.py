"""Saved analysis context and historical definitions keep separate provenance."""
from copy import deepcopy
import csv
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from capa_map.application import dashboard, runs
from capa_map.main import app
from map_core.catalog import reference_coverage

RUN = 'b' * 32

def group():
    return dict(id=7, historicalTitle='Historical name', historicalSummary='Historical full-cluster context',
                proposalTitle='AI title for current analysis', proposalSummary='Current analysis: TEST-42; 20 mL; 12-May-2025.\r\nOriginal second line.',
                proposalScope='current_batch_only')


def test_current_context_is_primary_while_historical_names_and_content_are_retained():
    saved = group()
    before = deepcopy(saved)
    result = dashboard.cluster_context(saved, RUN, 12)
    assert result['context'] == saved['proposalSummary']
    assert result['contextSource'] == 'current_analysis'
    assert result['title'] == saved['historicalTitle'] and result['titleSource'] == 'historical'
    assert result['analysisContext'] == dict(title=saved['proposalTitle'], summary=saved['proposalSummary'], recordCount=12, runId=RUN)
    assert result['historicalContext'] == dict(title=saved['historicalTitle'], summary=saved['historicalSummary'])
    assert saved == before


def test_missing_historical_catalog_entry_uses_saved_run_title_and_summary():
    saved = group()
    saved.update(historicalTitle='', historicalSummary='')
    result = dashboard.cluster_context(saved, RUN, 8)
    assert result['title'] == saved['proposalTitle'] and result['titleSource'] == 'current_analysis'
    assert result['context'] == saved['proposalSummary'] and result['contextSource'] == 'current_analysis'
    assert result['historicalContext'] is None
    assert result['analysisContext']['recordCount'] == 8


@pytest.mark.parametrize('scope', ['', 'unknown', 'all_historical_records'])
def test_unknown_proposal_scope_is_never_presented_as_current_analysis(scope):
    saved = group()
    saved['proposalScope'] = scope
    result = dashboard.cluster_context(saved, RUN, 1)
    assert result['analysisContext'] is None
    assert result['contextSource'] == 'historical' and result['context'] == saved['historicalSummary']


def test_missing_proposal_summary_uses_history_without_hiding_saved_title():
    saved = group()
    saved['proposalSummary'] = ''
    result = dashboard.cluster_context(saved, RUN, 1)
    assert result['contextSource'] == 'historical'
    assert result['analysisContext']['title'] == saved['proposalTitle']
    assert result['analysisContext']['summary'] == ''


def test_no_saved_context_is_explicit_without_generating_or_inventing_text():
    saved = {'id': 7}
    result = dashboard.cluster_context(saved, RUN, 1)
    assert result['title'] == 'Cluster 7' and result['titleSource'] == 'unavailable'
    assert result['contextSource'] == 'unavailable'
    assert result['analysisContext'] is result['historicalContext'] is None
    assert result['context'] == 'No saved summary is available for this cluster.'


def test_legacy_generated_summary_is_not_mislabelled_as_historical_reference():
    saved = {'id': 7, 'historicalTitle': 'Older AI title', 'historicalSummary': 'Older generated batch text',
             'summarySource': 'openai_generated_unreviewed'}
    result = dashboard.cluster_context(saved, RUN, 3)
    assert result['contextSource'] == result['titleSource'] == 'current_analysis'
    assert result['analysisContext']['summary'] == 'Older generated batch text'
    assert result['historicalContext'] is None


def test_saved_csv_context_reaches_http_contract_without_rewriting_run_files(tmp_path, monkeypatch):
    monkeypatch.setenv('CAPA_DATA_DIR', str(tmp_path))
    directory = runs.root() / RUN
    directory.mkdir(parents=True)
    runs.write_json(directory/'status.json', {'run_id': RUN, 'status': 'completed'})
    draft = directory / runs.ARTIFACTS['draft'][0]
    row = dict(pr_id='001', final_cluster_id=7, cluster_summary='', detailed_cluster_summary='',
               summary_source='historical_annotation_missing', historical_label_missing=True,
               proposed_cluster_summary='Saved batch title', proposed_detailed_cluster_summary='Saved TEST-42; 20 mL on 12-May-2025.',
               proposal_scope='current_batch_only', proposal_status='pending_review')
    with draft.open('w', newline='', encoding='utf8') as handle:
        writer=csv.DictWriter(handle, fieldnames=list(row)); writer.writeheader(); writer.writerow(row)
    before=draft.read_bytes()
    with patch('map_core.llm.LiveLLM', side_effect=AssertionError('Do not call models on dataset reads')), TestClient(app) as client:
        response=client.get(f'/api/v1/map/runs/{RUN}/dataset')
    assert response.status_code == 200
    data=response.json(); cluster=data['clusters'][0]
    assert cluster['contextSource'] == cluster['titleSource'] == 'current_analysis'
    assert cluster['analysisContext']['runId'] == RUN and cluster['analysisContext']['recordCount'] == 1
    assert cluster['context'] == row['proposed_detailed_cluster_summary']
    assert data['records'][0]['warnings']  # Missing catalogue metadata is still disclosed.
    assert draft.read_bytes() == before


def write_catalog(path, data):
    with path.open('w', newline='', encoding='utf8') as handle:
        writer=csv.DictWriter(handle, fieldnames=['final_cluster_id','cluster_summary','detailed_cluster_summary'])
        writer.writeheader()
        for identifier, title, summary in data:
            writer.writerow(dict(final_cluster_id=identifier, cluster_summary=title, detailed_cluster_summary=summary))


def test_catalog_coverage_accounts_for_missing_partial_duplicate_and_unexpected_ids(tmp_path):
    path=tmp_path/'catalog.csv'
    write_catalog(path, [(0,'Title A','Original\nmultiline context'),(0,'Title A','Original\nmultiline context'),(1,'Only title',''),(99,'Unexpected','Summary')])
    before=path.read_bytes()
    report=reference_coverage(path, [0,1,2])
    assert report['status'] == 'incomplete'
    assert report['model_clusters'] == 3 and report['labelled_model_clusters'] == 1
    assert report['missing_cluster_ids'] == [1,2] and report['unexpected_cluster_ids'] == [99]
    assert len(report['reference_sha256']) == 64 and path.read_bytes() == before


def test_catalog_conflicts_and_malformed_ids_are_visible_as_diagnostics(tmp_path):
    path=tmp_path/'catalog.csv'
    write_catalog(path, [(0,'Title','First'),(0,'Title','Second'),('bad','Title','Summary')])
    report=reference_coverage(path, [0,1])
    assert report['status'] == 'invalid' and report['conflicting_cluster_ids'] == [0]
    assert report['invalid_reference_rows'] == 1 and report['missing_cluster_ids'] == [0,1]


def test_missing_and_invalid_catalogues_never_invent_coverage(tmp_path):
    path=tmp_path/'catalog.csv'
    assert reference_coverage(path,[0])['status'] == 'unavailable'
    path.write_text('wrong,columns\n1,2\n')
    assert reference_coverage(path,[0])['status'] == 'invalid'


def test_complete_catalog_supports_final_merged_model_ids(tmp_path):
    path=tmp_path/'catalog.csv'
    write_catalog(path, [(7,'Title','Summary'),(8,'Other','Other context')])
    report=reference_coverage(path,[7,7,8])
    assert report['status'] == 'complete' and report['model_clusters'] == 2
    assert report['missing_cluster_ids'] == [] and report['labelled_model_clusters'] == 2
