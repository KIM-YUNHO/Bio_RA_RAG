"""Notebook persistence, tag semantics, conflicts and source lifetime. No LLM."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from uuid import uuid4

import fitz

from rag_service.__main__ import Jobs
from rag_service.reviews import RevisionConflict
from rag_service.workspace.workspaces import STAGES, WorkspaceStore, source_packet


class NotebookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = WorkspaceStore(self.root / 'notebooks')
        self.project = self.store.create({'name': '자사 개발품 A', 'ingredient': 'ustekinumab'})

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self, **extra):
        return {'request_id': uuid4().hex, 'body': '협력사와 분석자료 확인', 'stage_ids': ['cmc', 'phase1'],
                'products': [], 'tags': ['#CRO A', 'CRO a', '자료 확인'], **extra}

    def test_development_pipeline_needs_no_comparison_products(self):
        self.assertEqual(self.project['name'], '자사 개발품 A')
        self.assertNotIn('products', self.project)
        self.assertNotIn('products', self.store.list()['projects'][0])
        for payload in ({'name': '', 'ingredient': 'ustekinumab'},
                        {'name': '비교 제품 아님', 'ingredient': 'ustekinumab', 'products': ['SB17']}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.store.create(payload)

    def test_review_flow_preserves_old_memo_links_without_writing_on_read(self):
        saved = self.store.add(self.project['id'], self.payload(stage_ids=['cmc', 'bridging', 'phase3']))
        memo = deepcopy(saved['memos'][0])
        saved['stages'] = [{'id': key, 'label': '기존 단계', 'short': key, 'ctd': 'M3 / 2.3'}
                           for key in ('cmc', 'nonclinical', 'phase1', 'bridging', 'phase3', 'approval')]
        self.store.write(saved)
        path = self.store.path(self.project['id'])
        original = path.read_bytes()
        restored = self.store.get(self.project['id'])
        self.assertEqual(restored['stages'][:len(STAGES)], STAGES)
        self.assertEqual(restored['memos'][0], memo)
        self.assertEqual(restored['revision'], saved['revision'])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual([s['id'] for s in STAGES], ['bridging', 'cmc', 'nonclinical', 'phase1', 'phase2', 'phase3', 'approval'])
        self.assertEqual(restored['stages'], STAGES)
        self.assertTrue(all(s.get('track') != 'parallel' for s in restored['stages']))
        self.assertTrue(all('ctd' not in s for s in restored['stages']))
        new = self.store.add(self.project['id'], self.payload(stage_ids=['phase2', 'postmarketing']))
        self.assertEqual(new['memos'][1], memo)
        self.assertIn('postmarketing', {s['id'] for s in new['stages']})
        self.assertEqual(new['stages'], self.store.get(self.project['id'])['stages'])
        self.assertNotIn('clinical_application', {s['id'] for s in new['stages']})

    def test_legacy_pipeline_products_do_not_constrain_or_erase_memos(self):
        saved = self.store.add(self.project['id'], self.payload())
        saved['products'] = ['ABP654']
        saved['memos'][0]['products'] = ['ABP654']
        self.store.write(saved)
        restored = self.store.get(self.project['id'])
        self.assertNotIn('products', restored)
        self.assertEqual(restored['memos'], saved['memos'])
        memo = restored['memos'][0]
        changes = {k: deepcopy(memo[k]) for k in ('body', 'stage_ids', 'products', 'tags', 'archived')}
        changes.update(body='기존 메모 수정', expected_revision=1)
        updated = self.store.revise(self.project['id'], memo['id'], changes)
        self.assertEqual(updated['memos'][0]['products'], ['ABP654'])
        self.assertEqual(updated['memos'][0]['history'][0]['body'], memo['body'])
        self.store.add(self.project['id'], self.payload())

    def test_tags_multi_stage_and_restart_are_persistent(self):
        saved = self.store.add(self.project['id'], self.payload())
        memo = saved['memos'][0]
        self.assertEqual(memo['tags'], ['CRO A', '자료 확인'])
        self.assertEqual(memo['stage_ids'], ['cmc', 'phase1'])
        self.assertNotIn('partner', memo)
        self.assertNotIn('status', memo)
        restored = WorkspaceStore(self.root/'notebooks').get(self.project['id'])
        self.assertEqual(restored['memos'], saved['memos'])

    def test_idempotency_conflict_and_reversible_archive(self):
        payload = self.payload()
        original = self.store.add(self.project['id'], payload)
        repeat = self.store.add(self.project['id'], payload)
        self.assertTrue(repeat['duplicate'])
        self.assertEqual(len(repeat['memos']), 1)
        memo = original['memos'][0]
        changes = {k: deepcopy(memo[k]) for k in ('body', 'stage_ids', 'products', 'tags', 'archived')}
        changes.update(body='회신 확인', tags=['CRO A', '검토 완료'], expected_revision=1, archived=True)
        revised = self.store.revise(self.project['id'], memo['id'], changes)['memos'][0]
        self.assertEqual(revised['history'][0]['body'], memo['body'])
        self.assertEqual(revised['revision'], 2)
        with self.assertRaises(RevisionConflict):
            self.store.revise(self.project['id'], memo['id'], changes)
        changes.update(expected_revision=2, archived=False)
        self.assertFalse(self.store.revise(self.project['id'], memo['id'], changes)['memos'][0]['archived'])

    def test_invalid_metadata_and_injection_remain_plain_text(self):
        self.assertEqual(self.store.add(self.project['id'], self.payload(body='<script>bad()</script>'))['memos'][0]['body'], '<script>bad()</script>')
        for changes in ({'stage_ids': []}, {'stage_ids': ['unknown']}, {'products': ['BAT2206']}, {'tags': ['a\nb']}, {'partner': 'CRO'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.add(self.project['id'], self.payload(**changes))
        with self.assertRaises(ValueError):
            self.store.get('../outside')

    def test_separate_instances_do_not_lose_concurrent_adds(self):
        second = WorkspaceStore(self.root/'notebooks')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(store.add, self.project['id'], self.payload(body=str(i))) for i, store in enumerate((self.store, second))]
            for result in results:
                result.result()
        self.assertEqual(len(self.store.get(self.project['id'])['memos']), 2)

    def test_saved_source_survives_release_change_and_detects_replaced_file(self):
        pdf = self.root/'original.pdf'
        doc = fitz.open(); doc.new_page().insert_text((30, 40), 'Original evidence sentence.'); doc.save(pdf); doc.close()
        pipeline = SimpleNamespace(bundle=SimpleNamespace(docs={'x': {'document_id': 'doc', 'version_id': 'version1', 'raw_path': str(pdf)}}), guidelines=None)
        archive = self.root/'reports'
        jobs = Jobs(pipeline, archive, workspace_store=self.store)
        job_id = uuid4().hex
        block = {'document_id': 'doc', 'version_id': 'version1', 'title': 'Original document', 'pdf_page': 1,
                 'product_id': 'SB17', 'source_refs': [], 'source_spans': [], 'text': 'Original evidence sentence.'}
        quote = {'evidence': 'E1', 'quote': 'Original evidence sentence.', 'offset_basis': 'evidence_text', 'unit_id': 'u1',
                 'source_range': [0, 27], 'representation_range': [10, 37]}
        job = {'id': job_id, 'state': 'completed', 'question': 'q', 'report': {'results': [{'claims': [{'id': 'C1', 'text': '원문 근거', 'sources': [quote]}], 'evidence': {'E1': block}}]}}
        jobs.jobs[job_id] = job; jobs.persist(job)
        request = {'job_id': job_id, 'result_index': 0, 'kind': 'claim', 'claim_id': 'C1'}
        packet = source_packet(jobs, request)
        self.assertEqual(packet['quotes'][0]['unit_id'], 'u1')
        self.assertEqual(packet['original_text'], '원문 근거')
        self.store.write({**self.project, 'products': ['ABP654']})
        first = self.store.add(self.project['id'], self.payload(source=request), packet)
        self.assertEqual(first['memos'][0]['products'], ['SB17'])
        self.assertNotIn('products', first)
        with self.assertRaises(ValueError):
            self.store.add(self.project['id'], self.payload(source=request, products=['ABP654']), packet)
        duplicate = self.store.add(self.project['id'], self.payload(source=request), packet)
        self.assertTrue(duplicate['duplicate'])
        jobs.close()
        changed = SimpleNamespace(bundle=SimpleNamespace(docs={}), guidelines=None)
        reopened = Jobs(changed, archive, workspace_store=WorkspaceStore(self.root/'notebooks'))
        try:
            detail = reopened.pages.detail(reopened.get(job_id), 0, 'E1')
            self.assertEqual(detail['page'], 1)
            self.assertEqual(first['memos'][0]['source']['quotes'][0]['source_range'], [0, 27])
            with self.assertRaises(ValueError):
                source_packet(reopened, {**request, 'quote': 'forged'})
            with pdf.open('ab') as stream:
                stream.write(b'changed')
            with self.assertRaises(ValueError):
                reopened.pages.detail(reopened.get(job_id), 0, 'E1')
        finally:
            reopened.close()


if __name__ == '__main__':
    unittest.main()
