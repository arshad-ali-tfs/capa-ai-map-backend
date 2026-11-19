"""Translation, 5W1H parsing and assembly from prepared GTW text."""
from __future__ import annotations
import re
import pandas as pd
from .records import NARRATIVE, clean_text, narrative_for_llm, LLM_PREPROCESSING_VERSION
from .prompts import translation_prompt, fivewh_prompt

KEYS = ('WHO','WHAT','WHEN','WHERE','WHY','HOW')
PATTERN = re.compile(r'(?s)\b(WHO|WHAT|WHEN|WHERE|WHY|HOW)\s*:\s*(.*?)(?=\s*(?:WHO|WHAT|WHEN|WHERE|WHY|HOW)\s*:|$)',re.I)


def parse_5w1h(text: str) -> dict[str, str]:
    if not text or not isinstance(text,str): raise ValueError('Empty 5W1H response')
    values: dict[str,list[str]]={key:[] for key in KEYS}
    for key,value in PATTERN.findall(text):
        val = value.strip()
        if val: values[key.upper()].append(val)
    if not all(values.values()):
        missing = [k for k,v in values.items() if not v]
        raise ValueError(f'5W1H malformed/missing categories: {missing}')
    result={k:'; '.join(values[k]) for k in KEYS}
    for key,val in result.items():
        if val.casefold() in {'n/a','na','none','not available'}: result[key]='not_available'
    return result


def extract_fivewh(prepared: pd.DataFrame, llm, *, max_source_chars: int | None = None) -> pd.DataFrame:
    results=[]
    for _,r in prepared.iterrows():
        d=r.to_dict()
        for field in NARRATIVE:
            d.setdefault('source_' + field, d.get(field, ''))
        lang=str(d.get('deviation_language','en'))
        if lang not in {'en','unknown','unchecked'}:
            for field in NARRATIVE:
                original=d.get(field,'')
                if original and original != 'not available':
                    d[field] = llm.generate('translate_'+field, translation_prompt(field,lang,str(original)))
            d['concat_columns'] = ' '.join(str(d[f]) for f in NARRATIVE)
            d['concat_columns_cleaned'] = clean_text(d['concat_columns'])
            d['translated_to_english'] = True
        else:
            d['translated_to_english'] = False
        if not any(str(d.get(field, '')).strip() for field in NARRATIVE):
            raise ValueError('No text to extract for PR_ID '+str(d.get('pr_id')))
        source = narrative_for_llm(d)
        if max_source_chars is not None and len(source) > max_source_chars:
            raise ValueError(f'Source exceeds configured extraction limit for PR_ID {d.get("pr_id")}; no silent truncation')
        d['llm_input_text'] = source
        d['llm_preprocessing_version'] = LLM_PREPROCESSING_VERSION
        output=llm.generate('5w1h', fivewh_prompt(source))
        d['llm_output'] = output
        d.update({k.lower():v for k,v in parse_5w1h(output).items()})
        results.append(d)
    return pd.DataFrame(results)


def from_prepared_5w1h(df: pd.DataFrame) -> pd.DataFrame:
    out=df.copy()
    out.columns=[c.lower() for c in out.columns]
    if 'pr_id' not in out: raise ValueError('PR_ID column required')
    for col in ('who','what','when','where','why','how'):
        if col not in out: raise ValueError(f'Expected 5W1H column: {col}')
    # Replicate Dataiku concatenation order (without separators).
    for col in ('who','what','when','where','why','how'):
        out[col] = out[col].fillna('not_available').astype(str)
    out['5w_concat'] = out['what']+out['when']+out['where']+out['why']+out['who']+out['how']
    return out
