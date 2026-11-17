"""Persisted UMAP/FAISS clustering of already-computed 1024D embeddings."""
from __future__ import annotations
import numpy as np
import pandas as pd
from pathlib import Path
from prepare_assets import verify
from run_map import load_models, transform_persisted_umap, EMBEDDING_COLUMNS


def cluster_embedded(df: pd.DataFrame, inputs: Path, *, transform_mode: str = 'auto',
                     normalize_input: bool = False) -> tuple[pd.DataFrame, dict]:
    frame=df.copy()
    frame.columns=[c.lower() for c in frame.columns]
    if 'pr_id' not in frame:
        raise ValueError('Input needs PR_ID')
    if frame['pr_id'].isna().any() or frame['pr_id'].duplicated().any():
        raise ValueError('Missing/duplicate PR_ID in input')
    missing=[c for c in EMBEDDING_COLUMNS if c not in frame]
    if missing:
        raise ValueError(f'Expected 1024 embedding columns, missing {missing[:4]}')
    X=np.ascontiguousarray(frame[EMBEDDING_COLUMNS].to_numpy(dtype=np.float32))
    if not np.isfinite(X).all() or np.any(np.linalg.norm(X,axis=1)<1e-12):
        raise ValueError('Embeddings have null/nonfinite or zero vectors')
    paths=verify(inputs)
    model,index,merges=load_models(paths)
    coords, training_ids, approximate, ambiguous=transform_persisted_umap(
        X,model,mode=transform_mode,normalize_input=normalize_input,
        pr_ids=frame['pr_id'].astype(str).tolist(),
        row_ids_path=inputs/'dpd_persisted_umap_row_ids.csv')
    scores,cluster_ids=index.search(coords,2)
    predicted=cluster_ids[:,0].astype(int)
    frame['cluster_id']=predicted
    frame['final_cluster_id']=[int(merges.get(int(v),int(v))) for v in predicted]
    frame['cluster_similarity']=scores[:,0]
    frame['cluster_top2_margin']=scores[:,0]-scores[:,1]
    frame['second_cluster_id'] = cluster_ids[:,1].astype(int)
    # Keep legacy columns compatible with exports, but score proximity is not a
    # review trigger. Preserve the scores/margin and the selected cluster.
    frame['close_assignment_review'] = False
    frame['assignment_review_margin_threshold'] = np.nan
    frame['inference_path']=np.where(training_ids>=0,'fitted_training',
        np.where(ambiguous,'ambiguous_transform','umap_transform'))
    frame['inference_ambiguous']=ambiguous
    frame['approximate_input_match']=approximate
    metrics={
        'rows':int(len(frame)), 'cluster_count':int(frame['final_cluster_id'].nunique()),
        'fitted_training_records':int((training_ids>=0).sum()),
        'new_records_transformed':int(((training_ids<0)&(~ambiguous)).sum()),
        'ambiguous_training_vectors':int(ambiguous.sum()),
        'close_assignments_for_review': 0,
        'close_assignment_review_enabled': False,
        'review_margin_threshold': None,
        'input_normalized_before_umap':bool(normalize_input),
        'note':'No retraining performed. Saved UMAP and FAISS state reused.'
    }
    return frame,metrics
