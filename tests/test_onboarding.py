"""Orchestration failure/recovery checks, without external APIs or model downloads."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
from threading import Thread
from unittest.mock import Mock

import pytest

from capa_map import docker_setup as onboarding
from capa_map.application import runs
from capa_map.main import RunRequest


@pytest.fixture
def backend():
    state = {'bodies': [], 'response': 'completed', 'drop_first': False}

    class Handler(BaseHTTPRequestHandler):
        def send(self, value):
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            state['bodies'].append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            if state['drop_first'] and len(state['bodies']) == 1:
                self.close_connection = True
                return
            self.send({'run_id': 'a' * 32, 'status': 'queued'})

        def do_GET(self):
            self.send({'run_id': 'a' * 32, 'status': state['response'], 'stage': 'Fixture', 'records': 42})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', state
    server.shutdown()
    server.server_close()
    thread.join()


def test_all_records_by_default_and_completed_run_reused(tmp_path, backend):
    base, state = backend
    assert onboarding.run_pipeline(base, tmp_path, 'version1', 0, False)['records'] == 42
    onboarding.run_pipeline(base, tmp_path, 'version1', 0, False)
    assert len(state['bodies']) == 1
    assert state['bodies'][0]['limit'] == 0
    assert state['bodies'][0]['summary_mode'] == 'live'
    assert state['bodies'][0]['source_mode'] == 'historical'


def test_response_lost_after_submission_reuses_same_idempotency_key(tmp_path, backend, monkeypatch):
    base, state = backend
    state['drop_first'] = True
    monkeypatch.setattr(onboarding.time, 'sleep', lambda _: None)
    onboarding.run_pipeline(base, tmp_path, 'version1', 0, False)
    assert len(state['bodies']) == 2
    assert state['bodies'][0] == state['bodies'][1]
    assert json.loads((tmp_path / 'pipeline.json').read_text())['run_id'] == 'a' * 32


@pytest.mark.parametrize('status', ['failed', 'cancelled'])
def test_failed_run_is_not_automatically_billed_again(tmp_path, backend, status):
    base, state = backend
    state['response'] = status
    for _ in range(2):
        with pytest.raises(RuntimeError, match='No automatic paid retry'):
            onboarding.run_pipeline(base, tmp_path, 'version1', 0, False)
    assert len(state['bodies']) == 1


def test_new_run_is_explicit_and_respects_record_limit(tmp_path, backend):
    base, state = backend
    onboarding.run_pipeline(base, tmp_path, 'version1', 2, False)
    onboarding.run_pipeline(base, tmp_path, 'version1', 2, True)
    assert len(state['bodies']) == 2
    assert state['bodies'][0]['limit'] == 2
    assert state['bodies'][0]['client_request_id'] != state['bodies'][1]['client_request_id']


def test_new_run_does_not_displace_active_work(tmp_path, backend):
    base, state = backend
    onboarding.write_json(tmp_path / 'pipeline.json', {'identity': 'version1', 'run_id': 'a' * 32})
    state['response'] = 'running'
    with pytest.raises(ValueError, match='still active'):
        onboarding.run_pipeline(base, tmp_path, 'version1', 0, True)
    assert not state['bodies']


def test_api_recovers_same_job_after_lost_post_response(tmp_path, monkeypatch):
    monkeypatch.setenv('CAPA_DATA_DIR', str(tmp_path))
    existing_id = 'b' * 32
    options = RunRequest(limit=0, client_request_id='c' * 32).model_dump()
    directory = runs.root() / existing_id
    directory.mkdir()
    saved = dict(options, run_id=existing_id, created_at='2026-01-01')
    (directory / 'request.json').write_text(json.dumps(saved))
    monkeypatch.setattr(runs, 'history', lambda _: [dict(saved, status='running')])
    spawn = Mock(side_effect=AssertionError('Duplicate worker was started'))
    monkeypatch.setattr(runs.subprocess, 'Popen', spawn)
    assert runs.start(**options)['run_id'] == existing_id
    with pytest.raises(ValueError, match='different analysis options'):
        runs.start(**dict(options, limit=5))
    spawn.assert_not_called()


def test_request_key_rejects_non_uuid_text():
    with pytest.raises(ValueError):
        RunRequest(client_request_id='../outside')


def runtime_fixture(root, payload=b'fictional model bytes'):
    source = root / 'bundle'
    source.mkdir()
    (source / 'model.bin').write_bytes(payload)
    manifest = {'embedding_model': 'fixture', 'embedding_revision': 'fixture-revision', 'files': [
        {'path': 'data/model.bin', 'size_bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}]}
    (root / 'runtime-assets.json').write_text(json.dumps(manifest))
    return source


def test_docker_preparation_copies_only_allowlisted_assets_and_rejects_changed_files(tmp_path):
    source = runtime_fixture(tmp_path)
    (source / 'unrelated.bin').write_bytes(b'exclude')
    target = tmp_path / 'storage'
    onboarding.prepare_files(tmp_path, target, source)
    assert (target / 'model.bin').read_bytes() == b'fictional model bytes'
    assert not (target / 'unrelated.bin').exists()
    (target / 'model.bin').write_bytes(b'modified user file')
    with pytest.raises(ValueError, match='Content does not match'):
        onboarding.prepare_files(tmp_path, target, source)
    assert (target / 'model.bin').read_bytes() == b'modified user file'


def test_docker_manifest_cannot_escape_data_mount(tmp_path):
    source = runtime_fixture(tmp_path)
    manifest = json.loads((tmp_path / 'runtime-assets.json').read_text())
    manifest['files'][0]['path'] = 'data/../../outside'
    (tmp_path / 'runtime-assets.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='escapes'):
        onboarding.prepare_files(tmp_path, tmp_path / 'storage', source)


def test_source_import_keeps_record_accounting_and_reuses_immutable_snapshot(tmp_path, monkeypatch):
    monkeypatch.delenv('CAPA_SOURCE_SNAPSHOT', raising=False)
    data = tmp_path / 'data'
    (data / '.setup').mkdir(parents=True)
    source = tmp_path / 'source.csv'
    source.write_text('pr_id,site,record_type,detailed_description\n001,Fixture,Deviation,Original event\n,Fixture,Deviation,Missing ID\n')
    first = onboarding.prepare_source(data, source)
    second = onboarding.prepare_source(data, source)
    assert first == second
    assert first['accepted_rows'] == 1 and first['quarantined_rows'] == 1
    assert onboarding.read_json(data / '.setup/source.json') == first


def test_fully_accepted_csv_preserves_exact_original_bytes(tmp_path, monkeypatch):
    monkeypatch.delenv('CAPA_SOURCE_SNAPSHOT', raising=False)
    data = tmp_path / 'data'
    (data / '.setup').mkdir(parents=True)
    source = tmp_path / 'source.csv'
    original = b'pr_id,site,record_type,detailed_description\r\n001,Fixture,Deviation,"On 02-Jan-2020, 12 units from B-007 failed."\r\n'
    source.write_bytes(original)
    result = onboarding.prepare_source(data, source)
    assert Path(result['CAPA_SOURCE_CSV']).read_bytes() == original
