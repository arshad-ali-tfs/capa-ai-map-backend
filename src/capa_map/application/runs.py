"""Durable MAP jobs, review revisions and allowlisted result artifacts."""
import csv
from datetime import datetime, timezone
import hashlib
from io import StringIO
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from uuid import uuid4
import psutil
from filelock import FileLock


class RunBusyError(RuntimeError):
    pass


ARTIFACTS = {
    'draft': ('07_map_dpd_final_draft.csv', 'Draft records'),
    'approved': ('07_approved_only.csv', 'Locally approved summaries'),
    'review': ('06_manual_review_queue.csv', 'Cluster review queue'),
    'quality': ('06_cluster_quality_sample.csv', 'Quality sample'),
    'report': ('MAP_CONTROLLED_RUN_REPORT.md', 'Controlled run report'),
    'filter': ('01_dpd_closed_deviations.csv', 'Filtered source records'),
    'filter-report': ('01_filter_report.json', 'Filter report'),
    'prepared': ('02_prepared_text.csv', 'Prepared source text'),
    'extracted': ('03_extracted_5w1h.csv', 'Extracted 5W1H'),
    'embeddings': ('04_bge_embeddings.csv', 'BGE embeddings'),
    'clusters': ('05_cluster_assignments.csv', 'Cluster assignments'),
    'cluster-info': ('05_cluster_run_info.json', 'Cluster diagnostics'),
    'summaries': ('06_cluster_summaries.csv', 'Cluster summaries and proposals'),
    'validation': ('validation.json', 'Run validation'),
    'metadata': ('run_metadata.json', 'Model and prompt provenance'),
    'evidence': ('five_field_evidence_review.csv', 'Field evidence review'),
    'theme-review': ('five_assignment_and_theme_review.csv', 'Assignment and theme review'),
    'assignment-review': ('forty_assignment_review_queue.csv', 'Assignment review queue'),
    'factual-review': ('forty_factual_review_queue.csv', 'Factual review queue'),
    'reference-overlap': ('forty_available_reference_overlap.csv', 'Historical reference overlap'),
    'controlled-metrics': ('controlled_run_metrics.json', 'Controlled validation metrics'),
    'import-provenance': ('import_provenance.json', 'Retained run import provenance'),
    'approvals': ('approvals.csv', 'Applied review decisions'),
    'approvals-template': ('approvals-template.csv', 'Review CSV template'),
}
LEGACY = {
    'corrected_five_fresh_20261111': 'Corrected five-record validation run',
    'corrected_forty_fresh_20261111': 'Corrected 40-record validation run',
}
REVIEW_FILES = {'draft', 'approved', 'review', 'approvals'}
NARRATIVES = ('detailed_description', 'investigation', 'conclusion')
FIVEWH = ('who', 'what', 'when', 'where', 'why', 'how')


def now():
    return datetime.now(timezone.utc).isoformat()


def root() -> Path:
    directory = Path(os.environ['CAPA_DATA_DIR']) / 'map-runs'
    directory.mkdir(parents=True, exist_ok=True)
    return directory.resolve()


def contained(directory: Path, filename: str) -> Path:
    target = directory / filename
    if not target.resolve().is_relative_to(directory.resolve()):
        raise ValueError('Artifact is outside MAP storage')
    return target


