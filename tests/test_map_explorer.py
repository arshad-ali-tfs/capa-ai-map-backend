"""Offline MAP result boundaries and durable cluster-summary review behavior."""
import csv
from io import StringIO
import json
from pathlib import Path
from unittest.mock import Mock, patch
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from capa_map.application import runs
from capa_map.main import app
from map_core.summaries import apply_review


@pytest.fixture
def completed(tmp_path, monkeypatch):
    monkeypatch.setenv('CAPA_DATA_DIR', str(tmp_path))
    run_id = 'b' * 32
    directory = runs.root() / run_id
    directory.mkdir()
    runs.write_json(directory / 'request.json', {'run_id': run_id, 'source_mode': 'synthetic', 'created_at': '2026-01-01'})
    runs.write_json(directory / 'status.json', {'run_id': run_id, 'status': 'completed'})
    assignments = pd.DataFrame([
        {'pr_id': '001', 'site': 'Fictional Site', 'dr_type': 'Deviation', 'cluster_id': 7, 'final_cluster_id': 7, 'second_cluster_id': 8,
         'cluster_similarity': .91, 'cluster_top2_margin': .01, 'close_assignment_review': True, 'translated_to_english': True,
         'deviation_language': 'fr', 'source_detailed_description': 'Texte fictif', 'detailed_description': 'Fictional translated source',
         'llm_input_text': 'DETAILED_DESCRIPTION: Fictional source', 'llm_output': 'WHAT: Fictional event',
         'who': 'Operator', 'what': 'Fictional event', 'when': 'not_available', 'where': 'Fictional Site', 'why': 'not_available', 'how': 'not_available',
         'inference_path': 'umap_transform', 'embedding_0': .2},
        {'pr_id': '002', 'site': 'Fictional Site', 'cluster_id': 8, 'final_cluster_id': 8, 'second_cluster_id': 7,
         'cluster_similarity': .7, 'cluster_top2_margin': .2, 'close_assignment_review': False, 'translated_to_english': False},
    ])
    summaries = pd.DataFrame([
        {'final_cluster_id': 7, 'cluster_size': 1, 'cluster_summary': 'Historical title', 'detailed_cluster_summary': 'Historical description',
         'summary_source': 'supplied_historical_annotation', 'proposed_cluster_summary': 'Unreviewed proposal', 'proposed_detailed_cluster_summary': 'Unreviewed proposal description',
         'proposal_scope': 'current_batch_only', 'proposal_status': 'pending_review', 'historical_label_missing': False},
        {'final_cluster_id': 8, 'cluster_size': 1, 'cluster_summary': '', 'detailed_cluster_summary': '',
         'summary_source': 'historical_annotation_missing', 'proposed_cluster_summary': 'Unreviewed missing-label proposal',
         'proposed_detailed_cluster_summary': 'Unreviewed description', 'proposal_status': 'pending_review', 'historical_label_missing': True},
    ])
    assignments.to_csv(directory / runs.ARTIFACTS['clusters'][0], index=False)
    summaries.to_csv(directory / runs.ARTIFACTS['summaries'][0], index=False)
    final, approved, queue = apply_review(assignments, summaries)
    for data, kind in [(final, 'draft'), (approved, 'approved'), (queue, 'review')]:
        data.drop(columns=['embedding_0'], errors='ignore').to_csv(directory / runs.ARTIFACTS[kind][0], index=False)
    runs.write_json(directory / '01_filter_report.json', {'selected_rows': 2, 'rules': ['STATUS=Closed - Done']})
    runs.write_json(directory / '05_cluster_run_info.json', {'new_records_transformed': 2})
    runs.write_json(directory / 'run_metadata.json', {'model': 'fictional-model', 'prompt_sha256': {'5w1h': 'abc'}})
    return run_id, directory


