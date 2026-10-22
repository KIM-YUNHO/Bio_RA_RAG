"""Scoped source events preserve chronology, speaker attribution and approval boundaries."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from eval.build_pre_disclosure_v3 import fixture
from rag_service import memo_workflow
from rag_service.workspaces import WorkspaceStore


class MemoWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.units = fixture()['units']
        self.items = [{'context': u['context']} for u in self.units]

    def board(self, phase):
        return {s['scope_id']: s for s in memo_workflow.project(self.items, phase)}

    def test_later_reply_updates_only_its_work_scope(self):
        first = self.board(1)
        self.assertEqual(first['W-IV-RERUN']['axes']['execution']['value'], 'requested')
        self.assertEqual(first['W-IV-RERUN']['axes']['scope_agreement']['value'], 'not_recorded')
        self.assertEqual(first['W-PFS-CPB']['axes']['scope_agreement']['value'], 'not_recorded')
        self.assertNotIn('W-IV-CPB', first)
        self.assertNotIn('W-RA-RESPONSE', first)
        second = self.board(2)
        for key in ('W-IV-RERUN', 'W-PFS-CPB'):
            self.assertEqual(second[key]['axes']['execution']['value'], 'result_reported')
            self.assertEqual(second[key]['axes']['scope_agreement']['value'], 'not_recorded')
        self.assertEqual(second['W-IV-CPB']['axes']['execution']['value'], 'not_performed')
        self.assertEqual(second['W-IV-CPB']['axes']['scope_agreement']['value'], 'not_agreed')
        self.assertEqual(second['W-IV-RERUN']['history'][0]['phase'], 1)

    def test_result_receipt_and_analyst_comment_do_not_approve_qa(self):
        scope = self.board(2)['W-IV-RERUN']
        self.assertEqual(scope['axes']['qa_approval']['value'], 'not_recorded')
        review = scope['axes']['technical_review']
        self.assertEqual(review['value'], 'analyst_comment_recorded')
        self.assertEqual(review['speaker'], '김분석')
        self.assertEqual(review['source_unit_ids'], ['U12'])

    def test_simulated_draft_is_neither_submitted_nor_an_actual_agency_decision(self):
        axes = self.board(3)['W-RA-RESPONSE']['axes']
        self.assertEqual(axes['approval']['value'], 'draft')
        self.assertEqual(axes['submission']['value'], 'not_submitted')
        self.assertEqual(axes['agency_decision']['value'], 'no_actual_agency_process')

    def test_concurrent_conflicting_events_are_not_silently_ordered(self):
        context = deepcopy(next(u['context'] for u in self.units if u['id'] == 'U14'))
        context['unit_id'] = 'CONFLICT'
        original = context['events'][0]
        context['events'] = [{**original, 'id': 'CONFLICT_EVENT', 'value': 'not_performed'}]
        with self.assertRaises(ValueError):
            memo_workflow.project([*self.items, {'context': context}], 2)

    def test_opinion_or_draft_cannot_be_promoted_to_approval_submission_or_acceptance(self):
        for role, axis, value in [('analyst_opinion', 'qa_approval', 'approved'),
                                   ('sponsor_draft', 'submission', 'submitted'),
                                   ('simulated_agency_request', 'agency_decision', 'accepted')]:
            context = deepcopy(next(u['context'] for u in self.units if u['id'] == 'U04'))
            context['statement_role'] = role
            context['events'] = [{**context['events'][0], 'axis': axis, 'value': value}]
            with self.subTest(role=role), self.assertRaises(ValueError):
                memo_workflow.validate_context(context)

    def test_event_must_belong_to_the_named_work_scope(self):
        context = deepcopy(next(u['context'] for u in self.units if u['id'] == 'U04'))
        context['events'][0]['scope_id'] = 'unrelated'
        with self.assertRaises(ValueError):
            memo_workflow.validate_context(context)

    def test_source_context_is_persisted_but_not_editable_as_an_annotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkspaceStore(Path(tmp))
            project = store.create({'name': 'Source import', 'ingredient': 'ustekinumab'})
            unit = deepcopy(next(u for u in self.units if u['id'] == 'U12'))
            payload = {'request_id': uuid4().hex, 'body': unit['text'], 'stage_ids': ['cmc'], 'products': [], 'tags': []}
            memo = store.add(project['id'], payload, item_context=unit['context'])['memos'][0]
            unit['context']['speaker'] = 'Forged speaker'
            reopened = WorkspaceStore(Path(tmp)).get(project['id'])['memos'][0]
            self.assertEqual(reopened['items'][0]['context']['speaker'], '김분석')
            changes = {k: memo[k] for k in ('body', 'stage_ids', 'products', 'tags', 'archived')}
            for field, value in [('context', {}), ('statement_role', 'experiment_result')]:
                with self.subTest(field=field), self.assertRaises(ValueError):
                    store.revise(project['id'], memo['id'], {**changes, 'expected_revision': 1,
                        'annotations': [{'key': 'manual', field: value}]})
            changed = store.revise(project['id'], memo['id'], {**changes, 'expected_revision': 1, 'body': 'Separate working note'})['memos'][0]
            self.assertEqual(changed['items'][0]['text'], memo['items'][0]['text'])
            self.assertEqual(changed['items'][0]['context'], memo['items'][0]['context'])
            self.assertEqual(changed['body'], 'Separate working note')
            with self.assertRaises(ValueError):
                store.add(project['id'], {**payload, 'request_id': uuid4().hex, 'item_context': unit['context']})


if __name__ == '__main__':
    unittest.main()
