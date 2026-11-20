import json
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from capa_map.application import runs


def completed(tmp_path, monkeypatch):
    monkeypatch.setenv('CAPA_DATA_DIR',str(tmp_path))
    directory=runs.root()/('a'*32);directory.mkdir()
    (directory/'request.json').write_text(json.dumps({'run_id':'a'*32,'created_at':'2026-01-01','source_mode':'synthetic'}))
    (directory/'status.json').write_text(json.dumps({'run_id':'a'*32,'status':'completed'}))
    (directory/'07_map_dpd_final_draft.csv').write_text('pr_id,what,review_status\n001,Five labels,pending\n')
    return directory

def test_completed_run_history_results_and_download(tmp_path,monkeypatch):
    completed(tmp_path,monkeypatch)
    assert runs.history()[0]['source_mode']=='synthetic'
    assert runs.results('a'*32)['items'][0]['pr_id']=='001'
    assert runs.download('a'*32).is_file()

def test_lost_worker_is_failed_instead_of_polling_forever(tmp_path,monkeypatch):
    directory=completed(tmp_path,monkeypatch)
    (directory/'status.json').write_text(json.dumps({'run_id':'a'*32,'status':'running'}))
    (directory/'pid').write_text('2147483647')
    assert runs.status('a'*32)['status']=='failed'
    with pytest.raises(runs.RunBusyError):runs.download('a'*32)

def test_run_ids_cannot_escape_storage(tmp_path,monkeypatch):
    monkeypatch.setenv('CAPA_DATA_DIR',str(tmp_path))
    for value in ['../secrets','A'*32,'abc']:
        with pytest.raises(ValueError):runs.folder(value)

def test_one_active_worker_bounds_memory(tmp_path,monkeypatch):
    monkeypatch.setenv('CAPA_DATA_DIR',str(tmp_path))
    with patch.object(runs,'history',return_value=[{'status':'running'}]), pytest.raises(runs.RunBusyError):runs.start(1,[])
    assert not list(runs.root().glob('*/request.json'))

def test_worker_inherits_app_logs(tmp_path,monkeypatch):
    monkeypatch.setenv('CAPA_DATA_DIR',str(tmp_path))
    with patch.object(runs.subprocess,'Popen',return_value=SimpleNamespace(pid=123)) as process:
        runs.start(1,[])
    assert process.call_args.kwargs.get('stdout') is None
    assert process.call_args.kwargs.get('stderr') is None