def test_explorer_preserves_flags_trace_and_explicit_unavailable_data(completed):
    run_id, directory = completed
    payload = runs.results(run_id)
    assert payload['items'][0]['pr_id'] == '001'
    first = payload['records'][0]
    assert (first['close'], first['missing'], first['translated']) == (True, False, True)
    assert first['source']['detailed_description'] == 'Texte fictif'
    assert first['translatedSource']['detailed_description'] == 'Fictional translated source'
    assert first['llmInput'].startswith('DETAILED_DESCRIPTION')
    assert first['secondCluster'] == 8 and first['margin'] == .01
    assert first['assignmentReviewStatus'] == 'unreviewed'
    assert payload['stats'] == {'records': 2, 'sites': 1, 'clusters': 2, 'close': 1, 'missing': 1, 'translated': 1, 'approved': 0}
    assert payload['clusters'][0]['ids'] == ['001']
    assert payload['quality_available'] is False and payload['validation_available'] is False
    assert payload['metadata']['model'] == 'fictional-model'
    assert {'draft', 'approved', 'review', 'approvals-template', 'metadata', 'clusters'}.issubset({item['kind'] for item in payload['downloads']})
    assert runs.number('NaN') is None and runs.number('inf') is None


def test_allowlisted_downloads_and_failed_job_diagnostics(completed):
    run_id, directory = completed
    for kind in ['../../request.json', 'worker.log', 'request', 'pid']:
        with pytest.raises(ValueError):
            runs.download(run_id, kind)
    assert runs.download(run_id, 'metadata').name == 'run_metadata.json'
    template = runs.rows(runs.download(run_id, 'approvals-template'))
    assert [row['final_cluster_id'] for row in template] == ['7', '8']
    assert all(row['review_status'] == 'pending' and not row['reviewer'] for row in template)
    runs.write_json(directory / 'status.json', {'status': 'failed'})
    assert runs.results(run_id)['items'] == []
    assert runs.download(run_id, 'clusters').is_file()
    with pytest.raises(runs.RunBusyError):
        runs.download(run_id)


def test_review_revisions_preserve_originals_and_do_not_approve_assignments_or_missing_labels(completed):
    run_id, directory = completed
    original = (directory / runs.ARTIFACTS['draft'][0]).read_bytes()
    csv_text = 'final_cluster_id,review_status,reviewer,review_notes\n7,approved,Fictional Reviewer,Checked historical summary\n8,approved,Fictional Reviewer,No historical label\n'
    result = runs.review(run_id, csv_text, expected_revision=0)
    assert result['review_revision'] == 1
    assert result['stats']['approved'] == 1
    assert result['records'][0]['status'] == 'approved'
    assert result['records'][0]['close'] is True
    assert result['records'][0]['assignmentReviewStatus'] == 'unreviewed'
    assert result['records'][0]['proposalStatus'] == 'pending_review'
    assert [row['pr_id'] for row in runs.rows(runs.download(run_id, 'approved'))] == ['001']
    assert (directory / runs.ARTIFACTS['draft'][0]).read_bytes() == original
    assert result['review_history'][0]['assignment_approval'] is False
    assert len(result['review_history'][0]['approval_sha256']) == 64
    with pytest.raises(runs.RunBusyError):
        runs.review(run_id, csv_text, expected_revision=0)
    assert runs.review_state(directory)['revision'] == 1
    second = runs.review(run_id, 'final_cluster_id,review_status,reviewer\n7,rejected,Fictional Reviewer\n', 1)
    assert second['review_revision'] == 2 and len(second['review_history']) == 2
    assert [item['status'] for item in second['records']] == ['rejected', 'pending']
    assert (directory / 'reviews/000001/approvals.csv').read_text() == csv_text


@pytest.mark.parametrize('body', [
    'final_cluster_id,review_status,reviewer\n7,approved,\n',
    'final_cluster_id,review_status,reviewer\n7,approved,Reviewer\n7,rejected,Reviewer\n',
    'final_cluster_id,review_status,reviewer\n99,approved,Reviewer\n',
    'final_cluster_id,review_status,reviewer\n7,APPROVED,Reviewer\n',
    'final_cluster_id,review_status\n7,approved\n',
])
def test_invalid_review_never_advances_revision(completed, body):
    run_id, directory = completed
    with pytest.raises(ValueError):
        runs.review(run_id, body, 0)
    assert runs.review_state(directory)['revision'] == 0
    assert not list((directory / 'reviews').glob('000*')) if (directory / 'reviews').exists() else True


