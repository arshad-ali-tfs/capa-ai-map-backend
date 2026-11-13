"""Offline worker regression tests for vector export versus provenance metadata."""
import json
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

import map_pipeline
from capa_map.worker import run


@pytest.mark.parametrize('leaked_vector', [None, 'embedding_0', 'embedding_1023'])
def test_worker_preserves_embedding_provenance_and_rejects_exported_vectors(tmp_path, monkeypatch, leaked_vector):
    """Run the real worker and summary export without any model or network calls."""
    assets = tmp_path / 'assets'
    assets.mkdir()
    directory = tmp_path / 'job'
    directory.mkdir()
    request = {
        'run_id': 'e' * 32, 'source_mode': 'synthetic', 'limit': 1, 'pr_ids': [],
        'summary_mode': 'pending',
    }
    (directory / 'request.json').write_text(json.dumps(request), encoding='utf8')
    monkeypatch.setenv('MAP_ASSETS_DIR', str(assets))
    monkeypatch.setenv('MAP_BGE_MODEL', 'fictional-offline-bge-model')
    llm = Mock(model='fictional-offline-model')
    llm.metadata.return_value = {'model': llm.model}
    monkeypatch.setattr('map_core.llm.LiveLLM', Mock(return_value=llm))

    provenance = {
        'embedding_model': 'fictional-offline-bge-model',
        'embedding_preprocessing_version': 'fictional-preprocessing-v1',
        'embedding_input_text': 'Five fictional cartons carried the TEST-B label on 12 May 2025.',
    }

    def extract(frame, output, client):
        assert client is llm
        return frame.assign(**{key: 'Fictional ' + key for key in ('who', 'what', 'when', 'where', 'why', 'how')})

    def embed(frame, output, **options):
        vectors = pd.DataFrame(np.ones((len(frame), 1024)), columns=[f'embedding_{i}' for i in range(1024)])
        return pd.concat([frame.assign(**provenance).reset_index(drop=True), vectors], axis=1)

    def cluster(frame, inputs, output, **options):
        return frame.assign(cluster_id=7, final_cluster_id=7, second_cluster_id=8, cluster_similarity=.9,
                            cluster_top2_margin=.1, close_assignment_review=False, inference_path='offline_test_transform')

    original_summary = map_pipeline.summary_stage

    def summary(frame, output, **options):
        final = original_summary(frame, output, **options)
        if leaked_vector is not None:
            export = output / '07_map_dpd_final_draft.csv'
            exported = pd.read_csv(export)
            exported[leaked_vector] = .5
            exported.to_csv(export, index=False)
        return final

    monkeypatch.setattr(map_pipeline, 'extract_stage', extract)
    monkeypatch.setattr(map_pipeline, 'embedding_stage', embed)
    monkeypatch.setattr(map_pipeline, 'cluster_stage', cluster)
    monkeypatch.setattr(map_pipeline, 'summary_stage', summary)
    run(directory)

    state = json.loads((directory / 'status.json').read_text(encoding='utf8'))
    validation = json.loads((directory / 'validation.json').read_text(encoding='utf8'))
    exported = pd.read_csv(directory / '07_map_dpd_final_draft.csv')
    assert {key: exported.iloc[0][key] for key in provenance} == provenance
    assert all(value for key, value in validation['checks'].items() if key != 'no_vectors_in_final_export')
    assert validation['embedding_shape'] == [1, 1024]
    assert validation['records'] == 1
    if leaked_vector is None:
        assert state['status'] == 'completed' and state['records'] == 1
        assert validation['validation'] == 'passed'
        assert validation['checks']['no_vectors_in_final_export'] is True
        assert not exported.columns.str.fullmatch(r'embedding_\d+').any()
    else:
        assert state['status'] == 'failed'
        assert state['stage'] == 'Processing failed'
        assert validation['validation'] == 'failed'
        assert validation['checks']['no_vectors_in_final_export'] is False
        assert leaked_vector in exported.columns
    llm.generate.assert_not_called()
