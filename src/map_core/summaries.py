"""Generate controlled cluster draft summaries/titles; keep human approval separate.

Dataiku references:
  recipe_from_notebook_dpd_train_summary_concat.py
  compute_dpd_cluster_long_summaries.prompt
  recipe_from_notebook_satya_anuraghthermofisher_coms_Python_notebook_3.py
  compute_dpd_train_cluster_summaries.prompt
"""
from __future__ import annotations
import json
import re
import pandas as pd
from pathlib import Path
from .prompts import long_summary_prompt, title_prompt, quality_prompt


def clean(value) -> str:
    if pd.isna(value): return ''
    return re.sub(r'\s+',' ',str(value)).strip()


def dedup(values: list[str]) -> list[str]:
    seen=set();out=[]
    for value in values:
        val=clean(value)
        key=re.sub(r'[^\w\s]','',val.lower()).strip()
        if key and key not in seen:
            out.append(val);seen.add(key)
    return out


def join_up_to(values: list[str], max_chars: int = 12000) -> str:
    out=[];length=0
    for v in values:
        if length+len(v)+1>max_chars:break
        out.append(v);length+=len(v)+1
    return '\n'.join(out)


def source_record_text(row: pd.Series) -> str:
    raw=clean(row.get('llm_output',''))
    if raw: return raw
    # For 500-row fixture only: these are original 5W1H facts, not invented LLM output.
    return '\n'.join(f'{k.upper()}: {clean(row.get(k,"")) or "N/A"}' for k in ('who','what','when','where','why','how'))


def cluster_groups(df: pd.DataFrame) -> pd.DataFrame:
    if not {'pr_id','final_cluster_id'}.issubset(df):
        raise ValueError('Need PR_ID and final_cluster_id to group')
    out=[]
    for cluster_id,g in df.groupby('final_cluster_id',sort=True,dropna=False):
        records=dedup([source_record_text(r) for _,r in g.iterrows()])
        example='\n'.join(f'{i+1}. {t}' for i,t in enumerate(records[:5]))
        cluster_text=join_up_to(records,max_chars=12000)
        context=(f'Cluster ID: {cluster_id} Cluster size: {len(g)} '
                 f'Representative examples: {example} Cluster records: {cluster_text}')
        out.append({'final_cluster_id':int(cluster_id),'cluster_size':len(g),
                    'unique_record_count':len(records),'representative_examples':example,
                    'cluster_prompt_text_100':context})
    return pd.DataFrame(out)


def build_summaries(df: pd.DataFrame, *, mode: str, llm=None,
                    reference: Path | None = None) -> pd.DataFrame:
    groups=cluster_groups(df)
    if mode not in {'live','pending','reference'}:raise ValueError('Unknown summary mode')
    if mode=='reference':
        if reference is None or not reference.exists():
            raise ValueError('--summary-mode reference requires a reference CSV')
        ann=pd.read_csv(reference,dtype={'PR_ID':'string'})
        needed={'final_cluster_id','cluster_summary','detailed_cluster_summary'}
        if not needed.issubset(ann): raise ValueError('Reference missing title/summary columns')
        # Reference labels apply only to the historical 500-record fixture.
        ann=ann.drop_duplicates('final_cluster_id')
        merged=groups.merge(ann[['final_cluster_id','cluster_summary','detailed_cluster_summary']],
                            on='final_cluster_id',how='left',validate='one_to_one')
        merged['summary_source']='supplied_reference_annotation'
        return merged
    records=[]
    catalog = None
    if mode == 'live' and reference is not None and reference.exists():
        catalog = pd.read_csv(reference)
        required = ['final_cluster_id', 'cluster_summary', 'detailed_cluster_summary']
        if not set(required).issubset(catalog):
            raise ValueError('Historical cluster catalog missing label columns')
        if catalog.groupby('final_cluster_id')[required[1:]].nunique(dropna=False).gt(1).any().any():
            raise ValueError('Conflicting historical labels for the same cluster ID')
        catalog = catalog.drop_duplicates('final_cluster_id').set_index('final_cluster_id')
    for _,r in groups.iterrows():
        data=r.to_dict()
        if mode=='live':
            if llm is None: raise ValueError('Live summary mode requires configured LLM')
            detailed=llm.generate('cluster_long_summary',long_summary_prompt(data['cluster_prompt_text_100']))
            title_context=(f'Cluster ID: {data["final_cluster_id"]} Cluster size: {data["cluster_size"]} '
                           f'100-word cluster summary: {detailed} Representative examples: {data["representative_examples"]}')
            title=llm.generate('cluster_title',title_prompt(title_context))
            source='openai_generated_unreviewed'
        else:
            detailed='';title='';source='pending_llm_and_human_review'
        if mode == 'live':
            data.update(proposed_cluster_summary=title,
                        proposed_detailed_cluster_summary=detailed,
                        proposal_scope='current_batch_only', proposal_status='pending_review')
            if catalog is not None and data['final_cluster_id'] in catalog.index:
                historical = catalog.loc[data['final_cluster_id']]
                title = historical['cluster_summary']
                detailed = historical['detailed_cluster_summary']
                source = 'supplied_historical_annotation'
            else:
                title = detailed = ''
                source = 'historical_annotation_missing'
            data['historical_label_missing'] = source == 'historical_annotation_missing'
        data.update(cluster_summary=title,detailed_cluster_summary=detailed,summary_source=source)
        records.append(data)
    return pd.DataFrame(records)


