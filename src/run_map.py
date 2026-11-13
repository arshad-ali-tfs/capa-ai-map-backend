"""CAPA.AI MAP DPD local parity validation against James's 500-record fixture.

This is the persisted ML stage only, not GTW filtering / GPT-4.1 / BGE.
Original training records reuse model.embedding_ (historical fitted coordinates).
Truly new records use the fitted UMAP transform() method.

Only unpickle files supplied by a trusted owner after SHA-256 verification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import platform
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from prepare_assets import check_csvs, verify

EMBEDDING_COLUMNS = [f'embedding_{i}' for i in range(1024)]
OUTPUT_DIM = 15


def load_data(inputs: Path) -> tuple[np.ndarray, pd.DataFrame, pd.DataFrame]:
    emb_path, ref_path = check_csvs(inputs)
    prepared = pd.read_csv(emb_path, dtype={'PR_ID': 'string'}, low_memory=False)
    reference = pd.read_csv(ref_path, dtype={'PR_ID': 'string'}, low_memory=False)
    if not set(EMBEDDING_COLUMNS).issubset(prepared.columns):
        missing = set(EMBEDDING_COLUMNS) - set(prepared.columns)
        raise ValueError(f'Embedding columns missing: {sorted(missing)[:6]}')
    if not {'PR_ID', 'cluster_id', 'final_cluster_id'}.issubset(reference.columns):
        raise ValueError('Reference must have PR_ID, cluster_id, final_cluster_id')
    for frame, label in ((prepared, 'prepared'), (reference, 'reference')):
        if frame['PR_ID'].isna().any() or frame['PR_ID'].duplicated().any():
            raise ValueError(f'{label} has missing/duplicate PR_ID values')
        frame['PR_ID'] = frame['PR_ID'].str.strip()
    if len(prepared) != len(reference) or set(prepared['PR_ID']) != set(reference['PR_ID']):
        raise ValueError('Embedding and reference PR_ID populations differ')
    data = prepared[EMBEDDING_COLUMNS].apply(pd.to_numeric, errors='coerce')
    X = np.ascontiguousarray(data.to_numpy(dtype=np.float32))
    if not np.isfinite(X).all():
        raise ValueError('Embedding matrix contains NaN or infinite values')
    if np.any(np.linalg.norm(X, axis=1) == 0):
        raise ValueError('Embedding matrix contains zero-length rows')
    return X, prepared, reference


def fingerprint(row: np.ndarray) -> bytes:
    return hashlib.sha256(np.asarray(row, dtype=np.float32).tobytes()).digest()


def locate_training_rows(
    X: np.ndarray, raw_data: np.ndarray, *, decimals: int = 6
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Match 500 vectors to _raw_data without needing model-internal PR_IDs.

    Exact float32 bytes preferred. Rounded lookup is a guarded fallback for CSV
    float serialization; ambiguous matches are never silently assigned.
    Returns (training indices or -1, approximate-match flags, ambiguity flags).
    """
    training = np.asarray(raw_data, dtype=np.float32)
    if training.ndim != 2 or training.shape[1] != X.shape[1]:
        raise ValueError(f'UMAP _raw_data shape incompatible with inputs: {training.shape}')
    exact: dict[bytes, list[int]] = {}
    for i, row in enumerate(training):
        exact.setdefault(fingerprint(row), []).append(i)
    result = np.full(len(X), -1, dtype=np.int64)
    approximate = np.zeros(len(X), dtype=bool)
    ambiguous = np.zeros(len(X), dtype=bool)
    for j, row in enumerate(X):
        candidates = exact.get(fingerprint(row), [])
        if len(candidates) == 1 and np.array_equal(training[candidates[0]], row):
            result[j] = candidates[0]
        elif len(candidates) > 1:
            # Identical vectors may have different fitted UMAP coordinates.
            # Without original row-ID mapping we cannot choose a training row.
            ambiguous[j] = True
    remaining = np.flatnonzero((result < 0) & (~ambiguous))
    if len(remaining):
        coarse: dict[bytes, list[int]] = {}
        for i, row in enumerate(training):
            coarse.setdefault(fingerprint(np.round(row, decimals)), []).append(i)
        for j in remaining:
            row = X[j]
            candidates = coarse.get(fingerprint(np.round(row, decimals)), [])
            valid = [i for i in candidates if np.max(np.abs(training[i] - row)) <= 1e-6]
            if len(valid) == 1:
                result[j], approximate[j] = valid[0], True
            elif len(valid) > 1:
                ambiguous[j] = True
    return result, approximate, ambiguous


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    X = np.ascontiguousarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(X, axis=1)
    if np.any(norms < 1e-12):
        raise ValueError('Zero-length UMAP output row cannot be normalized')
    X /= norms[:, None]
    return X


