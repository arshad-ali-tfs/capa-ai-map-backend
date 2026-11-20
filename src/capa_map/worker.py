"""MAP workers invoke the packaged original Sol3fields/BGE/UMAP/FAISS stages."""
import hashlib
import json
import logging
import os
import re
from pathlib import Path
import sys
from datetime import datetime, timezone
from typing import Literal, Mapping


def _configure_logging(directory: Path):
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(directory / 'worker.log', encoding='utf-8'),
        ],
        force=True,
    )


def _source_configuration(environ: Mapping[str, str] | None = None) -> tuple[Literal['csv', 'table'], str, str]:
    environ = os.environ if environ is None else environ
    table = environ.get('CAPA_SOURCE_TABLE', '')
    if environ.get('CAPA_ENV', 'local') == 'databricks':
        if not table:
            raise ValueError('Databricks mode requires CAPA_SOURCE_TABLE; flat-file sources are disabled.')
        return 'table', table, ''

    configured_csv = environ.get('CAPA_SOURCE_CSV', '')
    configured_snapshot = environ.get('CAPA_SOURCE_SNAPSHOT', '')
    if configured_csv or configured_snapshot:
        if not configured_csv or not configured_snapshot:
            raise ValueError('Configure both CAPA_SOURCE_CSV and CAPA_SOURCE_SNAPSHOT, or configure CAPA_SOURCE_TABLE.')
        return 'csv', configured_csv, configured_snapshot
    if table:
        return 'table', table, ''
    raise ValueError('Configure CAPA_SOURCE_TABLE or both CAPA_SOURCE_CSV and CAPA_SOURCE_SNAPSHOT.')