def apply_review(df: pd.DataFrame, summaries: pd.DataFrame,
                 approvals: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    label_columns = ['final_cluster_id','cluster_summary','detailed_cluster_summary',
                     'summary_source','cluster_size']
    label_columns += [c for c in ('proposed_cluster_summary', 'proposed_detailed_cluster_summary',
                      'proposal_scope', 'proposal_status', 'historical_label_missing') if c in summaries]
    merged=df.merge(summaries[label_columns], on='final_cluster_id',
                    how='left', validate='many_to_one')
    status_map={}; reviewer_map={}; notes_map={}
    if approvals is not None:
        approvals_df=pd.read_csv(approvals,dtype={'final_cluster_id':'Int64'})
        required={'final_cluster_id','review_status','reviewer'}
        if not required.issubset(approvals_df):
            raise ValueError('Approvals CSV requires final_cluster_id,review_status,reviewer')
        if approvals_df['final_cluster_id'].isna().any() or approvals_df['final_cluster_id'].duplicated().any():
            raise ValueError('Approvals must have unique nonnull cluster IDs')
        if approvals_df['review_status'].isna().any() or not approvals_df['review_status'].isin(['approved','rejected','pending']).all():
            raise ValueError('Review status must be approved/rejected/pending')
        approved=approvals_df['review_status'].eq('approved')
        if approvals_df.loc[approved,'reviewer'].fillna('').astype(str).str.strip().eq('').any():
            raise ValueError('Approved clusters must have a named reviewer')
        if not set(approvals_df['final_cluster_id']).issubset(set(summaries['final_cluster_id'])):
            raise ValueError('Approval file contains a cluster not present in output')
        status_map=approvals_df.set_index('final_cluster_id')['review_status'].to_dict()
        reviewer_map=approvals_df.set_index('final_cluster_id')['reviewer'].fillna('').to_dict()
        if 'review_notes' in approvals_df:
            notes_map=approvals_df.set_index('final_cluster_id')['review_notes'].fillna('').to_dict()
    merged['review_status']=merged['final_cluster_id'].map(status_map).fillna('pending')
    merged['reviewer']=merged['final_cluster_id'].map(reviewer_map).fillna('')
    merged['review_notes']=merged['final_cluster_id'].map(notes_map).fillna('')
    # This local label is NOT formal GxP Quality authorization or production publication.
    is_approved=(merged['review_status']=='approved') & merged['reviewer'].ne('')
    has_draft=merged['cluster_summary'].fillna('').ne('') & merged['detailed_cluster_summary'].fillna('').ne('')
    approved=merged[is_approved & has_draft].copy()
    queue=summaries[label_columns].copy()
    queue['review_status']=queue['final_cluster_id'].map(status_map).fillna('pending')
    queue['reviewer']=queue['final_cluster_id'].map(reviewer_map).fillna('')
    queue['review_notes']=queue['final_cluster_id'].map(notes_map).fillna('')
    return merged,approved,queue


def assess_quality(df: pd.DataFrame, summaries: pd.DataFrame, llm, *, sample_frac: float = 0.2, seed: int = 1331) -> pd.DataFrame:
    """Optional LLM fit checks. Report only; never bypass manual gate."""
    joined=df.merge(summaries[['final_cluster_id','detailed_cluster_summary']],on='final_cluster_id',how='left')
    joined=joined[joined['detailed_cluster_summary'].fillna('').ne('')]
    if joined.empty: return pd.DataFrame(columns=['pr_id','final_cluster_id','quality_response'])
    chosen=joined.sample(frac=sample_frac,random_state=seed) if sample_frac < 1 else joined
    rows=[]
    for _,r in chosen.iterrows():
        result=llm.generate('cluster_quality',quality_prompt(
            clean(r.get('detailed_description',source_record_text(r))),
            clean(r['detailed_cluster_summary'])),temperature=0.1)
        try: parsed=json.loads(result)
        except json.JSONDecodeError: parsed={'unparsed_response':result}
        rows.append({'pr_id':r['pr_id'],'final_cluster_id':int(r['final_cluster_id']),
                     'quality_response':json.dumps(parsed,ensure_ascii=False)})
    return pd.DataFrame(rows)