def transform_persisted_umap(
    X: np.ndarray, model, *, mode: str = 'auto', normalize_input: bool = False,
    pr_ids: list[str] | None = None, row_ids_path: Path | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if normalize_input:
        X = l2_normalize(X)
    ids = np.full(X.shape[0], -1, dtype=np.int64)
    approximate = np.zeros(X.shape[0], dtype=bool)
    ambiguous = np.zeros(X.shape[0], dtype=bool)
    fitted = np.asarray(getattr(model, 'embedding_', np.empty((0, OUTPUT_DIM))), dtype=np.float32)
    raw = getattr(model, '_raw_data', None)
    if mode in ('auto', 'training-only'):
        if raw is None or fitted.ndim != 2 or fitted.shape[1] != OUTPUT_DIM:
            raise ValueError('Persisted UMAP lacks fitted training data/coordinates')
        ids, approximate, ambiguous = locate_training_rows(X, raw)
        if row_ids_path is not None and row_ids_path.exists():
            if pr_ids is None:
                raise ValueError('PR_ID array required when using training row-ID mapping')
            source = pd.read_csv(row_ids_path, dtype='string', low_memory=False)
            id_col = 'pr_id' if 'pr_id' in source.columns else 'PR_ID'
            if id_col not in source.columns or len(source) != len(raw):
                raise ValueError('Invalid persisted UMAP row-ID mapping: expected PR_ID and one row per training vector')
            mapping_ids = source[id_col].astype(str).str.strip()
            if mapping_ids.duplicated().any():
                raise ValueError('Duplicate PR_IDs in persisted UMAP row-ID mapping')
            by_id = {record_id: i for i, record_id in enumerate(mapping_ids)}
            raw_float = np.asarray(raw, dtype=np.float32)
            for j, record_id in enumerate(pr_ids):
                i = by_id.get(str(record_id))
                if i is not None:
                    if np.max(np.abs(raw_float[i] - X[j])) > 1e-6:
                        raise ValueError(f'PR_ID {record_id} training mapping disagrees with embedding values')
                    ids[j], ambiguous[j] = i, False
            print('Used persisted PR_ID-to-training-row mapping:', row_ids_path.name)
    if mode == 'training-only' and np.any(ids == -1):
        raise ValueError(f'{int((ids == -1).sum())} records not in the saved UMAP training data')
    output = np.empty((X.shape[0], OUTPUT_DIM), dtype=np.float32)
    known = ids >= 0
    if known.any():
        output[known] = fitted[ids[known]]
    if (~known).any():
        print(f'Transforming {int((~known).sum())} new rows via persisted UMAP (may take time)...', flush=True)
        output[~known] = np.asarray(model.transform(X[~known]), dtype=np.float32)
    return l2_normalize(output), ids, approximate, ambiguous


def load_models(paths: dict[str, Path]):
    import faiss
    import umap  # ensures module exists before unpickling UMAP estimator

    with paths['dpd_umap_model'].open('rb') as f:
        model = pickle.load(f)
    with paths['dpd_umap_select_cols'].open('rb') as f:
        selected_cols = pickle.load(f)
    with paths['dpd_faiss_kmeans_centroids'].open('rb') as f:
        centroids = np.ascontiguousarray(pickle.load(f), dtype=np.float32)
    with paths['dpd_faiss_centroid_index'].open('rb') as f:
        serialized_bytes = pickle.load(f)
    with paths['dpd_cluster_merge'].open('rb') as f:
        merges = pickle.load(f)
    if selected_cols != [f'umap_col_{i}' for i in range(OUTPUT_DIM)]:
        raise ValueError('Unexpected selected UMAP columns/order')
    if centroids.shape != (70, OUTPUT_DIM):
        raise ValueError(f'Expected (70,15) centroids, got {centroids.shape}')
    if not isinstance(serialized_bytes, np.ndarray) or serialized_bytes.dtype != np.uint8:
        raise ValueError('FAISS artifact should be pickle-wrapped uint8 serialized bytes')
    index = faiss.deserialize_index(np.ascontiguousarray(serialized_bytes))
    if type(index).__name__ != 'IndexFlatIP' or index.d != OUTPUT_DIM or index.ntotal != 70:
        raise ValueError('Unexpected FAISS index type/dimensions/centroids')
    stored = index.reconstruct_n(0, index.ntotal)
    if not np.array_equal(stored, centroids):
        raise ValueError('FAISS index vectors do not exactly match the supplied centroids')
    if not isinstance(merges, dict):
        raise ValueError('Cluster merge mapping must be a dict')
    print('UMAP:', type(model).__name__, '| trained rows:', getattr(model, '_raw_data', np.empty((0, 0))).shape)
    print('FAISS:', type(index).__name__, '| 70 centroids, 15 dimensions')
    return model, index, merges


def compare(
    prepared: pd.DataFrame, reference: pd.DataFrame,
    coords: np.ndarray, index, merges: dict, training_ids: np.ndarray,
    approximate: np.ndarray, ambiguous: np.ndarray, output: Path, tie_tolerance: float
):
    scores, labels = index.search(coords, 2)
    result = pd.DataFrame({
        'PR_ID': prepared['PR_ID'].to_numpy(),
        'input_type': np.where(training_ids >= 0, 'fitted_training',
                         np.where(ambiguous, 'ambiguous_transform', 'umap_transform')),
        'fitted_row_index': training_ids,
        'approximate_embedding_match': approximate,
        'ambiguous_training_match': ambiguous,
        'cluster_id_local': labels[:, 0].astype(int),
        'final_cluster_id_local': [int(merges.get(int(c), int(c))) for c in labels[:, 0]],
        'similarity': scores[:, 0],
        'top2_margin': scores[:, 0] - scores[:, 1],
        'second_cluster_id': labels[:, 1].astype(int),
    })
    expected = reference[['PR_ID', 'cluster_id', 'final_cluster_id']].copy().rename(columns={
        'cluster_id': 'reference_cluster_id', 'final_cluster_id': 'reference_final_cluster_id'
    })
    result = result.merge(expected, on='PR_ID', how='left', validate='one_to_one')
    for c in ('reference_cluster_id', 'reference_final_cluster_id'):
        result[c] = pd.to_numeric(result[c], errors='raise').astype(int)
    result['cluster_match'] = result['cluster_id_local'] == result['reference_cluster_id']
    result['final_cluster_match'] = result['final_cluster_id_local'] == result['reference_final_cluster_id']
    # A mismatch is marked tie-eligible only if the expected cluster has a near-top score.
    import faiss
    centroid_mat = np.empty((index.ntotal, index.d), dtype=np.float32)
    for i in range(index.ntotal):
        centroid_mat[i] = np.asarray(index.reconstruct(i), dtype=np.float32)
    similarities = coords @ centroid_mat.T
    expected_index = result['reference_cluster_id'].to_numpy()
    expected_similarity = similarities[np.arange(len(coords)), expected_index]
    result['near_top_expected_cluster'] = (scores[:, 0] - expected_similarity) <= tie_tolerance
    result['possible_tie'] = (~result['cluster_match']) & result['near_top_expected_cluster']
    output.mkdir(parents=True, exist_ok=True)
    outcsv = output / 'map_dpd_500_parity.csv'
    result.to_csv(outcsv, index=False)
    summary = {
        'rows': len(result),
        'fitted_training_records': int((training_ids >= 0).sum()),
        'new_records_transformed': int(((training_ids < 0) & (~ambiguous)).sum()),
        'ambiguous_training_matches_transformed': int(ambiguous.sum()),
        'strict_cluster_matches': int(result['cluster_match'].sum()),
        'strict_cluster_pct': round(float(result['cluster_match'].mean() * 100), 4),
        'strict_final_cluster_matches': int(result['final_cluster_match'].sum()),
        'strict_final_cluster_pct': round(float(result['final_cluster_match'].mean() * 100), 4),
        'possible_tie_mismatches': int(result['possible_tie'].sum()),
        'tie_tolerance': tie_tolerance,
        'note': 'Reference titles/summaries are provided annotations, not LLM-generated by this script.'
    }
    (output / 'map_dpd_500_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    for k, value in summary.items():
        print(f'{k}: {value}')
    print('Wrote:', outcsv)
    print('Wrote:', output / 'map_dpd_500_summary.json')
    return summary


def main():
    parser = argparse.ArgumentParser(description='Run persisted MAP DPD UMAP/FAISS parity on 500 record fixture')
    parser.add_argument('--inputs', type=Path, default=Path('inputs'))
    parser.add_argument('--outputs', type=Path, default=Path('outputs'))
    parser.add_argument('--mode', choices=['auto', 'training-only', 'transform'], default='auto',
                        help='auto: use saved training coords if matched; transform new rows')
    parser.add_argument('--normalize-input', action='store_true',
                        help='Experiment only: normalize input embeddings BEFORE UMAP; default uses supplied values')
    parser.add_argument('--tie-tolerance', type=float, default=1e-6)
    args = parser.parse_args()

    print('Python:', sys.version.split()[0], '| platform:', platform.platform())
    print('NumPy:', np.__version__, '| Pandas:', pd.__version__)
    X, prepared, reference = load_data(args.inputs)
    print('Fixture:', len(prepared), 'records; embeddings:', X.shape)
    paths = verify(args.inputs)
    model, index, merges = load_models(paths)
    coordinates, ids, approximate, ambiguous = transform_persisted_umap(
        X, model, mode=args.mode, normalize_input=args.normalize_input,
        pr_ids=prepared['PR_ID'].tolist(),
        row_ids_path=args.inputs / 'dpd_persisted_umap_row_ids.csv'
    )
    print('15-D output:', coordinates.shape, '| matched fitted rows:', int((ids >= 0).sum()))
    if ambiguous.any():
        print('WARNING: ambiguous vectors (need original PR_ID-to-training-row mapping):',
              prepared.loc[ambiguous, 'PR_ID'].tolist())
    compare(prepared, reference, coordinates, index, merges, ids, approximate, ambiguous, args.outputs, args.tie_tolerance)
    print('Note: parity against this 500-row fixture is not approval of the full upstream MAP pipeline.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}', file=sys.stderr)
        print('If model unpickling fails, ask James for Dataiku pip freeze / Python environment and recreate it.', file=sys.stderr)
        raise