def test_api_validation_and_review_conflicts(completed):
    run_id, directory = completed
    with TestClient(app) as client:
        opts = client.get('/api/v1/map/options').json()
        assert opts['defaults']['stopwords'] == 'spacy' and opts['defaults']['normalize_input'] is False
        assert opts['defaults']['batch_size'] == 32 and opts['defaults']['quality'] is False
        for values in [{'limit': -1}, {'batch_size': 0}, {'cluster_mode': 'random'}, {'include_dr_types': ['x'], 'exclude_dr_types': ['y']}]:
            assert client.post('/api/v1/map/runs', json=values).status_code == 422
        with patch.object(runs, 'start', return_value={'status': 'queued'}) as start:
            assert client.post('/api/v1/map/runs', json={'limit': 0, 'batch_size': 8, 'summary_mode': 'pending'}).status_code == 202
            assert start.call_args.kwargs['limit'] == 0 and start.call_args.kwargs['batch_size'] == 8
        response = client.post(f'/api/v1/map/runs/{run_id}/review', json={'approvals': [{'final_cluster_id': 7, 'review_status': 'approved', 'reviewer': 'Fictional Reviewer'}], 'expected_revision': 0})
        assert response.status_code == 200 and response.json()['review_revision'] == 1
        response = client.post(f'/api/v1/map/runs/{run_id}/review', json={'csv': 'final_cluster_id,review_status,reviewer\n7,pending,\n', 'expected_revision': 0})
        assert response.status_code == 409
        assert client.get(f'/api/v1/map/runs/{run_id}/download?kind=worker.log').status_code == 404
        assert client.get('/api/v1/map/runs/not-a-job/results').status_code == 404


def test_legacy_import_is_allowlisted_idempotent_and_independent_of_originals(completed, tmp_path, monkeypatch):
    run_id, directory = completed
    legacy = tmp_path / 'retained'
    legacy.mkdir()
    source = legacy / 'corrected_five_fresh_20261111'
    source.mkdir()
    for filename, _ in runs.ARTIFACTS.values():
        current = directory / filename
        if current.is_file():
            (source / filename).write_bytes(current.read_bytes())
    (source / 'private-unlisted.txt').write_text('Do not import')
    monkeypatch.setenv('CAPA_MAP_LEGACY_RUNS_DIR', str(legacy))
    assert runs.legacy_catalog()['items'][0]['available'] is True
    imported = runs.import_legacy()['items'][0]
    copied = runs.folder(imported['run_id'])
    assert imported['source_mode'] == 'retained_validation'
    assert not (copied / 'private-unlisted.txt').exists()
    assert runs.import_legacy()['items'][0]['run_id'] == imported['run_id']
    for current in source.iterdir():
        current.unlink()
    assert runs.results(imported['run_id'])['stats']['records'] == 2
    with pytest.raises(ValueError):
        runs.import_legacy('../outside')


def test_cancel_controls_only_the_exact_job_worker(completed):
    run_id, directory = completed
    runs.write_json(directory / 'status.json', {'run_id': run_id, 'status': 'running'})
    (directory / 'pid').write_text('123')
    process = Mock()
    process.is_running.return_value = True
    process.cmdline.return_value = ['python', '-m', 'capa_map.worker', str(directory / 'other')]
    with patch.object(runs.psutil, 'Process', return_value=process):
        assert runs.status(run_id)['status'] == 'failed'
        with pytest.raises(runs.RunBusyError):
            runs.cancel(run_id)
        process.terminate.assert_not_called()
    runs.write_json(directory / 'status.json', {'run_id': run_id, 'status': 'running'})
    process.cmdline.return_value = ['python', '-m', 'capa_map.worker', str(directory)]
    with patch.object(runs.psutil, 'Process', return_value=process):
        assert runs.cancel(run_id)['status'] == 'cancelled'
        process.terminate.assert_called_once()
    assert runs.results(run_id)['records'] == []


def test_structured_review_edits_preserve_other_cluster_decisions(completed):
    run_id, directory = completed
    with TestClient(app) as client:
        def save(cluster, status, revision):
            return client.post(f'/api/v1/map/runs/{run_id}/review', json={'approvals': [{'final_cluster_id': cluster, 'review_status': status, 'reviewer': 'Fictional Reviewer'}], 'expected_revision': revision})
        assert save(7, 'approved', 0).status_code == 200
        response = save(8, 'rejected', 1)
        assert response.status_code == 200
        result = response.json()
        assert [row['status'] for row in result['records']] == ['approved', 'rejected']
        assert result['review_history'][-1]['submission_mode'] == 'patch'
        assert {row['final_cluster_id'] for row in runs.rows(runs.download(run_id, 'approvals'))} == {'7', '8'}
        template = runs.rows(runs.download(run_id, 'approvals-template'))
        assert {row['final_cluster_id'] for row in template} == {'7', '8'}
        assert all(row['review_status'] == 'pending' for row in template)


