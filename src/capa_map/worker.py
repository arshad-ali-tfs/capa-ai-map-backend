"""MAP workers invoke the packaged original Sol3fields/BGE/UMAP/FAISS stages."""
import json
import os
import re
from pathlib import Path
import sys
from datetime import datetime, timezone


def run(directory: Path):
    request = json.loads((directory / 'request.json').read_text(encoding='utf8'))

    def update(status, stage, **extra):
        value = {'run_id': request['run_id'], 'status': status, 'stage': stage,
                 'updated_at': datetime.now(timezone.utc).isoformat(), **extra}
        temporary = directory / 'status.tmp'
        temporary.write_text(json.dumps(value), encoding='utf8')
        temporary.replace(directory / 'status.json')

    try:
        update('running', 'Preparing source records')
        import numpy as np
        import pandas as pd
        from map_core.records import raw_to_filtered
        from map_core.llm import LiveLLM
        from map_pipeline import prepare_stage, extract_stage, embedding_stage, cluster_stage, summary_stage, save, save_json
        from capa_map.infrastructure.storage import local_directory, local_file
        from capa_map.infrastructure.data.repository import SqliteRecordRepository
        synthetic = request.get('source_mode') == 'synthetic'
        if synthetic:
            from capa_map.infrastructure.synthetic import SYNTHETIC_RECORD
            source = directory / 'synthetic_source.csv'
            pd.DataFrame([SYNTHETIC_RECORD]).to_csv(source, index=False)
            snapshot_id = 'synthetic-fixture-v1'
        else:
            source = local_file(os.environ['CAPA_SOURCE_CSV'])
            snapshot_id = SqliteRecordRepository(local_file(os.environ['CAPA_SOURCE_SNAPSHOT'])).metadata()['snapshot_id']
        assets = local_directory(os.environ['MAP_ASSETS_DIR'])
        frame, report = raw_to_filtered(source, limit=0,
            require_root_deviation=not request.get('allow_any_root', False),
            require_description=not request.get('allow_blank_description', False),
            include_dr_types=request.get('include_dr_types') or None,
            exclude_dr_types=request.get('exclude_dr_types') or None)
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
        import traceback
        traceback.print_exc()
        update('failed', 'Processing failed', error_type=type(error).__name__,
               message='MAP did not complete. Check the local worker log and configuration.')


if __name__ == '__main__':
    run(Path(sys.argv[1]).resolve(strict=True))
