import tempfile
import unittest
from pathlib import Path

import pandas as pd

from map_core.records import prepare_text, raw_to_filtered
from map_core.fivewh import extract_fivewh
from map_core.summaries import build_summaries, apply_review


class CaptureLLM:
    def __init__(self):
        self.calls = []

    def generate(self, task, instruction):
        self.calls.append(instruction)
        if task == '5w1h':
            return 'WHO: operator\nWHAT: pump failure\nWHEN: 28-Aug-2026\nWHERE: EQ-104\nWHY: N/A\nHOW: N/A'
        return 'Proposed batch label'


class QualityFixTests(unittest.TestCase):
    def test_original_numbers_and_late_source_reach_extraction(self):
        original = '<p>At 12:45 on 28-Aug-2026, batch B123 used EQ-104.</p> ' + 'context ' * 2500 + 'END-EVIDENCE-987'
        prepared = prepare_text(pd.DataFrame([{'pr_id': '1', 'detailed_description': original,
            'investigation': 'Check 0.25 mg.', 'conclusion': 'Review pending.'}]), language_detection=False)
        llm = CaptureLLM()
        result = extract_fivewh(prepared, llm)
        for fact in ('12:45', '28-Aug-2026', 'B123', 'EQ-104', '0.25 mg', 'END-EVIDENCE-987'):
            self.assertIn(fact, llm.calls[0])
        self.assertEqual(result.iloc[0]['source_detailed_description'], original)
        self.assertNotIn('987', result.iloc[0]['concat_columns_cleaned'])

    def test_explicit_limit_fails_without_silent_truncation(self):
        llm = CaptureLLM()
        with self.assertRaisesRegex(ValueError, 'no silent truncation'):
            extract_fivewh(pd.DataFrame([{'pr_id': '1', 'detailed_description': 'long source',
                                         'deviation_language': 'en'}]), llm, max_source_chars=3)
        self.assertEqual(llm.calls, [])

    def test_live_title_cannot_replace_historical_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp) / 'catalog.csv'
            pd.DataFrame([{'final_cluster_id': 1, 'cluster_summary': 'Historical title',
                           'detailed_cluster_summary': 'Historical context'}]).to_csv(catalog, index=False)
            frame = pd.DataFrame([{'pr_id': '1', 'final_cluster_id': 1, 'what': 'pump'}])
            summary = build_summaries(frame, mode='live', llm=CaptureLLM(), reference=catalog)
            final, approved, queue = apply_review(frame, summary)
            self.assertEqual(final.iloc[0]['cluster_summary'], 'Historical title')
            self.assertEqual(queue.iloc[0]['proposed_cluster_summary'], 'Proposed batch label')
            self.assertEqual(queue.iloc[0]['proposal_scope'], 'current_batch_only')
            self.assertTrue(approved.empty)

    def test_type_policy_is_explicit_and_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'gtw.csv'
            rows = [dict(PR_ID=str(i), PSG_Division='DPD', RECORD_TYPE='Deviation',
                         STATUS='Closed - Done', ROOT_PARENT_RECORD_TYPE='Deviation',
                         DETAILED_DESCRIPTION='Example', DR_TYPE=t)
                    for i, t in enumerate(['Product Complaint', 'Site Deviation'])]
            pd.DataFrame(rows).to_csv(source, index=False)
            all_rows, _ = raw_to_filtered(source)
            subset, report = raw_to_filtered(source, exclude_dr_types=['product complaint'])
            self.assertEqual(len(all_rows), 2)
            self.assertEqual(subset.iloc[0].dr_type, 'Site Deviation')
            self.assertEqual(report['type_policy_excluded_rows'], 1)
            self.assertEqual(report['duplicate_pr_ids_dropped'], 0)


if __name__ == '__main__':
    unittest.main()
