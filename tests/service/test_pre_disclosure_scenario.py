"""Scenario chronology, memo reuse and outcome isolation; no model calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from eval.pre_disclosure_scenario import audit, messages, normalize_answer, prepare, render_answer, retrieve, schema


class PreDisclosureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data, self.store, self.manifest = prepare(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_future_experiments_and_mock_authority_questions_are_filtered(self):
        for task in self.data['tasks']:
            with self.subTest(task=task['id']):
                result = retrieve(self.data, self.store, self.manifest, task)
                self.assertTrue(all(u['available_from'] <= task['phase'] for u in result['selected']))
                self.assertIn('U01', [u['unit_id'] for u in result['selected']])
                prompt = json.dumps(messages(task, result), ensure_ascii=False)
                self.assertNotIn('SB17', prompt)
                self.assertNotIn('Pyzchiva', prompt)
                if task['phase'] == 1:
                    self.assertNotIn('SIM-EXP-IV-02', prompt)
                    self.assertNotIn('SIM-AGENCY-QUESTIONS-01', prompt)

    def test_response_retrieval_includes_original_rerun_results(self):
        result = retrieve(self.data, self.store, self.manifest, self.data['tasks'][1])
        ids = [u['unit_id'] for u in result['selected']]
        self.assertIn('U08', ids)
        self.assertIn('U06', ids)
        self.assertIn('U07', ids)
        self.assertNotIn('U10', ids)
        self.assertNotIn('U11', ids)

    def test_link_to_future_data_cannot_bypass_filter(self):
        data = deepcopy(self.data)
        for unit in data['units']:
            unit['related_unit_ids'] = ['U10', 'U11']
        result = retrieve(data, self.store, self.manifest, data['tasks'][0])
        self.assertTrue(set(u['unit_id'] for u in result['selected']).isdisjoint({'U10', 'U11'}))

    def test_retrieval_reads_saved_memo_annotations_and_excludes_archives(self):
        project = self.store.get(self.manifest['project_id'])
        reference = next(r for r in self.manifest['references'] if r['unit_id'] == 'U02')
        memo = next(m for m in project['memos'] if m['id'] == reference['memo_id'])
        memo['items'][0]['note'] = 'Persisted analyst clarification'
        self.store.write(project)
        result = retrieve(self.data, self.store, self.manifest, self.data['tasks'][0])
        restored = next(u for u in result['selected'] if u['unit_id'] == 'U02')
        self.assertEqual(restored['note'], 'Persisted analyst clarification')
        memo['archived'] = True
        self.store.write(project)
        result = retrieve(self.data, self.store, self.manifest, self.data['tasks'][0])
        self.assertNotIn('U02', [u['unit_id'] for u in result['selected']])

    def test_actual_outcome_identity_leak_is_rejected(self):
        result = retrieve(self.data, self.store, self.manifest, self.data['tasks'][0])
        result['selected'][0]['note'] = 'CHMP accepted the actual SB17 case'
        with self.assertRaises(ValueError):
            messages(self.data['tasks'][0], result)

    def test_latest_work_events_are_supplied_even_when_initial_request_ranks_high(self):
        task = self.data['tasks'][1]
        result = retrieve(self.data, self.store, self.manifest, task)
        selected = {u['unit_id'] for u in result['selected']}
        states = {s['scope_id']: s for s in result['workflow_states']}
        self.assertIn('U14', selected)
        self.assertIn('U15', selected)
        self.assertEqual(states['W-IV-RERUN']['axes']['execution']['value'], 'result_reported')
        self.assertEqual(states['W-IV-CPB']['axes']['execution']['value'], 'not_performed')
        for scope in states.values():
            for state in scope['axes'].values():
                self.assertTrue(set(state['source_unit_ids']) <= selected)

    def test_observation_and_analyst_response_are_separate_persisted_items(self):
        result = retrieve(self.data, self.store, self.manifest, self.data['tasks'][1])
        by_id = {u['unit_id']: u for u in result['selected']}
        self.assertEqual(by_id['U06']['statement_role'], 'experiment_result')
        self.assertEqual(by_id['U12']['statement_role'], 'analyst_opinion')
        self.assertEqual(by_id['U12']['context']['speaker'], '김분석')
        self.assertNotIn('오류나 무효화 사유를 찾지 못', by_id['U06']['text'])

    def test_output_schema_limits_four_fields_to_three_items_each(self):
        fields = schema(['U01'])['schema']['properties']['sections']
        self.assertEqual(set(fields['required']), {'judgment', 'evidence', 'gaps', 'actions'})
        self.assertTrue(all(value['maxItems'] == 3 for value in fields['properties'].values()))

    def test_approval_status_is_rendered_from_source_events(self):
        task = self.data['tasks'][2]
        selection = retrieve(self.data, self.store, self.manifest, task)
        raw = {'position': 'Review draft', 'sections': {key: [{'text': 'Draft only', 'status': 'draft_response',
               'source_unit_ids': ['U11']}] for key in ('judgment', 'evidence', 'gaps', 'actions')}}
        rendered = render_answer(task, normalize_answer(raw), selection)
        # Labels come from memo_workflow.AXES (plain wording since 2026-10-19).
        self.assertIn('초안(미승인)', rendered)
        self.assertIn('미제출', rendered)
        self.assertIn('모의 질의(실제 아님)', rendered)
        self.assertNotIn('승인·제출·수용 여부: .', rendered)

    def test_missing_agreement_record_cannot_be_reported_as_definite_nonagreement(self):
        selection = retrieve(self.data, self.store, self.manifest, self.data['tasks'][0])
        answer = {'sections': [{'title': '공백', 'items': [{'text': '독립 재분석의 납기는 아직 합의되지 않았습니다.',
            'status': 'observation', 'source_unit_ids': ['U04']}]}]}
        check = audit(answer, selection)
        self.assertEqual(check['review_status'], 'needs_review')
        self.assertEqual(check['workflow_consistency_warnings'][0]['scope_id'], 'W-IV-RERUN')

    def test_explicit_unagreed_additional_scope_is_not_confused_with_completed_work(self):
        selection = retrieve(self.data, self.store, self.manifest, self.data['tasks'][1])
        answer = {'sections': [{'title': '공백', 'items': [{'text': 'IV 재분석 결과는 회신됐고, 추가 IV CPB는 미합의입니다.',
            'status': 'observation', 'source_unit_ids': ['U14', 'U15']}]}]}
        check = audit(answer, selection)
        self.assertEqual(check['workflow_consistency_warnings'], [])

    def test_citation_identity_check_does_not_claim_semantic_accuracy(self):
        selection = {'selected': [{'unit_id': 'U01'}]}
        answer = {'sections': [{'items': [{'text': 'unsupported conclusion', 'source_unit_ids': ['U99']}]}]}
        result = audit(answer, selection)
        self.assertFalse(result['all_citations_in_supplied_context'])
        self.assertFalse(result['meaning_verified_automatically'])
        self.assertIsNone(result['regulatory_prediction_accuracy'])


if __name__ == '__main__':
    unittest.main()
