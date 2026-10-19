"""Real-PDF memo replay is a storage/UI fixture, never counted as a retrieval or answer eval."""
from pathlib import Path
import tempfile
import unittest

from eval.seed_memo_scenario import JOB_ID, PDF, seed
from rag_service import memo_items
from rag_service.__main__ import Jobs
from rag_service.workspaces import WorkspaceStore
from types import SimpleNamespace


@unittest.skipUnless(PDF.is_file(), 'Canonical SB17 PDF not installed')
class SourceReplayTests(unittest.TestCase):
    def test_real_pdf_quotes_and_synthetic_comments_stay_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = WorkspaceStore(root/'notebooks')
            result = seed(store, root/'reports')
            self.assertEqual(result['item_count'], 6)
            self.assertEqual(result['model_calls'], 0)
            self.assertEqual(result['gold_cases_evaluated'], 0)
            project = store.get(result['project_id'])
            source_memo = next(m for m in project['memos'] if m['source'])
            observed, criterion, assessment = source_memo['items']
            self.assertEqual(source_memo['source']['report_origin'], 'public_source_replay')
            self.assertEqual(observed['origin'], 'source_replay')
            self.assertIn('98–102%', observed['text'])
            self.assertIn('107–127%', criterion['text'])
            self.assertEqual(assessment['statement_role'], 'regulator_position')
            self.assertEqual(observed['evidence'][0]['pdf_page'], 35)
            self.assertEqual(observed['run_validity'], 'not_disclosed')
            self.assertTrue(all(m['items'][0]['origin'] == 'user_note' for m in project['memos'] if not m['source']))
            jobs = Jobs(SimpleNamespace(), root/'reports', workspace_store=store)
            try:
                job = jobs.get(JOB_ID)
                evidence = job['report']['results'][0]['evidence']['E1']
                for item in source_memo['items']:
                    quote = item['source_quotes'][0]
                    start, end = quote['source_range']
                    self.assertEqual(evidence['text'][start:end], quote['quote'])
                detail = jobs.pages.detail(job, 0, 'E1')
                self.assertEqual(detail['page'], 35)
                self.assertTrue(detail['boxes'])
                self.assertEqual(len(memo_items.brief(project, 'partner_meeting')['groups']), 7)
            finally:
                jobs.close()
            edited = store.get(result['project_id'])
            edited['memos'][0]['body'] = 'User edited this fixture'
            store.write(edited)
            repeat = seed(store, root/'reports')
            self.assertFalse(repeat['created'])
            self.assertEqual(store.get(result['project_id'])['memos'][0]['body'], 'User edited this fixture')


if __name__ == '__main__':
    unittest.main()
