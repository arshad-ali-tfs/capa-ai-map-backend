from fastapi.testclient import TestClient
from capa_map.application import runs
from capa_map.main import app


def test_previous_runs_beyond_default_history_are_selectable(tmp_path, monkeypatch):
    monkeypatch.setenv('CAPA_DATA_DIR', str(tmp_path))
    for index in range(35):
        run_id = f'{index:032x}'
        directory = runs.root() / run_id
        directory.mkdir()
        runs.write_json(directory / 'status.json', {
            'run_id': run_id, 'status': 'completed', 'records': 1,
            'source_mode': 'historical', 'created_at': f'2026-11-17T10:00:{index:02d}Z',
        })
    client = TestClient(app)
    assert len(client.get('/api/v1/map/runs').json()['items']) == 30
    all_runs = client.get('/api/v1/map/runs?limit=100000').json()['items']
    assert len(all_runs) == 35
    assert all_runs[0]['run_id'] == f'{34:032x}'
    assert len(client.get('/api/v1/map/runs?limit=1').json()['items']) == 1
    assert client.get('/api/v1/map/runs?limit=0').status_code == 422
    assert client.get('/api/v1/map/runs?limit=100001').status_code == 422