@pytest.mark.parametrize('source_mode,limit,pr_ids,expected', [
    ('historical', 0, [], 2), ('synthetic', 100000, [], 1), ('synthetic', 0, ['FICTIONAL-NOT-ELIGIBLE'], 0),
])
def test_worker_advanced_options_retain_source_eligibility_and_zero_limit(tmp_path, monkeypatch, source_mode, limit, pr_ids, expected):
    """Exercise the worker offline; only model-dependent stages are replaced."""
    import numpy as np
    import map_pipeline
    from capa_map.worker import run
    from capa_map.infrastructure.synthetic import SYNTHETIC_RECORD
    source_rows = []
    for identifier, status in [('FICTIONAL-001', 'Closed - Done'), ('FICTIONAL-002', 'Closed - Done'), ('FICTIONAL-INELIGIBLE', 'Open')]:
        row = {**SYNTHETIC_RECORD, 'PR_ID': identifier, 'STATUS': status}
        source_rows.append(row)
    source = tmp_path / 'fictional.csv'
    pd.DataFrame(source_rows).to_csv(source, index=False)
    snapshot = tmp_path / 'snapshot.sqlite'
    snapshot.write_text('mock repository only')
    assets = tmp_path / 'assets'
    assets.mkdir()
    directory = tmp_path / 'job'
    directory.mkdir()
    request = {'run_id': 'c' * 32, 'source_mode': source_mode, 'limit': limit, 'pr_ids': pr_ids,
               'allow_any_root': False, 'allow_blank_description': False, 'include_dr_types': [], 'exclude_dr_types': [],
               'stopwords': 'none', 'batch_size': 8, 'cluster_mode': 'transform', 'normalize_input': True,
               'summary_mode': 'pending', 'quality': True}
    runs.write_json(directory / 'request.json', request)
    monkeypatch.setenv('CAPA_SOURCE_CSV', str(source))
    monkeypatch.setenv('CAPA_SOURCE_SNAPSHOT', str(snapshot))
    monkeypatch.setenv('MAP_ASSETS_DIR', str(assets))
    fake_llm = Mock(model='fictional-model')
    fake_llm.metadata.return_value = {'model': 'fictional-model'}
    seen = {}
    def prepare(frame, output):
        return frame
    def extract(frame, output, llm):
        for key in ('who', 'what', 'when', 'where', 'why', 'how'):
            frame[key] = 'Fictional ' + key
        return frame
    def embed(frame, output, **options):
        seen['embedding'] = options
        vectors = pd.DataFrame(np.ones((len(frame), 1024)), columns=['embedding_' + str(i) for i in range(1024)])
        return pd.concat([frame.reset_index(drop=True), vectors], axis=1)
    def cluster(frame, inputs, output, **options):
        seen['cluster'] = options
        frame = frame.assign(cluster_id=7, final_cluster_id=7, second_cluster_id=8, cluster_similarity=.9,
                             cluster_top2_margin=.1, close_assignment_review=False, inference_path='offline_test_transform')
        return frame
    with patch('map_core.llm.LiveLLM', return_value=fake_llm) as llm_factory, patch('capa_map.infrastructure.data.repository.SqliteRecordRepository') as repository, \
         patch.object(map_pipeline, 'prepare_stage', side_effect=prepare), patch.object(map_pipeline, 'extract_stage', side_effect=extract), \
         patch.object(map_pipeline, 'embedding_stage', side_effect=embed), patch.object(map_pipeline, 'cluster_stage', side_effect=cluster):
        repository.return_value.metadata.return_value = {'snapshot_id': 'fictional-snapshot'}
        run(directory)
    state = runs.read_json(directory / 'status.json')
    if expected == 0:
        assert state['status'] == 'failed'
        assert runs.read_json(directory / '01_filter_report.json')['selected_rows'] == 0
        llm_factory.assert_not_called()
        return
    assert state['status'] == 'completed' and state['records'] == expected
    filtered = runs.rows(directory / '01_dpd_closed_deviations.csv')
    expected_ids = {'FICTIONAL-001', 'FICTIONAL-002'} if source_mode == 'historical' else {SYNTHETIC_RECORD['PR_ID']}
    assert {row['pr_id'] for row in filtered} == expected_ids
    assert seen['embedding']['stopwords'] == 'none' and seen['embedding']['batch_size'] == 8
    assert seen['cluster'] == {'mode': 'transform', 'normalize_input': True}
    validation = runs.read_json(directory / 'validation.json')
    assert validation['validation'] == 'passed' and all(validation['checks'].values())
    assert validation['quality_requested'] is True
    assert (directory / runs.ARTIFACTS['quality'][0]).is_file()
    fake_llm.generate.assert_not_called()