def folder(run_id: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{32}', run_id):
        raise ValueError('Invalid run ID')
    target = contained(root(), run_id)
    if not target.is_dir():
        raise FileNotFoundError(run_id)
    return target


def read_json(target: Path, default=None):
    return json.loads(target.read_text(encoding='utf-8-sig')) if target.is_file() else ({} if default is None else default)


def write_json(target: Path, data):
    temporary = target.with_name(target.name + '-' + uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf8')
    temporary.replace(target)


def rows(target: Path) -> list[dict]:
    if not target.is_file():
        return []
    with target.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def _worker(directory: Path):
    process = psutil.Process(int(contained(directory, 'pid').read_text()))
    command = process.cmdline()
    # Both the module and exact job directory must match before controlling a PID.
    matches = 'capa_map.worker' in command and any(
        Path(arg).resolve() == directory.resolve() for arg in command if arg and not arg.startswith('-'))
    return process if process.is_running() and matches else None


def _status(directory: Path) -> dict:
    status_file = contained(directory, 'status.json')
    if not status_file.is_file():
        raise FileNotFoundError('MAP job status is missing')
    current = read_json(status_file)
    if not current.get('status'):
        raise ValueError('MAP job status is invalid')
    if current.get('status') in {'queued', 'running'}:
        try:
            alive = _worker(directory) is not None
        except (OSError, ValueError, psutil.NoSuchProcess, psutil.ZombieProcess):
            alive = False
        except psutil.AccessDenied:
            alive = True
        if not alive:
            # The worker may have committed its terminal state after the first read.
            latest = read_json(status_file)
            if latest.get('status') in {'queued', 'running'}:
                latest.update(status='failed', stage='Worker stopped', message='The worker stopped before completing. Start a new analysis.', updated_at=now())
                write_json(status_file, latest)
            current = latest
    request = read_json(contained(directory, 'request.json'))
    return {**request, **current}


def history(limit: int = 30) -> list[dict]:
    items = []
    for directory in root().iterdir():
        if directory.is_dir() and re.fullmatch(r'[a-f0-9]{32}', directory.name):
            try:
                items.append(_status(folder(directory.name)))
            except (OSError, ValueError):
                continue
    return sorted(items, key=lambda item: item.get('created_at', item.get('updated_at', '')), reverse=True)[:limit]


def start(limit: int, pr_ids: list[str], source_mode: str = 'historical', **options) -> dict:
    if options.get('include_dr_types') and options.get('exclude_dr_types'):
        raise ValueError('Choose either included or excluded DR types.')
    with FileLock(str(root() / '.launch.lock'), timeout=10):
        if any(item['status'] in {'queued', 'running'} for item in history(100000)):
            raise RunBusyError('A MAP analysis is already running. Wait for it to finish.')
        run_id = uuid4().hex
        directory = root() / run_id
        directory.mkdir()
        request = {'run_id': run_id, 'limit': limit, 'pr_ids': pr_ids, 'source_mode': source_mode,
                   'created_at': now(), **options}
        write_json(directory / 'request.json', request)
        write_json(directory / 'status.json', {'run_id': run_id, 'status': 'queued', 'stage': 'Starting'})
        python = os.getenv('CAPA_MAP_PYTHON') or sys.executable
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        try:
            with (directory / 'worker.log').open('wb') as log:
                process = subprocess.Popen([python, '-u', '-m', 'capa_map.worker', str(directory)],
                    stdout=log, stderr=log, env=os.environ.copy(), creationflags=flags)
            (directory / 'pid').write_text(str(process.pid), encoding='utf8')
        except OSError:
            write_json(directory / 'status.json', {'run_id': run_id, 'status': 'failed', 'stage': 'Worker could not start'})
            raise
        return {**request, 'status': 'queued'}


def status(run_id: str) -> dict:
    return _status(folder(run_id))


def cancel(run_id: str) -> dict:
    directory = folder(run_id)
    with FileLock(str(directory / '.review.lock'), timeout=10):
        current = _status(directory)
        if current.get('status') not in {'queued', 'running'}:
            raise RunBusyError('Only a queued or running MAP analysis can be cancelled.')
        try:
            process = _worker(directory)
            if process is None:
                raise RunBusyError('The MAP worker is no longer running.')
            process.terminate()
            try:
                process.wait(timeout=5)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            pass
        except psutil.AccessDenied as error:
            raise RunBusyError('The local MAP worker could not be stopped.') from error
        current.update(status='cancelled', stage='Cancelled by operator', updated_at=now())
        write_json(directory / 'status.json', current)
        return current


def review_state(directory):
    state = read_json(contained(directory, 'review_state.json'), {'revision': 0})
    revision = state.get('revision', 0)
    if not isinstance(revision, int) or revision < 0:
        raise ValueError('Invalid review revision')
    return state


def artifact_path(directory, kind, revision=None):
    if kind == 'review-template':
        kind = 'approvals-template'
    if kind not in ARTIFACTS:
        raise ValueError('Unknown MAP artifact')
    filename = ARTIFACTS[kind][0]
    if kind in REVIEW_FILES:
        revision = review_state(directory)['revision'] if revision is None else revision
        if revision:
            return contained(directory, f'reviews/{revision:06d}/{filename}')
    return contained(directory, filename)


def flag(value):
    return str(value).strip().casefold() in {'true', '1', 'yes'}


def number(value, integer=False):
    try:
        result = float(value)
        return (int(result) if integer else result) if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def results(run_id: str):
    directory = folder(run_id)
    current = status(run_id)
    state = review_state(directory)
    # Failed/cancelled jobs keep diagnostics and stage downloads; never appear completed.
    draft_path = artifact_path(directory, 'draft', state['revision'])
    if current.get('status') == 'completed' and not draft_path.is_file():
        raise FileNotFoundError('Completed MAP output is missing')
    raw = rows(draft_path) if current.get('status') == 'completed' else []
    evidence = rows(contained(directory, ARTIFACTS['evidence'][0]))
    themes = {row.get('pr_id'): row for row in rows(contained(directory, ARTIFACTS['theme-review'][0]))}
    records = []
    for row in raw:
        missing = flag(row.get('historical_label_missing')) or (
            row.get('summary_source') == 'supplied_reference_annotation' and
            (not row.get('cluster_summary', '').strip() or not row.get('detailed_cluster_summary', '').strip()))
        record = {
            'id': row.get('pr_id', ''), 'site': row.get('site', ''), 'type': row.get('dr_type', ''),
            'cluster': number(row.get('cluster_id'), True), 'finalCluster': number(row.get('final_cluster_id'), True),
            'secondCluster': number(row.get('second_cluster_id'), True),
            'similarity': number(row.get('cluster_similarity')), 'margin': number(row.get('cluster_top2_margin')),
            'close': flag(row.get('close_assignment_review')), 'missing': missing,
            'historicalTitle': row.get('cluster_summary', '') if not missing else '',
            'historicalSummary': row.get('detailed_cluster_summary', '') if not missing else '',
            'proposalTitle': row.get('proposed_cluster_summary', ''), 'proposalSummary': row.get('proposed_detailed_cluster_summary', ''),
            'proposalScope': row.get('proposal_scope', ''), 'proposalStatus': row.get('proposal_status', ''),
            'summarySource': row.get('summary_source', ''),
            'fields': {field: row.get(field, '') for field in FIVEWH},
            'source': {field: row.get('source_' + field, row.get(field, '')) for field in NARRATIVES},
            'translatedSource': {field: row.get(field, '') for field in NARRATIVES},
            'translated': flag(row.get('translated_to_english')), 'language': row.get('deviation_language', ''),
            'status': row.get('review_status', 'pending'), 'reviewer': row.get('reviewer', ''), 'reviewNotes': row.get('review_notes', ''),
            'path': row.get('inference_path', ''), 'llmInput': row.get('llm_input_text', ''), 'llmOutput': row.get('llm_output', ''),
            'llmPreprocessingVersion': row.get('llm_preprocessing_version', ''),
            'evidence': [item for item in evidence if item.get('pr_id') == row.get('pr_id')], 'themeReview': themes.get(row.get('pr_id')),
            'assignmentReviewStatus': 'unreviewed', 'reviewScope': 'cluster_summary_only',
        }
        records.append(record)
    groups = {}
    for record in records:
        groups.setdefault(record['finalCluster'], []).append(record)
    clusters = []
    for key, members in sorted(groups.items(), key=lambda item: (item[0] is None, item[0] or 0)):
        first = members[0]
        clusters.append({'id': key, 'count': len(members), 'ids': [item['id'] for item in members],
                         **{field: first[field] for field in ('historicalTitle', 'historicalSummary', 'proposalTitle', 'proposalSummary', 'proposalScope', 'proposalStatus', 'summarySource', 'missing', 'status', 'reviewer', 'reviewNotes')},
                         'close': sum(item['close'] for item in members)})
    downloads = []
    for kind, (filename, label) in ARTIFACTS.items():
        if kind == 'approvals-template' or artifact_path(directory, kind, state['revision']).is_file():
            downloads.append({'kind': kind, 'label': label, 'filename': filename, 'url': f'/api/v1/map/runs/{run_id}/download?kind={kind}'})
    quality_path = contained(directory, ARTIFACTS['quality'][0])
    quality_rows = rows(quality_path)
    metadata_path = contained(directory, ARTIFACTS['metadata'][0])
    validation_path = contained(directory, ARTIFACTS['validation'][0])
    revisions = []
    for revision in range(1, state['revision'] + 1):
        info = contained(directory, f'reviews/{revision:06d}/review_provenance.json')
        if info.is_file():
            revisions.append(read_json(info))
    return {**current, 'items': raw, 'records': records, 'clusters': clusters,
            'stats': {'records': len(records), 'sites': len({item['site'] for item in records}), 'clusters': len(clusters),
                      'close': sum(item['close'] for item in records), 'missing': sum(item['missing'] for item in records),
                      'translated': sum(item['translated'] for item in records),
                      'approved': len(rows(artifact_path(directory, 'approved', state['revision']))) if current.get('status') == 'completed' else 0},
            'metadata': read_json(metadata_path), 'metadata_available': metadata_path.is_file(),
            'validation': read_json(validation_path), 'validation_available': validation_path.is_file(),
            'filter_report': read_json(contained(directory, ARTIFACTS['filter-report'][0])),
            'cluster_run_info': read_json(contained(directory, ARTIFACTS['cluster-info'][0])),
            'review_queue': rows(artifact_path(directory, 'review', state['revision'])), 'quality': quality_rows,
            'assignment_review': rows(contained(directory, ARTIFACTS['assignment-review'][0])),
            'factual_review': rows(contained(directory, ARTIFACTS['factual-review'][0])),
            'reference_overlap': rows(contained(directory, ARTIFACTS['reference-overlap'][0])),
            'controlled_metrics': read_json(contained(directory, ARTIFACTS['controlled-metrics'][0])),
            'import_provenance': read_json(contained(directory, ARTIFACTS['import-provenance'][0])),
            'quality_available': quality_path.is_file(), 'quality_status': ('available' if quality_rows else 'no_assessed_records') if quality_path.is_file() else 'not_run_or_not_retained',
            'downloads': downloads, 'artifacts': downloads, 'review_revision': state['revision'], 'review_history': revisions,
            'review_scope': 'cluster_summary_only', 'assignment_review_status': 'unreviewed',
            'review_notice': 'Local cluster-summary decisions do not approve assignments, generated proposals, formal Quality authorization, or publication.'}


def download(run_id: str, kind='draft') -> Path:
    directory = folder(run_id)
    if kind in {'draft', 'approved', 'review', 'approvals', 'approvals-template', 'review-template'} and status(run_id).get('status') != 'completed':
        raise RunBusyError('Results are available after the analysis completes.')
    target = artifact_path(directory, kind)
    if kind in {'approvals-template', 'review-template'} and not target.is_file():
        with FileLock(str(directory / '.review.lock'), timeout=10):
            if not target.is_file():
                queue = rows(artifact_path(directory, 'review'))
                with target.open('w', encoding='utf8', newline='') as handle:
                    writer = csv.DictWriter(handle, fieldnames=['final_cluster_id', 'review_status', 'reviewer', 'review_notes'])
                    writer.writeheader()
                    writer.writerows({'final_cluster_id': item.get('final_cluster_id', ''), 'review_status': 'pending', 'reviewer': '', 'review_notes': ''} for item in queue)
    if not target.is_file():
        raise FileNotFoundError(kind)
    return target


def review(run_id: str, csv_text: str, expected_revision=None, merge=False):
    directory = folder(run_id)
    if status(run_id).get('status') != 'completed':
        raise RunBusyError('Review requires a completed MAP analysis.')
    if len(csv_text.encode('utf8')) > 2_000_000:
        raise ValueError('Review CSV exceeds 2 MB.')
    from map_core.summaries import apply_review
    import pandas as pd
    with FileLock(str(directory / '.review.lock'), timeout=10):
        state = review_state(directory)
        if expected_revision is not None and expected_revision != state['revision']:
            raise RunBusyError('Review changed since it was loaded. Reload results before saving.')
        # The original assignment and summary artifacts are immutable review inputs.
        assignments = pd.read_csv(contained(directory, ARTIFACTS['clusters'][0]), dtype={'pr_id': 'string'})
        summaries = pd.read_csv(contained(directory, ARTIFACTS['summaries'][0]))
        parsed = list(csv.DictReader(StringIO(csv_text.lstrip('\ufeff'))))
        required = {'final_cluster_id', 'review_status', 'reviewer'}
        if not parsed or not required.issubset(parsed[0]):
            raise ValueError('Approvals CSV requires final_cluster_id,review_status,reviewer and at least one row.')
        submitted_count = len(parsed)
        if merge:
            # Structured edits patch existing decisions; CSV upload replaces the full set.
            submitted_ids = [str(row['final_cluster_id']).strip() for row in parsed]
            if len(submitted_ids) != len(set(submitted_ids)):
                raise ValueError('Approvals must have unique cluster IDs')
            previous = {str(row.get('final_cluster_id', '')).strip(): row for row in rows(artifact_path(directory, 'review'))}
            for row in parsed:
                previous[str(row['final_cluster_id']).strip()] = row
            stream = StringIO()
            writer = csv.DictWriter(stream, fieldnames=['final_cluster_id', 'review_status', 'reviewer', 'review_notes'], extrasaction='ignore')
            writer.writeheader()
            writer.writerows(previous.values())
            csv_text = stream.getvalue()
        revision = state['revision'] + 1
        pending = contained(directory, 'reviews/' + uuid4().hex + '.tmp')
        pending.mkdir(parents=True)
        try:
            approval_path = pending / 'approvals.csv'
            approval_path.write_text(csv_text, encoding='utf8')
            final, approved, queue = apply_review(assignments, summaries, approvals=approval_path)
            for frame, filename in [(final, ARTIFACTS['draft'][0]), (approved, ARTIFACTS['approved'][0]), (queue, ARTIFACTS['review'][0])]:
                frame.loc[:, ~frame.columns.str.fullmatch(r'embedding_\d+')].to_csv(pending / filename, index=False)
            provenance = {'revision': revision, 'created_at': now(), 'approval_sha256': hashlib.sha256(csv_text.encode('utf8')).hexdigest(),
                          'decision_count': len(parsed), 'submitted_decision_count': submitted_count, 'submission_mode': 'patch' if merge else 'full_csv', 'reviewers': sorted({row.get('reviewer', '').strip() for row in parsed if row.get('reviewer', '').strip()}),
                          'scope': 'cluster_summary_only', 'assignment_approval': False, 'proposal_approval': False,
                          'previous_revision': state['revision']}
            write_json(pending / 'review_provenance.json', provenance)
            pending.rename(contained(directory, f'reviews/{revision:06d}'))
            write_json(directory / 'review_state.json', {'revision': revision, 'updated_at': provenance['created_at']})
        finally:
            if pending.exists():
                shutil.rmtree(pending)
    return results(run_id)


def legacy_catalog():
    configured = os.getenv('CAPA_MAP_LEGACY_RUNS_DIR')
    if not configured:
        return {'configured': False, 'items': []}
    source_root = Path(configured).resolve()
    items = []
    for key, title in LEGACY.items():
        candidate = contained(source_root, key)
        run_id = hashlib.sha256(('legacy-map:' + key).encode()).hexdigest()[:32]
        available = candidate.is_dir() and contained(candidate, ARTIFACTS['draft'][0]).is_file()
        items.append({'key': key, 'title': title, 'available': available, 'imported_run_id': run_id if (root() / run_id / 'status.json').is_file() else None})
    return {'configured': True, 'items': items}


def import_legacy(key=None):
    catalog = legacy_catalog()
    if not catalog['configured']:
        raise ValueError('Configure CAPA_MAP_LEGACY_RUNS_DIR to import retained validation runs.')
    if key is not None and key not in LEGACY:
        raise ValueError('Unknown retained validation run.')
    source_root = Path(os.environ['CAPA_MAP_LEGACY_RUNS_DIR']).resolve()
    imported = []
    with FileLock(str(root() / '.launch.lock'), timeout=10):
        for item in catalog['items']:
            if (key is not None and item['key'] != key) or not item['available']:
                continue
            run_id = hashlib.sha256(('legacy-map:' + item['key']).encode()).hexdigest()[:32]
            directory = root() / run_id
            if not directory.exists():
                temporary = root() / (uuid4().hex + '.import')
                temporary.mkdir()
                try:
                    source = contained(source_root, item['key'])
                    hashes = {}
                    for filename, _ in ARTIFACTS.values():
                        origin = contained(source, filename)
                        if origin.is_file():
                            shutil.copyfile(origin, temporary / filename)
                            hashes[filename] = hashlib.sha256((temporary / filename).read_bytes()).hexdigest()
                    review_folder = contained(source_root, 'controlled_validation_20261111')
                    extras = ['controlled_run_metrics.json'] + (['five_field_evidence_review.csv', 'five_assignment_and_theme_review.csv'] if 'five' in item['key'] else ['forty_assignment_review_queue.csv', 'forty_factual_review_queue.csv', 'forty_available_reference_overlap.csv'])
                    for filename in extras:
                        origin = contained(review_folder, filename)
                        if origin.is_file():
                            shutil.copyfile(origin, temporary / filename)
                            hashes[filename] = hashlib.sha256((temporary / filename).read_bytes()).hexdigest()
                    report = source_root.parent / 'MAP_CONTROLLED_RUN_REPORT.md'
                    if report.is_file() and report.resolve().is_relative_to(source_root.parent):
                        shutil.copyfile(report, temporary / report.name)
                        hashes[report.name] = hashlib.sha256((temporary / report.name).read_bytes()).hexdigest()
                    stamp = now()
                    write_json(temporary / 'request.json', {'run_id': run_id, 'title': item['title'], 'source_mode': 'retained_validation', 'created_at': stamp, 'legacy_key': item['key'], 'imported': True})
                    write_json(temporary / 'status.json', {'run_id': run_id, 'status': 'completed', 'stage': 'Retained validation results imported', 'updated_at': stamp, 'records': len(rows(temporary / ARTIFACTS['draft'][0]))})
                    write_json(temporary / 'import_provenance.json', {'legacy_key': item['key'], 'imported_at': stamp, 'sha256': hashes, 'originals_modified': False})
                    temporary.rename(directory)
                finally:
                    if temporary.exists():
                        shutil.rmtree(temporary)
            imported.append(status(run_id))
    return {'items': imported}
