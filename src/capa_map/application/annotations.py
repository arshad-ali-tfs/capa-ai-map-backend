"""Versioned record annotations, separate from cluster-summary approvals."""
from filelock import FileLock
from . import runs


def empty():
    return {'assessment': '', 'note': '', 'revision': 0, 'savedAt': None}


def read(run_id):
    directory = runs.folder(run_id)
    with FileLock(str(directory / '.annotations.lock'), timeout=10):
        return runs.read_json(directory / 'record_annotations.json', {'records': {}})['records']


def save(run_id, record_id, assessment, note, expected_revision):
    directory = runs.folder(run_id)
    payload = runs.results(run_id)
    if payload.get('status') != 'completed':
        raise runs.RunBusyError('Annotations require a completed analysis.')
    if record_id not in {row['id'] for row in payload['records']}:
        raise FileNotFoundError(record_id)
    path = directory / 'record_annotations.json'
    with FileLock(str(directory / '.annotations.lock'), timeout=10):
        state = runs.read_json(path, {'records': {}, 'history': []})
        previous = state['records'].get(record_id, empty())
        if previous['revision'] != expected_revision:
            raise runs.RunBusyError('This annotation was updated elsewhere. Refresh to load the latest version before saving.')
        entry = {'assessment': assessment, 'note': note, 'revision': previous['revision'] + 1, 'savedAt': runs.now()}
        state['records'][record_id] = entry
        state['history'].append({'recordId': record_id, **entry})
        runs.write_json(path, state)
        return entry
