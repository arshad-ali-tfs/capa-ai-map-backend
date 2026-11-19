"""CAPA.AI MAP DPD -- local end-to-end implementation (Python 3.11).

Stages mirror the received Dataiku project export.  No proprietary connector
or cloud service is contacted unless the operator explicitly selects --live.
The persisted UMAP and FAISS artifacts are required only at clustering stages.
All generated LLM text remains unreviewed until investigator/SME approval.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from map_core.clustering import cluster_embedded
from map_core.embeddings import generate_embeddings
from map_core.fivewh import extract_fivewh
from map_core.records import prepare_text, raw_to_filtered
from map_core.summaries import apply_review, assess_quality, build_summaries

ROOT = Path(__file__).resolve().parent
DEFAULT_INPUTS = ROOT / 'inputs'
DEFAULT_OUTPUTS = ROOT / 'outputs'


def read_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f'Input file not found: {path}')
    frame = pd.read_csv(path, dtype={'PR_ID': 'string', 'pr_id': 'string'}, low_memory=False)
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    if 'pr_id' not in frame:
        raise ValueError('Input CSV must have PR_ID')
    frame['pr_id'] = frame['pr_id'].astype('string').str.strip()
    if frame['pr_id'].isna().any() or frame['pr_id'].eq('').any() or frame['pr_id'].duplicated().any():
        raise ValueError('PR_ID must be unique and nonempty')
    return frame


def save(frame: pd.DataFrame, path: Path, *, include_vectors: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    result = frame.copy()
    if not include_vectors:
        result = result.loc[:, ~result.columns.str.fullmatch(r'embedding_\d+')]
    result.to_csv(path, index=False)
    print(f'WROTE {path.name}: {len(result):,} rows, {len(result.columns):,} columns')


def save_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding='utf-8')
    print('WROTE', path.name)


def live_client(args: argparse.Namespace):
    if not getattr(args, 'live', False) or not getattr(args, 'confirm_approved_data', False):
        raise ValueError('Live calls require BOTH --live and --confirm-approved-data. Use approved, rotated credentials and approved datasets only.')
    from map_core.llm import LiveLLM
    return LiveLLM(Path(args.outputs) / 'llm_cache')


def filter_stage(gtw: Path, outputs: Path, limit: int, *, allow_any_root: bool,
                 allow_blank_description: bool, include_dr_types=None, exclude_dr_types=None) -> pd.DataFrame:
    filtered, report = raw_to_filtered(gtw, limit=limit, require_root_deviation=not allow_any_root,
                                      require_description=not allow_blank_description,
                                      include_dr_types=include_dr_types, exclude_dr_types=exclude_dr_types)
    save(filtered, outputs / '01_dpd_closed_deviations.csv')
    save_json(report, outputs / '01_filter_report.json')
    if filtered.empty:
        raise ValueError('No eligible DPD records found; inspect 01_filter_report.json and source column values')
    return filtered


def prepare_stage(frame: pd.DataFrame, outputs: Path) -> pd.DataFrame:
    prepared = prepare_text(frame)
    save(prepared, outputs / '02_prepared_text.csv')
    return prepared


def extract_stage(frame: pd.DataFrame, outputs: Path, llm, *, limit: int = 0) -> pd.DataFrame:
    if llm is None:
        raise ValueError('5W1H extraction needs --live --confirm-approved-data. Offline: use the provided 500-row prepared fixture.')
    if limit > 0:
        frame = frame.head(limit).copy()
    parsed = extract_fivewh(frame, llm)
    save(parsed, outputs / '03_extracted_5w1h.csv')
    return parsed


def embedding_stage(frame: pd.DataFrame, outputs: Path, *, model: str,
                    stopwords: str, batch_size: int) -> pd.DataFrame:
    embedded = generate_embeddings(frame, model_name=model, stopwords=stopwords, batch_size=batch_size)
    save(embedded, outputs / '04_bge_embeddings.csv')
    return embedded


def cluster_stage(frame: pd.DataFrame, inputs: Path, outputs: Path, *, mode: str,
                  normalize_input: bool) -> pd.DataFrame:
    clustered, stats = cluster_embedded(frame, inputs, transform_mode=mode,
                                       normalize_input=normalize_input)
    save(clustered, outputs / '05_cluster_assignments.csv')
    save_json(stats, outputs / '05_cluster_run_info.json')
    return clustered


def summary_stage(frame: pd.DataFrame, outputs: Path, *, mode: str, llm=None,
                  reference: Path | None = None, approvals: Path | None = None,
                  quality: bool = False) -> pd.DataFrame:
    summaries = build_summaries(frame, mode=mode, llm=llm, reference=reference)
    final, approved, review_queue = apply_review(frame, summaries, approvals=approvals)
    save(summaries, outputs / '06_cluster_summaries.csv')
    save(review_queue, outputs / '06_manual_review_queue.csv')
    save(final, outputs / '07_map_dpd_final_draft.csv', include_vectors=False)
    save(approved, outputs / '07_approved_only.csv', include_vectors=False)
    if quality:
        if llm is None:
            raise ValueError('Quality assessment requires live approved LLM access')
        assessed = assess_quality(frame, summaries, llm)
        save(assessed, outputs / '06_cluster_quality_sample.csv')
    return final


def add_live_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--live', action='store_true', help='Permit real LLM requests')
    parser.add_argument('--confirm-approved-data', action='store_true',
                        help='Operator confirms dataset and endpoint are approved for this traffic')


def add_embedding_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--model', default='BAAI/bge-large-en-v1.5',
                        help='Pinned BGE model ID or approved local checkpoint directory')
    parser.add_argument('--stopwords', choices=['spacy', 'none'], default='spacy',
                        help='spacy mirrors Dataiku; none is a non-parity development fallback')
    parser.add_argument('--batch-size', type=int, default=32)


def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='CAPA.AI MAP -- local DPD workflow')
    parser.add_argument('--inputs', type=Path, default=DEFAULT_INPUTS)
    parser.add_argument('--outputs', type=Path, default=DEFAULT_OUTPUTS)
    sub = parser.add_subparsers(dest='command', required=True)

    f = sub.add_parser('filter', help='Extract eligible DPD deviations from GTW CSV')
    f.add_argument('--gtw', type=Path, required=True)
    f.add_argument('--limit', type=int, default=20)
    f.add_argument('--allow-any-root', action='store_true')
    f.add_argument('--allow-blank-description', action='store_true')
    f.add_argument('--include-dr-type', action='append', dest='include_dr_types')
    f.add_argument('--exclude-dr-type', action='append', dest='exclude_dr_types')

    p = sub.add_parser('prepare', help='Clean, concatenate and detect source language')
    p.add_argument('--input', type=Path, required=True)

    x = sub.add_parser('extract', help='Translate and extract 5W1H via approved LLM')
    x.add_argument('--input', type=Path, required=True)
    x.add_argument('--limit', type=int, default=5)
    add_live_flags(x)

    e = sub.add_parser('embed', help='Create BGE 1024-dimensional embeddings')
    e.add_argument('--input', type=Path, required=True)
    add_embedding_flags(e)

    c = sub.add_parser('cluster', help='Use original persisted UMAP and FAISS')
    c.add_argument('--input', type=Path, required=True)
    c.add_argument('--mode', choices=['auto', 'transform', 'training-only'], default='auto')
    c.add_argument('--normalize-input', action='store_true',
                   help='Diagnostic only; differs from the successful 500-row baseline')

    s = sub.add_parser('summarize', help='Draft cluster labels, prepare manual quality gate')
    s.add_argument('--input', type=Path, required=True)
    s.add_argument('--mode', choices=['pending', 'live', 'reference'], default='pending')
    s.add_argument('--reference', type=Path, default=None)
    s.add_argument('--approvals', type=Path, default=None)
    s.add_argument('--quality', action='store_true')
    add_live_flags(s)

    fixture = sub.add_parser('fixture', help='Run existing 500-row baseline, no LLM or BGE needed')
    fixture.add_argument('--summary-mode', choices=['pending', 'reference'], default='reference')
    fixture.add_argument('--mode', choices=['auto', 'transform', 'training-only'], default='auto')

    probe = sub.add_parser('probe', help='Synthetic LLM gateway connectivity check; sends NO case data')
    add_live_flags(probe)

    a = sub.add_parser('all', help='All stages from GTW to draft MAP output')
    a.add_argument('--gtw', type=Path, required=True)
    a.add_argument('--limit', type=int, default=5,
                   help='Limit API usage, default 5 records; set an explicit count')
    a.add_argument('--allow-any-root', action='store_true')
    a.add_argument('--allow-blank-description', action='store_true')
    a.add_argument('--include-dr-type', action='append', dest='include_dr_types')
    a.add_argument('--exclude-dr-type', action='append', dest='exclude_dr_types')
    a.add_argument('--approvals', type=Path, default=None)
    a.add_argument('--quality', action='store_true')
    add_embedding_flags(a)
    add_live_flags(a)
    return parser.parse_args()


def main() -> None:
    args = cli()
    inputs, outputs = args.inputs.resolve(), args.outputs.resolve()
    outputs.mkdir(parents=True, exist_ok=True)
    cmd = args.command
    if cmd == 'filter':
        filter_stage(args.gtw, outputs, args.limit, allow_any_root=args.allow_any_root,
                     allow_blank_description=args.allow_blank_description,
                     include_dr_types=args.include_dr_types, exclude_dr_types=args.exclude_dr_types)
    elif cmd == 'prepare':
        prepare_stage(read_csv(args.input), outputs)
    elif cmd == 'extract':
        llm = live_client(args)
        extract_stage(read_csv(args.input), outputs, llm, limit=args.limit)
    elif cmd == 'embed':
        embedding_stage(read_csv(args.input), outputs, model=args.model,
                        stopwords=args.stopwords, batch_size=args.batch_size)
    elif cmd == 'cluster':
        cluster_stage(read_csv(args.input), inputs, outputs, mode=args.mode,
                      normalize_input=args.normalize_input)
    elif cmd == 'summarize':
        llm = live_client(args) if args.mode == 'live' or args.quality else None
        if args.mode == 'live' and llm is None:
            raise ValueError('--mode live requires an LLM')
        summary_stage(read_csv(args.input), outputs, mode=args.mode, llm=llm,
                      reference=args.reference or inputs / 'MAP_DPD_500_Reference_Cluster_Results.csv',
                      approvals=args.approvals, quality=args.quality)
    elif cmd == 'fixture':
        fixture_file = inputs / 'MAP_DPD_500_5W1H_and_Embeddings.csv'
        reference = inputs / 'MAP_DPD_500_Reference_Cluster_Results.csv'
        df = cluster_stage(read_csv(fixture_file), inputs, outputs,
                           mode=args.mode, normalize_input=False)
        summary_stage(df, outputs, mode=args.summary_mode,
                      reference=reference if args.summary_mode == 'reference' else None)
        print('500-row fixture completed. Reference annotations are NOT newly generated by AI.')
    elif cmd == 'probe':
        llm = live_client(args)
        answer = llm.generate('synthetic_probe', 'This is only a synthetic gateway connectivity check. Reply with the single word READY.',
                              use_cache=False)
        print('Synthetic gateway probe response:', answer[:100])
    elif cmd == 'all':
        if args.limit <= 0:
            raise ValueError('End-to-end mode requires --limit >= 1 to bound LLM costs')
        # Construct credentials before reading source or making partial output.
        llm = live_client(args)
        frame = filter_stage(args.gtw, outputs, args.limit,
                             allow_any_root=args.allow_any_root,
                             allow_blank_description=args.allow_blank_description,
                             include_dr_types=args.include_dr_types, exclude_dr_types=args.exclude_dr_types)
        frame = prepare_stage(frame, outputs)
        frame = extract_stage(frame, outputs, llm)
        frame = embedding_stage(frame, outputs, model=args.model,
                                stopwords=args.stopwords, batch_size=args.batch_size)
        frame = cluster_stage(frame, inputs, outputs, mode='auto', normalize_input=False)
        summary_stage(frame, outputs, mode='live', llm=llm,
                      reference=inputs / 'MAP_DPD_500_Reference_Cluster_Results.csv',
                      approvals=args.approvals, quality=args.quality)
        print('End-to-end MAP draft completed. Human review is REQUIRED before publication.')
    else:
        raise ValueError(f'Unknown stage {cmd}')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERROR ({type(exc).__name__}): {exc}', file=sys.stderr)
        raise SystemExit(1)
