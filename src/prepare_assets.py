"""Verify James's August 2026 MAP DPD artifacts and safely unpack the large UMAP ZIP.

Only use with trusted, approved files. SHA-256 confirms correspondence with the
supplied manifest; it is not an independent security signature.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

MANIFEST = 'DPD_PERSISTED_MODEL_MANIFEST.json'
ARTIFACT_PREFIXES = {
    'dpd_umap_model': '.pkl',
    'dpd_umap_select_cols': '.pkl',
    'dpd_faiss_kmeans_centroids': '.pkl',
    'dpd_faiss_centroid_index': '.faiss',
    'dpd_cluster_merge': '.pkl',
}


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def locate(inputs: Path, prefix: str, suffix: str) -> Path | None:
    """Locate original encoded names or simplified names on Windows."""
    matches = sorted(p for p in inputs.glob(prefix + '*' + suffix) if p.is_file())
    if prefix == 'dpd_umap_model':
        preferred = [p for p in matches if p.name == 'dpd_umap_model_verified.pkl']
        if preferred:
            return preferred[0]
    return matches[0] if matches else None


def unpack_umap_zip(inputs: Path, expected: dict) -> Path:
    archive = locate(inputs, 'dpd_umap_model', '.zip')
    if not archive:
        raise FileNotFoundError('Missing large UMAP .zip; copy James\'s ZIP into inputs/')
    target = inputs / 'dpd_umap_model_verified.pkl'  # No colon in the Windows filename.
    print(f'Extracting {archive.name} -> {target.name} (about 529 MiB)...', flush=True)
    with zipfile.ZipFile(archive, 'r') as z:
        files = [i for i in z.infolist() if not i.is_dir()]
        if len(files) != 1 or not files[0].filename.startswith('dpd_umap_model_') or not files[0].filename.endswith('.pkl'):
            raise ValueError('Unexpected UMAP ZIP structure; expected exactly one dpd_umap_model_*.pkl')
        if files[0].file_size != int(expected['size_bytes']):
            raise ValueError('UMAP ZIP member size does not match manifest')
        temporary = target.with_suffix('.partial')
        h = hashlib.sha256()
        try:
            with z.open(files[0]) as source, temporary.open('wb') as dest:
                while True:
                    chunk = source.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    h.update(chunk)
                    dest.write(chunk)
            if h.hexdigest() != expected['sha256']:
                raise ValueError('UMAP ZIP content SHA-256 does not match manifest')
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def verify(inputs: Path, *, do_extract: bool = True) -> dict[str, Path]:
    manifest_path = inputs / MANIFEST
    if not manifest_path.exists():
        raise FileNotFoundError(f'Missing {MANIFEST} inside {inputs}')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    artifacts = {}
    for a in manifest['artifacts']:
        source_name = a['filename']
        prefix = next((p for p in ARTIFACT_PREFIXES if source_name.startswith(p + '_')), None)
        if not prefix:
            raise ValueError(f'Unrecognized artifact in manifest: {source_name}')
        suffix = ARTIFACT_PREFIXES[prefix]
        path = locate(inputs, prefix, suffix)
        if not path and prefix == 'dpd_umap_model' and do_extract:
            path = unpack_umap_zip(inputs, a)
        if not path:
            raise FileNotFoundError(f'Missing {prefix} ({suffix}). Put the file in inputs/')
        size = path.stat().st_size
        if size != a['size_bytes']:
            raise ValueError(f'{path.name}: size mismatch; {size} != {a["size_bytes"]}')
        digest = digest_file(path)
        if digest != a['sha256']:
            raise ValueError(f'{path.name}: SHA256 mismatch! Do not load this file.')
        print(f'OK {prefix}: {size:,} bytes; SHA-256 matched')
        artifacts[prefix] = path
    return artifacts


def check_csvs(inputs: Path) -> tuple[Path, Path]:
    emb = inputs / 'MAP_DPD_500_5W1H_and_Embeddings.csv'
    ref = inputs / 'MAP_DPD_500_Reference_Cluster_Results.csv'
    for p in (emb, ref):
        if not p.exists():
            raise FileNotFoundError(f'Missing {p.name} in inputs/')
        print(f'OK CSV: {p.name} ({p.stat().st_size:,} bytes)')
    return emb, ref


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs', type=Path, default=Path('inputs'))
    parser.add_argument('--skip-csv', action='store_true')
    args = parser.parse_args()
    if not args.skip_csv:
        check_csvs(args.inputs)
    verify(args.inputs)
    print('All supplied artifacts verified. They may now be loaded in a trusted environment.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError) as exc:
        print(f'FAILED: {exc}', file=sys.stderr)
        sys.exit(1)
