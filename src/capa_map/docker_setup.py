"""Container-only source/model preparation and restart-safe pipeline monitoring."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from filelock import FileLock


def digest(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf8')) if path.is_file() else {}


def write_json(path, value):
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf8')
    temporary.replace(path)


def contained(base: Path, relative: str) -> Path:
    target = (base / relative).resolve()
    if PurePosixPath(relative).is_absolute() or not target.is_relative_to(base.resolve()):
        raise ValueError('Runtime asset path escapes its allowed directory')
    return target


def prepare_files(root: Path, data: Path, bundle: Path):
    """Only copy explicitly versioned assets; never replace a user's changed file."""
    from capa_map.bootstrap import verify_bundle
    manifest = read_json(root / 'runtime-assets.json')
    for item in manifest['files']:
        relative = PurePosixPath(item['path'])
        if not relative.parts or relative.parts[0] != 'data':
            raise ValueError('Runtime manifest entries must be under data/')
        asset = str(relative.relative_to('data'))
        target = contained(data, asset)
        if target.exists():
            continue  # Full hash verification below also rejects LFS pointers.
        source = contained(bundle, asset)
        if not source.is_file() or source.stat().st_size != item['size_bytes'] or digest(source) != item['sha256']:
            raise ValueError(f"Missing or damaged repository file: {item['path']}. Check Git LFS access and rerun.")
        print(f'Copying {asset} ({item["size_bytes"] / 1024 / 1024:.1f} MB) into mounted storage...', flush=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.partial')
        with source.open('rb') as incoming, temporary.open('wb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        temporary.replace(target)
    result = verify_bundle(root, data_root=data)
    print(f"Verified {result['verified_files']} source/model files.", flush=True)


def prepare_source(data: Path, source=None, sheet=None):
    from capa_map.infrastructure.data.importer import import_snapshot
    from capa_map.infrastructure.data.repository import SqliteRecordRepository
    from capa_map.infrastructure.data.export import export_csv
    print('Preparing the indexed source snapshot and CSV export; first import can take several minutes on a Windows bind mount.', flush=True)
    configured = os.getenv('CAPA_SOURCE_SNAPSHOT', '').strip()
    input_source = None
    if source:
        input_source = source
        snapshot = import_snapshot(source, data / 'sources', sheet=sheet)
    elif configured and 'REPLACE_WITH_' not in configured:
        snapshot = Path(configured).resolve(strict=True)
    else:
        input_source = data / 'sources/trackwise_records.csv'
        snapshot = import_snapshot(input_source, data / 'sources')
    if not snapshot.is_relative_to(data.resolve()):
        raise ValueError('The configured source snapshot must be inside the mounted data directory.')
    repository = SqliteRecordRepository(snapshot)
    metadata = repository.metadata()
    source_csv = snapshot.with_suffix('.csv')
    if not source_csv.is_file():
        temporary = source_csv.with_suffix('.partial.csv')
        if input_source and input_source.suffix.lower() == '.csv' and metadata['quarantined_rows'] == 0:
            # With every CSV row accepted, the original bytes already are the
            # exact canonical input. Avoid sorting/rereading a large SQLite DB
            # over Docker Desktop's Windows bind mount just to recreate them.
            shutil.copyfile(input_source, temporary)
            if digest(temporary) != metadata['source_sha256']:
                temporary.unlink(missing_ok=True)
                raise ValueError('Source changed after import; no pipeline was started.')
        else:
            export_csv(repository, temporary)
        temporary.replace(source_csv)
    result = {'snapshot_id': metadata['snapshot_id'], 'source_sha256': metadata['source_sha256'],
              'sheet': metadata.get('sheet'), 'accepted_rows': metadata['accepted_rows'],
              'quarantined_rows': metadata['quarantined_rows'],
              'CAPA_SOURCE_SNAPSHOT': str(snapshot), 'CAPA_SOURCE_CSV': str(source_csv)}
    write_json(data / '.setup/source.json', result)
    print(f"Source ready: {metadata['accepted_rows']:,} records, {metadata['quarantined_rows']:,} held at import.", flush=True)
    return result


def smoke_models(data: Path):
    """Run the unchanged real embedding and clustering paths with fictional text."""
    import numpy as np
    import pandas as pd
    from map_core.embeddings import generate_embeddings
    from map_core.clustering import cluster_embedded
    frame = pd.DataFrame([{'pr_id': 'installation-check', 'who': 'Laboratory analyst',
        'what': 'Instrument pressure exceeded the specified operating range during testing.',
        'when': 'During sample testing', 'where': 'Laboratory', 'why': 'Instrument malfunction',
        'how': 'An instrument alarm interrupted the test.'}])
    embedded = generate_embeddings(frame)
    vectors = embedded.filter(regex=r'^embedding_\d+$').to_numpy()
    if vectors.shape != (1, 1024) or not np.isfinite(vectors).all():
        raise ValueError('Offline BGE readiness check failed')
    clustered, _ = cluster_embedded(embedded, data / 'assets')
    if not np.isfinite(pd.to_numeric(clustered['cluster_similarity'])).all():
        raise ValueError('Offline historical cluster readiness check failed')


def prepare(root, data, bundle, source=None, sheet=None):
    prepare_files(root, data, bundle)
    prepare_source(data, source, sheet)
    model_code = ''.join(digest(path) for path in sorted((root / 'src/map_core').glob('*.py')))
    versions = [sys.version, *[importlib.metadata.version(name) for name in
                ('torch', 'spacy', 'sentence-transformers', 'umap-learn', 'faiss-cpu')]]
    identity = hashlib.sha256((digest(root / 'runtime-assets.json') + model_code + repr(versions)).encode()).hexdigest()
    marker = data / '.setup/models.json'
    if read_json(marker).get('identity') != identity:
        print('Checking spaCy, BGE, UMAP and FAISS offline inside Docker. First use can take several minutes.', flush=True)
        smoke_models(data)
        write_json(marker, {'identity': identity, 'passed': True})
    print('Container preparation passed. No paid model calls were made.', flush=True)


def api(base, path, body=None):
    request = Request(base + '/api/v1/map' + path,
        data=None if body is None else json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    try:
        # Internal container HTTP must not go through a corporate outbound proxy.
        with build_opener(ProxyHandler({})).open(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            detail = json.load(error).get('detail', f'HTTP {error.code}')
        except (ValueError, OSError):
            detail = f'HTTP {error.code}'
        raise RuntimeError(f'MAP API: {detail}') from None


def _submit(base: str, runtime: Path, identity: str, limit: int, new_run: bool):
    """Persist the submission key before POST; response loss cannot duplicate work."""
    state_path = runtime / 'pipeline.json'
    state = read_json(state_path)
    if new_run or state.get('identity') != identity:
        if state.get('run_id'):
            previous = api(base, '/runs/' + state['run_id'])
            if previous['status'] in ('queued', 'running'):
                raise ValueError(f"Run {state['run_id']} is still active. Rerun the original command without -NewRun.")
        state = {'identity': identity, 'client_request_id': uuid4().hex, 'limit': limit}
        write_json(state_path, state)
    if not state.get('run_id'):
        request = {'limit': limit, 'source_mode': 'historical', 'summary_mode': 'live',
                   'client_request_id': state['client_request_id']}
        for attempt in range(3):
            try:
                response = api(base, '/runs', request)
                break
            except (URLError, TimeoutError, OSError):
                if attempt == 2:
                    raise RuntimeError('Submission connection interrupted. Rerun the same command to recover safely.') from None
                time.sleep(2)
        state['run_id'] = response['run_id']
        write_json(state_path, state)
    return state['run_id']


def run_pipeline(base: str, runtime: Path, identity: str, limit: int, new_run: bool):
    # Keep the lock only around submission. A detached docker-exec monitor may
    # survive its host terminal, and must not prevent a second monitor resuming.
    with FileLock(str(runtime / 'submission.lock'), timeout=60):
        run_id = _submit(base, runtime, identity, limit, new_run)
    print(f'Run ID: {run_id}. Rerunning this command resumes the same request.', flush=True)
    last, errors = None, 0
    while True:
        try:
            status = api(base, '/runs/' + run_id)
            errors = 0
        except (URLError, OSError, RuntimeError):
            errors += 1
            if errors >= 3:
                raise RuntimeError(f'Cannot monitor {run_id}. Rerun the same command to resume; do not use -NewRun.') from None
            time.sleep(2)
            continue
        progress = (status['status'], status.get('stage'))
        if progress != last:
            print(f"  {status['status']}: {status.get('stage', '')}", flush=True)
            last = progress
        if status['status'] == 'completed':
            print(f"MAP pipeline completed. Processed records: {status.get('records', 'see results')}", flush=True)
            print(f'Saved files inside mounted storage: {runtime.parent / "map-runs" / run_id}', flush=True)
            return status
        if status['status'] in ('failed', 'cancelled'):
            raise RuntimeError(f'Run {run_id} {status["status"]}. Check its worker.log under data/map-runs. '
                               'No automatic paid retry was started. Fix the cause, then explicitly retry with -NewRun.')
        time.sleep(5)


def active_check():
    from capa_map.application.runs import history
    if any(item['status'] in ('queued', 'running') for item in history(100000)):
        raise ValueError('A MAP analysis is active. Let it finish or cancel it through the API before stopping/replacing this container.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run', 'active-check'])
    parser.add_argument('--source', type=Path)
    parser.add_argument('--sheet')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--new-run', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    data = Path(os.environ.get('CAPA_DATA_DIR', '/data')).resolve()
    runtime = data / '.setup'
    runtime.mkdir(parents=True, exist_ok=True)
    if args.action == 'active-check':
        active_check()
        return
    if args.action == 'prepare':
        with FileLock(str(runtime / 'orchestration.lock'), timeout=1):
            prepare(root, data, Path('/bundle'), args.source, args.sheet)
        return
    from map_core.llm import LiveLLM
    model = LiveLLM(runtime / 'config-check/cache')
    settings = {'model': model.metadata(), 'source': read_json(runtime / 'source.json')['snapshot_id'],
                'limit': args.limit, 'workflow': 'historical-live-v1'}
    identity = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
    run_pipeline('http://127.0.0.1:8101', runtime, identity, args.limit, args.new_run)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nMonitor closed. The container and analysis remain running. Rerun the same command to resume.', flush=True)
        raise SystemExit(130)
    except (OSError, ValueError, RuntimeError) as error:
        print(f'MAP setup/run failed: {error}', file=sys.stderr, flush=True)
        raise SystemExit(1)
