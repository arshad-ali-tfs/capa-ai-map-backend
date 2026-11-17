"""Close scores are retained as diagnostics and never trigger record review."""
from unittest.mock import Mock, patch
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from capa_map.application import dashboard, runs
from capa_map.main import app
from map_core import clustering
from run_map import EMBEDDING_COLUMNS


def test_future_clustering_never_flags_tied_or_close_scores(tmp_path):
    source = pd.DataFrame(np.ones((3, 1024)), columns=EMBEDDING_COLUMNS)
    source.insert(0, 'pr_id', ['TIED', 'CLOSE', 'DISTINCT'])
    # Even an obsolete flag in the input must not survive a new run.
    source['close_assignment_review'] = True
    before = source.copy(deep=True)
    scores = np.array([[.99, .99], [.99, .989999], [.99, .7]], dtype=np.float32)
    candidates = np.array([[7, 8], [7, 8], [9, 8]])
    index = Mock()
    index.search.return_value = scores, candidates
    coords = np.ones((3, 2), dtype=np.float32)
    with patch.object(clustering, 'verify', return_value={}), \
         patch.object(clustering, 'load_models', return_value=(Mock(), index, {7: 70})), \
         patch.object(clustering, 'transform_persisted_umap', return_value=(coords, np.full(3, -1), np.zeros(3, bool), np.zeros(3, bool))):
        result, metrics = clustering.cluster_embedded(source, tmp_path)
    pd.testing.assert_frame_equal(source, before)
    assert result.final_cluster_id.tolist() == [70, 70, 9]
    assert result.second_cluster_id.tolist() == [8, 8, 8]
    np.testing.assert_array_equal(result.cluster_similarity, scores[:, 0])
    np.testing.assert_array_equal(result.cluster_top2_margin, scores[:, 0] - scores[:, 1])
    assert not result.close_assignment_review.any()
    assert result.assignment_review_margin_threshold.isna().all()
    assert metrics['close_assignment_review_enabled'] is False
    assert metrics['close_assignments_for_review'] == 0
    assert metrics['review_margin_threshold'] is None


@pytest.mark.parametrize('close,missing,assigned,expected', [
    (True, False, 7, []), (False, False, 7, []),
    (True, True, 7, ['Cluster 7 has no historical name or description']),
    (True, False, None, ['No cluster assignment']),
])
def test_saved_runs_ignore_obsolete_close_flags_but_keep_real_warnings(monkeypatch, close, missing, assigned, expected):
    monkeypatch.delenv('CAPA_DATA_DIR', raising=False)
    payload = {'run_id': 'b'*32, 'status': 'completed', 'created_at': '2026-01-01',
               'records': [{'id': 'OLD-001', 'finalCluster': assigned, 'site': 'Fictional', 'type': 'Deviation',
                            'similarity': .99, 'secondCluster': 8, 'close': close, 'missing': missing,
                            'source': {'detailed_description': 'Fictional event.'}}],
               'clusters': [{'id': 7, 'historicalTitle': 'Fictional title', 'historicalSummary': 'Fictional context'}]}
    with patch.object(runs, 'results', return_value=payload), patch.object(dashboard, 'source_context', return_value={
        'fields': {}, 'capas': [], 'metadata': {}, 'available': False,
    }), TestClient(app) as client:
        response = client.get('/api/v1/map/runs/' + 'b'*32 + '/dataset')
    assert response.status_code == 200
    row = response.json()['records'][0]
    assert len(row['warnings']) == len(expected)
    assert all(fragment in warning for fragment, warning in zip(expected, row['warnings']))
    assert not any('Close assignment' in signal for signal in row['matchSignals'])
    assert row['similarity'] == .99
    assert payload['records'][0]['close'] == close  # Do not rewrite old results.
    if missing:
        assert 'OLD-001' in row['warnings'][0]
        assert 'does not indicate' in row['warnings'][0]