def test_status_checker_cannot_overwrite_a_concurrent_completion(completed):
    run_id, directory = completed
    runs.write_json(directory / 'status.json', {'run_id': run_id, 'status': 'running'})
    def finished_before_process_check(directory):
        runs.write_json(directory / 'status.json', {'run_id': run_id, 'status': 'completed'})
        return None
    with patch.object(runs, '_worker', side_effect=finished_before_process_check):
        assert runs.status(run_id)['status'] == 'completed'
    assert runs.read_json(directory / 'status.json')['status'] == 'completed'


def test_incomplete_job_directories_are_skipped_and_not_reported_as_completed(tmp_path, monkeypatch):
    monkeypatch.setenv('CAPA_DATA_DIR', str(tmp_path))
    (runs.root() / ('d' * 32)).mkdir()
    assert runs.history() == []
    with pytest.raises(FileNotFoundError):
        runs.status('d' * 32)


def test_reference_summary_and_quality_assessment_preserve_original_semantics(tmp_path):
    from map_pipeline import summary_stage
    frame = pd.DataFrame([{'pr_id': f'FICTIONAL-{i}', 'final_cluster_id': 7, 'who': 'Fictional operator',
                           'what': 'Fictional event', 'when': 'not_available', 'where': 'Fictional site',
                           'why': 'not_available', 'how': 'not_available', 'detailed_description': 'Fictional source'} for i in range(5)])
    reference = tmp_path / 'reference.csv'
    pd.DataFrame([{'final_cluster_id': 7, 'cluster_summary': 'Historical fixture title', 'detailed_cluster_summary': 'Historical fixture summary'}]).to_csv(reference, index=False)
    llm = Mock()
    llm.generate.return_value = '{"fit": true}'
    final = summary_stage(frame, tmp_path / 'reference', mode='reference', reference=reference, llm=llm, quality=True)
    assert final.summary_source.eq('supplied_reference_annotation').all()
    assert final.review_status.eq('pending').all()
    assert 'proposed_cluster_summary' not in final
    assert len(runs.rows(tmp_path / 'reference/06_cluster_quality_sample.csv')) == 1
    assert llm.generate.call_args.args[0] == 'cluster_quality'
    llm.reset_mock()
    pending = summary_stage(frame, tmp_path / 'pending', mode='pending', llm=llm, quality=True)
    assert pending.summary_source.eq('pending_llm_and_human_review').all()
    assert runs.rows(tmp_path / 'pending/06_cluster_quality_sample.csv') == []
    llm.generate.assert_not_called()


def test_reference_missing_labels_and_empty_quality_are_explicit(completed):
    run_id, directory = completed
    frame = pd.read_csv(directory / runs.ARTIFACTS['draft'][0], dtype={'pr_id': 'string'})
    frame['summary_source'] = 'supplied_reference_annotation'
    frame = frame.drop(columns=['historical_label_missing'])
    frame.to_csv(directory / runs.ARTIFACTS['draft'][0], index=False)
    (directory / runs.ARTIFACTS['quality'][0]).write_text('pr_id,final_cluster_id,quality_response\n')
    result = runs.results(run_id)
    assert [record['missing'] for record in result['records']] == [False, True]
    assert result['quality_available'] is True and result['quality_status'] == 'no_assessed_records'


def test_completed_job_without_draft_is_not_an_empty_success(completed):
    run_id, directory = completed
    (directory / runs.ARTIFACTS['draft'][0]).unlink()
    with pytest.raises(FileNotFoundError):
        runs.results(run_id)
    with TestClient(app) as client:
        assert client.get(f'/api/v1/map/runs/{run_id}/results').status_code == 404


