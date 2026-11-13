import tempfile
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

import numpy as np
import pandas as pd

from map_core.fivewh import parse_5w1h, from_prepared_5w1h, extract_fivewh
from map_core.records import raw_to_filtered, prepare_text
from map_core.summaries import build_summaries, apply_review
from map_core.embeddings import generate_embeddings
from map_pipeline import read_csv, save


class MapLocalFlowTest(unittest.TestCase):
    def test_parse_5w1h(self):
        parsed = parse_5w1h('WHO: operator\nWHAT: pump stopped\nWHEN: N/A\nWHERE: line 1\nWHY: seal\nHOW: vibration')
        self.assertEqual(parsed['WHO'], 'operator')
        self.assertEqual(parsed['WHEN'], 'not_available')
        self.assertEqual(parsed['WHY'], 'seal')

    def test_5w_concat_dataiku_order(self):
        row = pd.DataFrame([{'PR_ID': '7', 'WHO': 'A', 'WHAT': 'B', 'WHEN': 'C',
                             'WHERE': 'D', 'WHY': 'E', 'HOW': 'F'}])
        out = from_prepared_5w1h(row)
        self.assertEqual(out.loc[0, '5w_concat'], 'BCDEAF')

    def test_filter_and_prepare(self):
        with tempfile.TemporaryDirectory() as tmp:
            gtw = Path(tmp) / 'gtw.csv'
            pd.DataFrame([
                {'PR_ID': '10', 'PSG_Division': 'DPD', 'RECORD_TYPE': 'Deviation',
                 'STATUS': 'Closed - Done', 'IS_CLOSED': 1, 'ROOT_PARENT_RECORD_TYPE': 'Deviation',
                 'DETAILED_DESCRIPTION': 'A pump was stopped.', 'INVESTIGATION': 'Seal damage', 'CONCLUSION': 'Replaced'},
                {'PR_ID': '11', 'PSG_Division': 'DPD', 'RECORD_TYPE': 'Deviation',
                 'STATUS': 'Open', 'IS_CLOSED': 0, 'ROOT_PARENT_RECORD_TYPE': 'Deviation',
                 'DETAILED_DESCRIPTION': 'Other', 'INVESTIGATION': '', 'CONCLUSION': ''},
                {'PR_ID': '12', 'PSG_Division': 'DSD', 'RECORD_TYPE': 'Deviation',
                 'STATUS': 'Closed - Done', 'IS_CLOSED': 1, 'ROOT_PARENT_RECORD_TYPE': 'Deviation',
                 'DETAILED_DESCRIPTION': 'Other', 'INVESTIGATION': '', 'CONCLUSION': ''},
            ]).to_csv(gtw, index=False)
            frame, stats = raw_to_filtered(gtw)
            self.assertEqual(stats['selected_rows'], 1)
            self.assertEqual(frame.iloc[0]['pr_id'], '10')
            # Test structural prep independently of optional external contractions package.
            with patch.dict(sys.modules, {'contractions': SimpleNamespace(fix=lambda v: v)}):
                prepared = prepare_text(frame, language_detection=False)
            self.assertIn('pump', prepared.iloc[0]['concat_columns_cleaned'])

    def test_summary_never_autoapproves(self):
        frame = pd.DataFrame([{'pr_id': '1', 'final_cluster_id': 3, 'what': 'pump issue',
                               'who': 'operator', 'when': 'N/A', 'where': 'line',
                               'why': 'seal', 'how': 'stopped'}])
        summaries = build_summaries(frame, mode='pending')
        final, approved, queue = apply_review(frame, summaries)
        self.assertTrue(approved.empty)
        self.assertEqual(final.iloc[0]['review_status'], 'pending')
        self.assertEqual(queue.iloc[0]['summary_source'], 'pending_llm_and_human_review')

    def test_output_excludes_embedding_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'final.csv'
            frame = pd.DataFrame([{'pr_id': 'a', 'embedding_0': 0.1, 'cluster_id': 1}])
            save(frame, dest, include_vectors=False)
            loaded = pd.read_csv(dest)
            self.assertEqual(set(loaded.columns), {'pr_id', 'cluster_id'})

    def test_extract_5w1h_with_fake_llm(self):
        class FakeLLM:
            def generate(self, task, instruction):
                self_test = 'WHO: operator\nWHAT: motor stopped\nWHEN: N/A\nWHERE: line 1\nWHY: alarm\nHOW: shutdown'
                return self_test
        source = pd.DataFrame([{'pr_id': 'A1', 'deviation_language': 'en',
                                'detailed_description': 'Pump stopped', 'investigation': 'Alarm',
                                'conclusion': '', 'concat_columns_cleaned': 'pump stopped with alarm'}])
        result = extract_fivewh(source, FakeLLM())
        self.assertEqual(result.iloc[0]['what'], 'motor stopped')
        self.assertEqual(result.iloc[0]['when'], 'not_available')
        self.assertFalse(bool(result.iloc[0]['translated_to_english']))

    def test_embeddings_with_fake_checkpoint(self):
        # Tests control flow and 1024-D schema without downloading BGE.
        class FakeModel:
            def __init__(self, name, trust_remote_code=False):
                pass
            def get_sentence_embedding_dimension(self):
                return 1024
            def encode(self, chunks, **kwargs):
                return np.ones((len(chunks), 1024), dtype=np.float32)
        class FakeSplitter:
            def __init__(self, **kwargs):
                pass
            def split_text(self, text):
                return [text]
        stub_modules = {
            'sentence_transformers': SimpleNamespace(SentenceTransformer=FakeModel),
            'langchain_text_splitters': SimpleNamespace(RecursiveCharacterTextSplitter=FakeSplitter),
            'contractions': SimpleNamespace(fix=lambda v: v),
        }
        sample = pd.DataFrame([{'PR_ID': 'A1', 'WHO': 'operator', 'WHAT': 'pump stopped',
                                'WHEN': 'today', 'WHERE': 'line one', 'WHY': 'alarm', 'HOW': 'stopped'}])
        with patch.dict(sys.modules, stub_modules):
            embedded = generate_embeddings(sample, stopwords='none')
        self.assertEqual(embedded.filter(regex=r'^embedding_\d+$').shape, (1, 1024))
        self.assertAlmostEqual(float(embedded.iloc[0]['embedding_1023']), 1.0)

    def test_live_summary_with_fake_llm_stays_pending(self):
        class FakeLLM:
            def generate(self, task, instruction):
                return 'Pump issues in line one.' if task == 'cluster_long_summary' else 'Pump — Seal Deviations'
        frame = pd.DataFrame([{'pr_id': '1', 'final_cluster_id': 1,
                               'who': 'operator', 'what': 'pump stopped', 'when': 'N/A',
                               'where': 'line 1', 'why': 'seal', 'how': 'stopped'}])
        summaries = build_summaries(frame, mode='live', llm=FakeLLM())
        merged, approved, queue = apply_review(frame, summaries)
        self.assertEqual(summaries.iloc[0]['proposed_cluster_summary'], 'Pump — Seal Deviations')
        self.assertEqual(summaries.iloc[0]['cluster_summary'], '')
        self.assertTrue(summaries.iloc[0]['historical_label_missing'])
        self.assertEqual(merged.iloc[0]['review_status'], 'pending')
        self.assertTrue(approved.empty)

    def test_csv_read_requires_unique_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'in.csv'
            dest.write_text('PR_ID,WHAT\n123,test\n123,test\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                read_csv(dest)


if __name__ == '__main__':
    unittest.main()
