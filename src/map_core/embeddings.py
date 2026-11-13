"""Dataiku-compatible BGE embedding preparation for DPD 5W1H.

Source: recipe_from_notebook_dpd_train_embedding.py
1. WHAT+WHEN+WHERE+WHY+WHO+HOW (no separators, as source)
2. HTML/URL/contraction/number cleaning
3. spaCy stopword removal + lemmatization
4. RecursiveCharacterTextSplitter(size=500, overlap=50)
5. mean of unnormalized BGE chunk embeddings (1024 D)
"""
from __future__ import annotations
import os
from pathlib import Path
import numpy as np
import pandas as pd
from .records import clean_text, BGE_PREPROCESSING_VERSION
from .fivewh import from_prepared_5w1h

MODEL = 'BAAI/bge-large-en-v1.5'
DIM = 1024


def normalize_for_bge(text: str, *, mode: str = 'spacy', spacy_pipeline=None) -> str:
    text=clean_text(text)
    if mode == 'none': return text
    if mode != 'spacy': raise ValueError('stopword mode must be spacy or none')
    if spacy_pipeline is None:
        raise ValueError('spaCy language model required for Dataiku-like preparation')
    doc=spacy_pipeline(text)
    return ' '.join(tok.lemma_ for tok in doc if not tok.is_stop and tok.is_alpha)


def generate_embeddings(df: pd.DataFrame, *, model_name: str = MODEL,
                        batch_size: int = 32, stopwords: str = 'spacy') -> pd.DataFrame:
    # Keep downloaded checkpoints within the ignored project directory.
    os.environ.setdefault('HF_HOME', str(Path(__file__).resolve().parents[1] / '.models' / 'huggingface'))
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
    try:
        from sentence_transformers import SentenceTransformer
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError as exc:
        raise RuntimeError('Install BGE dependencies: pip install -r requirements-embeddings.txt') from exc
    nlp=None
    if stopwords=='spacy':
        try:
            import spacy
            nlp=spacy.load('en_core_web_sm')
        except (ImportError, OSError) as exc:
            raise RuntimeError('spaCy en_core_web_sm missing. Install/download approved model or use --stopwords none (changes historical preprocessing).') from exc
    table=from_prepared_5w1h(df)
    table['bge_preprocessing_version'] = BGE_PREPROCESSING_VERSION
    table['embedding_model'] = model_name
    table['embedding_stopwords_mode'] = stopwords
    table['5w_concat_cleaned'] = table['5w_concat'].map(lambda x: normalize_for_bge(x,mode=stopwords,spacy_pipeline=nlp))
    if table['5w_concat_cleaned'].str.strip().eq('').any():
        bad=table.loc[table['5w_concat_cleaned'].str.strip().eq(''),'pr_id'].head().tolist()
        raise ValueError(f'Empty BGE input after text cleaning for PR_ID examples {bad}')
    print('Loading embedding model:',model_name,flush=True)
    model=SentenceTransformer(model_name,trust_remote_code=False)
    if model.get_sentence_embedding_dimension()!=DIM:
        raise ValueError(f'BGE model returned {model.get_sentence_embedding_dimension()}D, expected {DIM}D')
    splitter=RecursiveCharacterTextSplitter(chunk_size=500,chunk_overlap=50,
        length_function=len,separators=['\n\n','\n',' ',''])
    rows=[]
    for _,r in table.iterrows():
        chunks=splitter.split_text(r['5w_concat_cleaned'])
        if not chunks:
            raise ValueError('Empty chunk list for PR_ID '+str(r['pr_id']))
        enc=np.asarray(model.encode(chunks,convert_to_tensor=False,normalize_embeddings=False,
                         batch_size=batch_size,show_progress_bar=False),dtype=np.float32)
        if enc.ndim != 2 or enc.shape[1] != DIM or not np.isfinite(enc).all():
            raise ValueError(f'Invalid BGE shape/values for PR_ID {r["pr_id"]}: {enc.shape}')
        # Original Dataiku recipe averages chunks, with NO pre-UMAP L2 normalization.
        rows.append(enc.mean(axis=0))
    embeddings=np.stack(rows,axis=0)
    embedding_frame = pd.DataFrame(embeddings, columns=[f'embedding_{i}' for i in range(DIM)], index=table.index)
    return pd.concat([table, embedding_frame], axis=1)