def run(directory: Path):
    _configure_logging(directory)
    logger = logging.getLogger(__name__)
    run_id = directory.name
    logger.info('worker_started run_id=%s', run_id)
    try:
        request = json.loads((directory / 'request.json').read_text(encoding='utf8'))
    except Exception as error:
        logger.exception('worker_request_read_failed run_id=%s error_type=%s', run_id, type(error).__name__)
        raise
    current_stage = 'Preparing source records'

    def update(status, stage, **extra):
        nonlocal current_stage
        current_stage = stage
        value = {'run_id': request['run_id'], 'status': status, 'stage': stage,
                 'updated_at': datetime.now(timezone.utc).isoformat(), **extra}
        temporary = directory / 'status.tmp'
        temporary.write_text(json.dumps(value), encoding='utf8')
        temporary.replace(directory / 'status.json')
        logger.info('worker_status run_id=%s status=%s stage=%s', request['run_id'], status, stage)

    try:
        update('running', 'Preparing source records')
        logger.info(
            'run_options run_id=%s source_mode=%s limit=%d requested_pr_id_count=%d',
            request['run_id'], request.get('source_mode', 'historical'),
            request['limit'], len(request['pr_ids']),
        )
        import numpy as np
        import pandas as pd
        from map_core.records import raw_to_filtered
        from map_core.llm import LiveLLM
        from map_pipeline import prepare_stage, extract_stage, embedding_stage, cluster_stage, summary_stage, save, save_json
        from capa_map.infrastructure.storage import local_directory, local_file
        from capa_map.infrastructure.data.repository import SqliteRecordRepository
        synthetic = request.get('source_mode') == 'synthetic'
        source_csv_path = None
        source_table = None
        if synthetic:
            from capa_map.infrastructure.synthetic import SYNTHETIC_RECORD
            source = directory / 'synthetic_source.csv'
            pd.DataFrame([SYNTHETIC_RECORD]).to_csv(source, index=False)
            snapshot_id = 'synthetic-fixture-v1'
        else:
            source_kind, source_location, snapshot_location = _source_configuration()
            if source_kind == 'csv':
                logger.info('source_selected run_id=%s source=csv_file', request['run_id'])
                source = local_file(source_location)
                snapshot_id = SqliteRecordRepository(local_file(snapshot_location)).metadata()['snapshot_id']
            else:
                from capa_map.infrastructure.databricks_repo import DatabricksRecordRepository
                logger.info(
                    'source_selected run_id=%s source=databricks_table table=%s',
                    request['run_id'], source_location,
                )
                source_dir = Path(os.environ['CAPA_DATA_DIR']) / 'sources'
                source_dir.mkdir(parents=True, exist_ok=True)
                temporary_source = source_dir / f'.{request["run_id"]}.csv'
                source_table = source_location
                try:
                    DatabricksRecordRepository(source_table).export_csv(
                        temporary_source, require_root_parent=not request.get('allow_any_root', False))
                except Exception:
                    temporary_source.unlink(missing_ok=True)
                    raise
                digest = hashlib.sha256()
                with temporary_source.open('rb') as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b''):
                        digest.update(block)
                snapshot_id = digest.hexdigest()
                source = source_dir / (snapshot_id + '.csv')
                if source.exists():
                    temporary_source.unlink()
                else:
                    temporary_source.replace(source)
                source_csv_path = str(source.resolve())
        assets = local_directory(os.environ['MAP_ASSETS_DIR'])
        logger.info(
            'runtime_paths run_id=%s map_assets_exists=%s hf_home_exists=%s data_dir_exists=%s',
            request['run_id'],
            Path(os.environ['MAP_ASSETS_DIR']).exists(),
            Path(os.environ.get('HF_HOME', '')).exists(),
            Path(os.environ.get('CAPA_DATA_DIR', directory.parent)).exists(),
        )
        frame, report = raw_to_filtered(source, limit=0,
            require_root_deviation=not request.get('allow_any_root', False),
            require_description=not request.get('allow_blank_description', False),
            include_dr_types=request.get('include_dr_types') or None,
            exclude_dr_types=request.get('exclude_dr_types') or None)
        logger.info(
            'source_filter_complete run_id=%s input_rows=%d eligible_rows=%d filter_rules=%s',
            request['run_id'], report['input_rows'], report['filtered_unique_rows'],
            ';'.join(report['rules']),
        )
        eligible_ids = set(frame.pr_id.astype(str))
        if request['pr_ids']:
            frame = frame.loc[frame.pr_id.astype(str).isin(request['pr_ids'])]
        if request['limit'] > 0:
            frame = frame.head(request['limit']).copy()
        report.update(selected_rows=len(frame), requested_pr_ids=request['pr_ids'],
                      unmatched_or_ineligible_requested_pr_ids=sorted(set(request['pr_ids']) - eligible_ids),
                      requested_limit=request['limit'], source_snapshot_id=snapshot_id)
        save(frame, directory / '01_dpd_closed_deviations.csv')
        save_json(report, directory / '01_filter_report.json')
        if frame.empty:
            raise ValueError('No eligible DPD records matched the selection.')
        selected_ids = set(frame.pr_id.astype(str))
        llm = LiveLLM(directory / 'llm_cache')
        model = os.getenv('MAP_BGE_MODEL', 'BAAI/bge-large-en-v1.5')
        metadata = llm.metadata()
        metadata.update(source_snapshot_id=snapshot_id, source_mode=request.get('source_mode', 'historical'),
                        embedding_model=model, options={key: request.get(key, default) for key, default in
                        [('allow_any_root', False), ('allow_blank_description', False), ('include_dr_types', []),
                         ('exclude_dr_types', []), ('stopwords', 'spacy'), ('batch_size', 32), ('cluster_mode', 'auto'),
                         ('normalize_input', False), ('summary_mode', 'live'), ('quality', False)]},
                        inputs=['detailed_description', 'investigation', 'conclusion'])
        if source_csv_path:
            metadata['source_csv_path'] = source_csv_path
            metadata['source_table'] = source_table
        save_json(metadata, directory / 'run_metadata.json')
        frame = prepare_stage(frame, directory)
        update('running', 'Translating and extracting 5W1H')
        frame = extract_stage(frame, directory, llm)
        update('running', 'Embedding extracted events')
        frame = embedding_stage(frame, directory, model=model, stopwords=request.get('stopwords', 'spacy'),
                                batch_size=request.get('batch_size', 32))
        vectors = frame.filter(regex=r'^embedding_\d+$').to_numpy()
        embedding_ok = vectors.shape == (len(frame), 1024) and bool(np.isfinite(vectors).all()) and bool((np.linalg.norm(vectors, axis=1) > 0).all())
        if not embedding_ok:
            raise ValueError('BGE embeddings failed shape or finite-vector validation.')
        update('running', 'Assigning historical clusters')
        frame = cluster_stage(frame, assets, directory, mode=request.get('cluster_mode', 'auto'),
                              normalize_input=request.get('normalize_input', False))
        update('running', 'Preparing cluster summaries')
        final = summary_stage(frame, directory, mode=request.get('summary_mode', 'live'), llm=llm,
                              reference=assets / 'MAP_DPD_500_Reference_Cluster_Results.csv',
                              quality=request.get('quality', False))
        checks = {
            'record_identity_preserved': bool(final.pr_id.is_unique and set(final.pr_id.astype(str)) == selected_ids),
            'fivewh_fields_present': all(bool(final[field].fillna('').astype(str).str.strip().ne('').all()) for field in ('who', 'what', 'when', 'where', 'why', 'how')),
            'embedding_shape_and_finiteness': embedding_ok,
            'finite_cluster_similarity': bool(np.isfinite(pd.to_numeric(final.cluster_similarity, errors='coerce')).all()),
            'all_pending_review': bool(final.review_status.eq('pending').all()),
            'no_vectors_in_final_export': not any(re.fullmatch(r'embedding_\d+', column) for column in pd.read_csv(directory / '07_map_dpd_final_draft.csv', nrows=0).columns),
        }
        validation = {'validation': 'passed' if all(checks.values()) else 'failed', 'checks': checks,
                      'records': len(final), 'source_snapshot_id': snapshot_id, 'model': llm.model,
                      'embedding_shape': list(vectors.shape), 'review_clusters': int(final.final_cluster_id.nunique()),
                      'approved_records': 0, 'all_pending_review': checks['all_pending_review'],
                      'inference_paths': final.inference_path.value_counts().to_dict(), 'credentials_saved': False,
                      'quality_requested': request.get('quality', False),
                      'limitation': 'Structural execution checks only. Generated content and cluster assignments require human review.'}
        save_json(validation, directory / 'validation.json')
        if not all(checks.values()):
            raise ValueError('MAP outputs failed structural validation.')
        update('completed', 'Ready for human review', records=len(final), snapshot_id=snapshot_id,
               model=llm.model, inputs=['detailed_description', 'investigation', 'conclusion'])
    except Exception as error:
        logger.exception(
            'worker_failed run_id=%s stage=%s error_type=%s',
            request.get('run_id', run_id), current_stage, type(error).__name__,
        )
        update('failed', 'Processing failed', error_type=type(error).__name__,
               message='MAP did not complete. Check the local worker log and configuration.')


if __name__ == '__main__':
    run(Path(sys.argv[1]).resolve(strict=True))
