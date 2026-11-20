"""GTW deviation selection, cleaning and language detection.

Source references:
  compute_closed_only_deviations.sampling
  compute_GTW_Complete_Dataset_filtered.sampling
  recipe_from_notebook_closed_deviations_translation.py
  recipe_from_notebook_division_wise_preprocess.py

These exports are not one connected canonical flow. The local DPD filter is a
conservative intersection of their visible rules and can be configured.
"""
from __future__ import annotations
import re
from pathlib import Path
import pandas as pd

FIELDS = [
    'pr_id','site','status','record_type','psg_division','date_opened','date_closed',
    'date_created','is_closed','responsible_department','client','criticality',
    'dr_type','dr_category','description','detailed_description',
    'investigation','conclusion','process_step','root_parent_record_type',
]
NARRATIVE = ('detailed_description','investigation','conclusion')
LLM_PREPROCESSING_VERSION = 'source-narratives-v2'
BGE_PREPROCESSING_VERSION = 'dataiku-clean-spacy-chunks-v1'


def extraction_text(value: object) -> str:
    """Strip HTML and normalize whitespace without losing numbers or punctuation."""
    if value is None or pd.isna(value):
        return ''
    from bs4 import BeautifulSoup
    return re.sub(r'\s+', ' ', BeautifulSoup(str(value), 'html.parser').get_text(separator=' ')).strip()


def narrative_for_llm(row) -> str:
    return '\n\n'.join(f'{field.upper()}: {extraction_text(row.get(field, ""))}' for field in NARRATIVE)


def clean_text(value: object) -> str:
    """Reproduce Dataiku sequence: lowercase, HTML/URL removal,
    contraction expansion, punctuation/digit removal, whitespace normalization.
    """
    if value is None or pd.isna(value):
        return ''
    from bs4 import BeautifulSoup
    import contractions
    text = str(value).lower()
    text = BeautifulSoup(text, 'html.parser').get_text(separator=' ')
    text = re.sub(r'http\S+|www\S+', '', text)
    text = contractions.fix(text)
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\d+', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def raw_to_filtered(gtw_file: Path, *, limit: int = 0, division: str = 'DPD',
                    require_root_deviation: bool = True,
                    require_description: bool = True,
                    include_dr_types: list[str] | None = None,
                    exclude_dr_types: list[str] | None = None) -> tuple[pd.DataFrame, dict]:
    data = pd.read_csv(gtw_file, dtype='string', low_memory=False, keep_default_na=True)
    data.columns = [str(c).strip().lower() for c in data.columns]
    missing = [c for c in ('pr_id','psg_division','record_type','status') if c not in data]
    if missing:
        raise ValueError(f'GTW missing required columns: {missing}')
    mask = (
        data['psg_division'].fillna('').str.strip().str.upper().eq(division.upper())
        & data['record_type'].fillna('').str.strip().str.casefold().eq('deviation')
        & data['status'].fillna('').str.strip().str.casefold().eq('closed - done')
    )
    if 'is_closed' in data:
        closed = data['is_closed'].fillna('').str.strip().str.casefold()
        mask &= closed.isin({'1', '1.0', 'true', 'yes', 'y'})
    if require_root_deviation:
        if 'root_parent_record_type' not in data:
            raise ValueError('ROOT_PARENT_RECORD_TYPE missing; use --allow-any-root after review')
        mask &= data['root_parent_record_type'].fillna('').str.strip().str.casefold().eq('deviation')
    if require_description:
        if 'detailed_description' not in data:
            raise ValueError('DETAILED_DESCRIPTION required for MAP; not found')
        mask &= data['detailed_description'].fillna('').str.strip().ne('')
    filtered = data.loc[mask].copy()
    filtered['pr_id'] = filtered['pr_id'].astype('string').str.strip()
    filtered = filtered.loc[filtered['pr_id'].notna() & filtered['pr_id'].ne('')]
    n_before_dedup = len(filtered)
    filtered = filtered.drop_duplicates(subset=['pr_id'], keep='first').reset_index(drop=True)
    if include_dr_types and exclude_dr_types:
        raise ValueError('Use either include_dr_types or exclude_dr_types')
    before_type_policy = len(filtered)
    type_counts = filtered.get('dr_type', pd.Series('', index=filtered.index)).fillna('(missing)').value_counts().to_dict()
    if include_dr_types or exclude_dr_types:
        if 'dr_type' not in filtered:
            raise ValueError('DR_TYPE required for configured type policy')
        types = filtered['dr_type'].fillna('').str.strip().str.casefold()
        if include_dr_types:
            filtered = filtered.loc[types.isin([v.strip().casefold() for v in include_dr_types])]
        else:
            filtered = filtered.loc[~types.isin([v.strip().casefold() for v in exclude_dr_types])]
    eligible = len(filtered)
    if limit > 0:
        filtered = filtered.head(limit).copy()
    if 'record_name' in filtered and 'description' not in filtered:
        filtered['description'] = filtered['record_name']
    for col in FIELDS:
        if col not in filtered:
            filtered[col] = pd.NA
    report = {'input_rows': int(len(data)), 'filtered_unique_rows': int(eligible),
              'selected_rows': int(len(filtered)), 'duplicate_pr_ids_dropped': int(n_before_dedup - before_type_policy),
              'dr_type_counts_before_policy': type_counts,
              'include_dr_types': include_dr_types, 'exclude_dr_types': exclude_dr_types,
              'type_policy_excluded_rows': before_type_policy - eligible,
              'dr_type_policy_status': 'explicit_operator_policy' if include_dr_types or exclude_dr_types else 'not_confirmed_no_dr_type_restriction',
              'division': division, 'rules': [
                  f'PSG_Division={division}', 'RECORD_TYPE=Deviation', 'STATUS=Closed - Done',
                  'IS_CLOSED=true/1 when present',
                  'ROOT_PARENT_RECORD_TYPE=Deviation' if require_root_deviation else 'root-parent rule disabled',
                  'DETAILED_DESCRIPTION not empty' if require_description else 'description rule disabled'],
              'source_file': str(gtw_file)}
    return filtered[FIELDS].copy(), report


def prepare_text(filtered: pd.DataFrame, *, language_detection: bool = True) -> pd.DataFrame:
    prepared = filtered.copy()
    for field in NARRATIVE:
        if field not in prepared: prepared[field] = ''
        prepared['source_' + field] = prepared[field]
        prepared[field] = prepared[field].fillna('not available').astype(str)
    prepared['concat_columns'] = (
        prepared['detailed_description'] + ' ' + prepared['investigation'] + ' ' + prepared['conclusion']
    )
    prepared['concat_columns_cleaned'] = prepared['concat_columns'].map(clean_text)
    prepared['llm_input_text'] = prepared.apply(narrative_for_llm, axis=1)
    prepared['llm_preprocessing_version'] = LLM_PREPROCESSING_VERSION
    if language_detection:
        try:
            from langdetect import detect, DetectorFactory
        except ImportError as exc:
            raise RuntimeError('langdetect not installed; install requirements.txt') from exc
        DetectorFactory.seed = 0
        def language(text: str) -> str:
            if not text.strip(): return 'unknown'
            try: return detect(text)
            except Exception: return 'unknown'
        prepared['deviation_language'] = prepared['concat_columns'].map(language)
    else:
        prepared['deviation_language'] = 'unchecked'
    return prepared
