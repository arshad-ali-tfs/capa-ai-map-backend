"""Exercise the real PowerShell launcher against a local API stub; no model calls."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
from threading import Thread

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'Run-Pipeline.ps1'
RUN_ID = 'a' * 32


@pytest.fixture
def backend():
    state = {'requests': [], 'bodies': [], 'source_ready': True, 'start_status': 202,
             'statuses': ['running', 'completed'], 'poll_error': False}

    class Handler(BaseHTTPRequestHandler):
        def respond(self, code, value):
            body = json.dumps(value).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            state['requests'].append(('GET', self.path))
            if self.path.endswith('/ready'):
                self.respond(200, {'source_ready': state['source_ready']})
            elif state['poll_error']:
                self.respond(503, {'detail': 'Fictional temporary outage'})
            else:
                statuses = state['statuses']
                status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
                self.respond(200, {'run_id': RUN_ID, 'status': status, 'stage': 'Fixture stage',
                                   'records': 42, 'message': 'Fictional failure details'})

        def do_POST(self):
            state['requests'].append(('POST', self.path))
            state['bodies'].append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.respond(state['start_status'], {'run_id': RUN_ID, 'status': 'queued',
                                                'detail': 'A MAP analysis is already running.'})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', state
    server.shutdown()
    server.server_close()
    thread.join()


def launch(url, *args):
    shell = shutil.which('pwsh') or shutil.which('powershell')
    if not shell:
        pytest.skip('PowerShell is required to exercise the launcher')
    result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                             '-File', str(SCRIPT), '-ApiUrl', url, '-PollSeconds', '1', *args],
                            capture_output=True, text=True, timeout=25,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    return result, result.stdout + result.stderr


def test_default_submits_all_eligible_records_once_and_waits_for_results(backend):
    url, state = backend
    result, output = launch(url)
    assert result.returncode == 0, output
    assert state['bodies'] == [{'limit': 0, 'source_mode': 'historical', 'summary_mode': 'live'}]
    assert 'MAP pipeline completed. Processed records: 42' in output
    assert f'{url}/api/v1/map/runs/{RUN_ID}/download?kind=draft' in output


def test_small_test_limit_is_supported(backend):
    url, state = backend
    result, output = launch(url, '-Limit', '5')
    assert result.returncode == 0, output
    assert state['bodies'][0]['limit'] == 5


def test_missing_source_prevents_submission(backend):
    url, state = backend
    state['source_ready'] = False
    result, output = launch(url)
    assert result.returncode != 0
    assert 'source is not ready' in output
    assert not state['bodies']


@pytest.mark.parametrize('terminal', ['failed', 'cancelled'])
def test_unsuccessful_runs_exit_with_error_and_retain_run_id(backend, terminal):
    url, state = backend
    state['statuses'] = [terminal]
    result, output = launch(url)
    assert result.returncode != 0
    assert RUN_ID in output and terminal in output
    assert 'MAP pipeline completed' not in output
    assert len(state['bodies']) == 1


def test_resume_only_monitors_existing_run(backend):
    url, state = backend
    result, output = launch(url, '-RunId', RUN_ID)
    assert result.returncode == 0, output
    assert not state['bodies']
    assert all(path == f'/api/v1/map/runs/{RUN_ID}' for method, path in state['requests'])


def test_status_outage_does_not_resubmit_and_prints_resume_command(backend):
    url, state = backend
    state['poll_error'] = True
    result, output = launch(url)
    assert result.returncode != 0
    assert 'Resume with:' in output and f'-RunId {RUN_ID}' in output
    assert len(state['bodies']) == 1
    assert len([path for method, path in state['requests'] if path.endswith(RUN_ID)]) == 3


def test_busy_backend_does_not_resubmit(backend):
    url, state = backend
    state['start_status'] = 409
    result, output = launch(url)
    assert result.returncode != 0
    assert 'already running' in output
    assert len(state['bodies']) == 1
