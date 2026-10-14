from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from rag_service.reviews import ReviewStore, RevisionConflict


class ReviewStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=ReviewStore(self.temp.name)
        self.job={'id':'a'*32,'state':'completed','question':'검토 질문','report':{'question':'검토 질문',
            'claims':[{'text':'original'}],'review_memo':{'meeting_questions':[{'issue_id':'T1','question_ko':'초안'}]}}}
        self.payload={'expected_revision':0,'status':'needs_work','note':'원문 확인','meeting_questions':[{'issue_id':'T1','question_ko':'수정 질문'}]}

    def test_append_only_history_survives_restart_and_original_report_is_unchanged(self):
        original=deepcopy(self.job)
        first=self.store.append(self.job,self.payload)
        next_payload={**self.payload,'expected_revision':1,'note':'재확인','status':'checked'}
        second=self.store.append(self.job,next_payload)
        self.assertEqual(second['revisions'][0],first['revisions'][0])
        self.assertEqual(ReviewStore(self.temp.name).get(self.job),second)
        self.assertEqual(self.job,original)

    def test_concurrent_stale_revision_cannot_overwrite_and_job_scope_is_bound(self):
        self.store.append(self.job,self.payload)
        with self.assertRaises(RevisionConflict):self.store.append(self.job,self.payload)
        different={**self.job,'id':'b'*32}
        self.assertFalse(self.store.get(different)['revisions'])
        changed={**self.job,'report':{**self.job['report'],'claims':[]}}
        with self.assertRaises(ValueError):self.store.get(changed)

    def test_write_failure_preserves_prior_history_and_removes_only_own_temp(self):
        first=self.store.append(self.job,self.payload)
        with patch('rag_service.reviews.os.replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.store.append(self.job,{**self.payload,'expected_revision':1,'note':'unsaved'})
        self.assertEqual(self.store.get(self.job),first)
        self.assertFalse(list(Path(self.temp.name).glob('*.tmp')))

    def test_invalid_shape_id_question_and_unknown_issue_are_rejected(self):
        for changed in [{**self.payload,'note':'x'*4001},{**self.payload,'expected_revision':True},
            {**self.payload,'meeting_questions':[{'issue_id':'T2','question_ko':'wrong'}]}]:
            with self.assertRaises(ValueError):self.store.append(self.job,changed)
        with self.assertRaises(ValueError):self.store.get({**self.job,'id':'../outside'})
        with self.assertRaises(ValueError):self.store.get({**self.job,'state':'failed'})
        path=Path(self.temp.name)/(self.job['id']+'.json');path.write_text('[]',encoding='utf-8')
        with self.assertRaises(ValueError):self.store.get(self.job)
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')),[])


if __name__=='__main__':unittest.main()
