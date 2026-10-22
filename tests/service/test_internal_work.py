"""Internal documents, as-of work states, work-draft inputs and the draft audit; a fake model, no provider calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest

from rag_service.workspace import internal_documents, memo_workflow, work_assist
from rag_service.workspace.work_drafts import WorkDraftService
from rag_service.workspace.workspaces import WorkspaceStore

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = json.loads((ROOT/'docs/demo/internal_pipeline_v4/02_INTERNAL_DOCUMENTS.json').read_text(encoding='utf-8'))
POINTS = {p['id']: p for p in BUNDLE['checkpoints']}


def draft(*columns):
    """A normalized draft from (column, text, basis, unit ids) rows; missing columns get one neutral row."""
    rows = {key: [] for key, _ in work_assist.COLUMNS}
    for key, text, basis, ids in columns:
        rows[key].append({'text': text, 'basis': basis, 'basis_label': work_assist.BASIS[basis], 'source_unit_ids': ids})
    return {'position': '검토 초안', 'columns': [{'key': key, 'label': label, 'items': rows[key] or [
        {'text': '추가 확인이 필요하다.', 'basis': 'gap', 'basis_label': '자료 공백', 'source_unit_ids': ['U01']}]} for key, label in work_assist.COLUMNS]}


class FakeChat:
    def __init__(self, answer=None, gate=None, fail=False):
        self.answer, self.gate, self.fail, self.calls = answer, gate, fail, []

    def __call__(self, messages, json_schema=None):
        self.calls.append((messages, json_schema))
        if self.gate:
            self.gate.wait(5)
        if self.fail:
            raise RuntimeError('provider unavailable')
        ids = json_schema['schema']['properties']['sections']['properties']['judgment']['items']['properties']['source_unit_ids']['items']['enum']
        item = {'text': 'IV 1차 결과 3 lot은 v1.0 범위 하한 미만이다.', 'basis': 'observation', 'source_unit_ids': [ids[0]]}
        answer = self.answer or {'position': '판단 범위', 'sections': {key: [item] for key, _ in work_assist.COLUMNS}}
        return {'text': json.dumps(answer, ensure_ascii=False), 'prompt_tokens': 10, 'completion_tokens': 5, 'model_reported': 'fake'}

    def close(self):
        pass


class Scenario(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = WorkspaceStore(Path(self.tmp.name)/'notebooks')
        self.project_id = self.store.create({'name': 'P17 내부 자료', 'ingredient': 'ustekinumab'})['id']
        self.result = self.store.register_documents(self.project_id, deepcopy(BUNDLE))
        self.project = self.store.get(self.project_id)

    def tearDown(self):
        self.tmp.cleanup()

    def prepared(self, point, **override):
        p = {**POINTS[point], **override}
        return work_assist.prepare(self.project, p['task'], p['question'], p['as_of'], p['audience'])


class RegistrationTests(Scenario):
    def test_documents_are_registered_once_and_versions_are_immutable(self):
        self.assertEqual((self.result['document_count'], len(self.result['added'])), (20, 20))
        again = self.store.register_documents(self.project_id, deepcopy(BUNDLE))
        self.assertEqual((again['added'], len(again['skipped'])), ([], 20))
        changed = deepcopy(BUNDLE)
        changed['documents'][1]['units'][0]['text'] = changed['documents'][1]['units'][0]['text'].replace('98.0%', '108.0%')
        with self.assertRaisesRegex(ValueError, '다른 내용으로 이미 등록'):
            self.store.register_documents(self.project_id, changed)
        clash = deepcopy(BUNDLE)
        clash['documents'] = [{**deepcopy(BUNDLE['documents'][0]), 'version': '2.0'}]
        with self.assertRaisesRegex(ValueError, '이미 쓰인 본문 단위 ID'):
            self.store.register_documents(self.project_id, clash)

    def test_states_come_only_from_the_kind_of_record_that_can_set_them(self):
        for role, event in [('experiment_result', {'scope': 'W-PFS-PRIMARY', 'axis': 'qa_approval', 'value': 'approved'}),
                            ('partner_opinion', {'scope': 'W-IV-RERUN', 'axis': 'execution', 'value': 'not_performed'}),
                            ('internal_opinion', {'scope': 'W-RANGE-REV', 'axis': 'approval', 'value': 'approved'}),
                            ('work_request', {'scope': 'W-IV-CPB', 'axis': 'scope_agreement', 'value': 'agreed'}),
                            ('work_record', {'scope': 'W-IV-CPB', 'axis': 'schedule', 'value': 'written_due'})]:
            bundle = deepcopy(BUNDLE)
            unit = bundle['documents'][1]['units'][0]
            unit.update(statement_role=role, scope_ids=[event['scope']], events=[event])
            with self.subTest(role=role, axis=event['axis']), self.assertRaises(ValueError):
                internal_documents.normalize_bundle(bundle)

    def test_blocked_case_identity_cannot_be_registered(self):
        bundle = deepcopy(BUNDLE)
        bundle['documents'][1]['units'][0]['text'] += ' (SB17 참고)'
        with self.assertRaisesRegex(ValueError, '차단한 표현'):
            internal_documents.normalize_bundle(bundle)

    def test_project_record_for_memo_screen_does_not_carry_documents(self):
        from rag_service.workspace.notebook_api import public
        record = public(self.project)
        self.assertNotIn('documents', record)
        self.assertEqual(record['document_count'], 20)


class AsOfTests(Scenario):
    def board(self, as_of, audience='internal'):
        return {s['scope_id']: s['axes'] for s in internal_documents.board(self.project, as_of, audience)}

    def test_only_documents_available_on_the_date_are_read(self):
        units = internal_documents.units_as_of(self.project, '2026-03-06')
        self.assertEqual([u['unit_id'] for u in units], ['U01', 'U02', 'U03', 'U04', 'U05', 'U06'])
        self.assertEqual(internal_documents.hidden_after(self.project, '2026-03-06'), 17)

    def test_partner_audience_reads_only_material_exchanged_with_the_partner(self):
        units = internal_documents.units_as_of(self.project, '2026-03-27', 'partner')
        self.assertEqual({u['sharing'] for u in units}, {'partner'})
        self.assertNotIn('U10', [u['unit_id'] for u in units])  # the internal QA record

    def test_draft_revision_never_becomes_the_applicable_criterion(self):
        standing = internal_documents.versions_as_of(self.project, '2026-04-21')
        self.assertEqual(standing[('PROT-P17-FA-001', '1.0')], 'applicable')
        self.assertEqual(standing[('PROT-P17-FA-001', '1.1-draft')], 'unapproved_revision')
        self.assertNotIn(('PROT-P17-FA-001', '1.1-draft'), internal_documents.versions_as_of(self.project, '2026-03-27'))

    def test_each_work_scope_keeps_its_own_current_state(self):
        t1, t2 = self.board('2026-03-06'), self.board('2026-03-27')
        self.assertEqual(t1['W-IV-RERUN']['scope_agreement']['value'], 'awaiting_response')
        self.assertEqual((t2['W-IV-RERUN']['scope_agreement']['value'], t2['W-IV-RERUN']['execution']['value']), ('agreed', 'result_reported'))
        self.assertEqual((t2['W-PFS-EXPL']['scope_agreement']['value'], t2['W-PFS-EXPL']['report']['value']), ('informal', 'draft'))
        self.assertEqual(t2['W-IV-PRIMARY']['qa_approval']['source_unit_ids'], ['U10'])
        self.assertNotIn('W-IV-CPB', t2)

    def test_written_schedule_replaces_a_verbal_estimate_and_keeps_it_as_history(self):
        schedule = self.board('2026-04-21')['W-IV-CPB']['schedule']
        self.assertEqual((schedule['value'], schedule['date']), ('written_due', '2026-05-08'))
        self.assertEqual((schedule['previous']['value'], schedule['previous']['date']), ('verbal_estimate', '2026-04-24'))
        self.assertEqual(self.board('2026-04-02')['W-IV-CPB']['schedule']['date'], '2026-04-24')

    def test_a_record_confirming_the_same_state_is_evidence_not_a_change(self):
        # The 4/1 minutes restate that the PFS exploration is informal; it became informal on 3/9.
        agreement = self.board('2026-04-21')['W-PFS-EXPL']['scope_agreement']
        self.assertEqual((agreement['value'], agreement['since'][0], agreement['at'][0]), ('informal', '2026-03-09', '2026-04-01'))
        self.assertEqual(agreement['source_unit_ids'], ['U08', 'U16'])
        self.assertEqual(agreement['previous']['value'], 'awaiting_response')

    def test_work_items_are_listed_in_the_order_they_started(self):
        order = [s['scope_id'] for s in internal_documents.board(self.project, '2026-05-15')]
        self.assertEqual(order[:3], ['W-IV-PRIMARY', 'W-IV-RERUN', 'W-PFS-EXPL'])
        self.assertEqual(order[-1], 'W-RA-RESPONSE')

    def test_draft_answer_is_neither_approved_nor_submitted_nor_an_agency_decision(self):
        axes = self.board('2026-05-15')['W-RA-RESPONSE']
        self.assertEqual({k: axes[k]['value'] for k in ('approval', 'submission', 'agency_decision')},
                         {'approval': 'draft', 'submission': 'not_submitted', 'agency_decision': 'no_actual_agency_process'})


class SelectionTests(Scenario):
    def test_small_date_windows_supply_every_available_unit(self):
        prepared = self.prepared('T2')
        self.assertEqual(prepared['retrieval']['method'], 'all_available_units')
        self.assertEqual(len(prepared['selected']), 15)

    def test_review_of_draft_answers_retrieves_the_evidence_the_answers_rely_on(self):
        prepared = self.prepared('T4')
        ids = {u['unit_id'] for u in prepared['selected']}
        self.assertGreater(len(prepared['eligible_unit_ids']), work_assist.UNIT_BUDGET)
        # PFS CPB table (Q-02), IV CPB report and its final-report date, PK note (Q-03), the unapproved range (Q-01)
        self.assertTrue({'U15', 'U26', 'U27', 'U24', 'U21', 'U23', 'U28', 'U29', 'U30', 'U31', 'U01', 'U02'} <= ids)

    def test_model_input_carries_states_standing_and_no_future_material(self):
        prepared = self.prepared('T3')
        content = work_assist.messages(prepared)[1]['content']
        payload = json.loads(content)
        self.assertEqual(payload['as_of'], '2026-04-21')
        self.assertTrue(all(u['date'] <= '2026-04-21' for u in payload['units']))
        self.assertIn('미승인 개정안(적용 안 됨)', content)
        cpb = next(s for s in payload['workflow_states'] if s['scope_id'] == 'W-IV-CPB')
        self.assertIn('2026-05-08', cpb['states']['일정'])
        self.assertIn('이전: 구두 예상 2026-04-24', cpb['states']['일정'])
        self.assertNotIn('U26', content)

    def test_blocked_terms_never_reach_the_model(self):
        with self.assertRaisesRegex(ValueError, '차단한 표현'):
            self.prepared('T1', question='SB17 사례와 비교해 주세요.')
        prepared = self.prepared('T1')
        prepared['selected'][0] = {**prepared['selected'][0], 'text': 'Pyzchiva 공개 보고서 내용'}
        with self.assertRaises(ValueError):
            work_assist.messages(prepared)


class AuditTests(Scenario):
    def codes(self, point, *rows, **override):
        prepared = self.prepared(point, **override)
        aliases = {k: v['aliases'] for k, v in self.project['work_scopes'].items()}
        return [w['code'] for w in work_assist.audit(draft(*rows), prepared, aliases)['warnings']]

    def test_nonagreement_is_flagged_where_a_purchase_order_exists(self):
        self.assertIn('contradicts_agreement_record', self.codes('T2', ('gaps', 'IV 재분석의 범위와 납기는 아직 합의되지 않았다.', 'inference', ['U05'])))
        self.assertEqual(self.codes('T2', ('gaps', 'PFS 탐색의 비용과 보고서 형식은 아직 정하지 않았다.', 'status', ['U08'])), [])

    def test_superseded_schedule_is_flagged_unless_the_change_is_stated(self):
        self.assertIn('superseded_schedule', self.codes('T3', ('evidence', 'IV CPB 결과는 4월 24일에 받는다.', 'status', ['U17'])))
        self.assertEqual(self.codes('T3', ('evidence', 'IV CPB 결과 예정일은 구두 4월 24일에서 서면 5월 8일로 변경됐다.', 'status', ['U19'])), [])

    def test_unapproved_revision_used_as_a_criterion(self):
        self.assertIn('non_applicable_criterion', self.codes('T3', ('judgment', 'IV 결과는 98–130% 범위 안이다.', 'criterion', ['U21'])))
        self.assertEqual(self.codes('T3', ('judgment', '98–130%는 미승인 초안이라 적용할 수 없다.', 'criterion', ['U21', 'U23'])), [])

    def test_draft_report_called_final(self):
        self.assertIn('draft_report_called_final', self.codes('T4', ('evidence', 'IV CPB 최종 보고서에서 차이가 해소됐다.', 'observation', ['U26'])))
        self.assertEqual(self.codes('T4', ('gaps', 'IV CPB 최종 보고서는 5월 22일 예정이다.', 'status', ['U27'])), [])
        # run1 T4: a sentence that asks for the final report is not a claim to hold it
        self.assertEqual(self.codes('T4', ('gaps', 'IV CPB 결과는 초안 상태다. 최종 보고서, ka·kd 원자료 및 반복성 검토가 필요합니다.', 'status', ['U26', 'U27'])), [])

    def test_opinion_presented_as_observation_and_overclaimed_submission(self):
        self.assertIn('opinion_cited_as_observation', self.codes('T2', ('evidence', '결과는 유사하다.', 'observation', ['U13'])))
        self.assertIn('submission_not_recorded', self.codes('T4', ('judgment', 'Q-01 답변을 제출했다.', 'status', ['U29'])))
        self.assertEqual(self.codes('T4', ('evidence', "초안은 '차이가 해소되었다'고 쓴다.", 'draft_text', ['U30'])), [])

    def test_partner_draft_cannot_cite_internal_material(self):
        prepared = self.prepared('T2', audience='partner')
        prepared['selected'].append(next(u for u in internal_documents.units_as_of(self.project, '2026-03-27') if u['unit_id'] == 'U10'))
        codes = [w['code'] for w in work_assist.audit(draft(('evidence', 'QA가 R101을 승인했다.', 'observation', ['U10'])), prepared)['warnings']]
        self.assertIn('internal_source_in_partner_draft', codes)

    def test_outside_case_identity_in_the_answer(self):
        self.assertIn('outside_identity', self.codes('T1', ('judgment', 'SB17과 같은 경향이다.', 'inference', ['U03'])))

    def test_missing_review_result_is_not_a_missing_experiment_result(self):
        # run2 T1: "QA 데이터 검토 결과가 없다" was read as "no result" for a test whose result exists
        self.assertEqual(self.codes('T1', ('gaps', '2차 검토자 확인 및 QA 데이터 검토 결과가 없다.', 'gap', ['U03'])), [])
        self.assertIn('contradicts_result_record', self.codes('T2', ('gaps', 'IV 재분석 결과가 아직 없다.', 'gap', ['U12'])))
        # run6 T1: a missing re-check of calculations, with no work item named, is not 'no result'
        self.assertEqual(self.codes('T1', ('gaps', '정규화 계산과 시료 전처리 기록의 재확인 결과가 없으므로 원인을 판단할 수 없다.', 'gap', ['U04'])), [])


class PresentationTests(Scenario):
    def test_met_due_date_is_shown_with_the_date_the_result_arrived(self):
        prepared = self.prepared('T2')
        rerun = next(s for s in json.loads(work_assist.messages(prepared)[1]['content'])['workflow_states'] if s['scope_id'] == 'W-IV-RERUN')
        self.assertIn('서면 예정 2026-03-23 [U07] · 결과 받음 2026-03-23 [U12]', rerun['states']['일정'])

    def test_inline_unit_ids_are_removed_from_prose_only_when_they_are_known(self):
        known = {'U01', 'U02', 'U10'}
        self.assertEqual(work_assist.strip_ids('범위 밖이다. [U01, U02] [U01, U02]', known), '범위 밖이다.')
        self.assertEqual(work_assist.strip_ids('표 [부록 C] 참조', known), '표 [부록 C] 참조')
        raw = {'position': '판단 [U10]', 'sections': {key: [{'text': '항목 [U01]', 'basis': 'gap', 'source_unit_ids': ['U01']}] for key, _ in work_assist.COLUMNS}}
        normalized = work_assist.normalize(raw, known)
        self.assertEqual((normalized['position'], normalized['columns'][0]['items'][0]['text']), ('판단', '항목'))

    def test_answer_status_is_summarized_from_records_not_the_model(self):
        summary = work_assist.status_summary(self.prepared('T4')['workflow_states'])
        answer = next(line for line in summary if line['scope_id'] == 'W-RA-RESPONSE')
        self.assertEqual([s['label'] for s in answer['states']], ['초안(미승인)', '미제출', '모의 질의(실제 아님)'])
        self.assertNotIn('W-IV-PRIMARY', {line['scope_id'] for line in summary})  # QA alone is not an answer status


class WorkListTests(Scenario):
    def items(self, as_of):
        return {i['scope_id']: i for i in internal_documents.work_items(self.project, as_of)}

    def test_each_work_item_has_one_status_a_summary_and_what_it_waits_for(self):
        t3, t4 = self.items('2026-04-21'), self.items('2026-05-15')
        cpb = t3['W-IV-CPB']
        self.assertEqual((cpb['status']['label'], cpb['waiting']), ('진행 중', '결과 예정 05/08(서면) · 04/24에서 변경'))
        self.assertEqual([e['unit_id'] for e in cpb['events']], ['U16', 'U17', 'U19', 'U20'])
        self.assertEqual(cpb['results'], [])
        self.assertEqual((t4['W-IV-CPB']['status']['label'], t4['W-IV-CPB']['waiting']), ('결과 받음', '최종 보고서 예정 05/22'))
        self.assertEqual([r['unit_id'] for r in t4['W-IV-CPB']['results']], ['U26', 'U27'])
        rerun = t3['W-IV-RERUN']
        self.assertEqual((rerun['status']['label'], rerun['now']), ('완료', '합의 완료(발주·서면) · 보고서 최종본'))
        self.assertEqual([o['unit_id'] for o in rerun['opinions']], ['U13'])

    def test_documents_show_their_own_text_and_approval_chain(self):
        t4 = self.items('2026-05-15')
        self.assertEqual((t4['W-RANGE-REV']['status']['label'], [r['unit_id'] for r in t4['W-RANGE-REV']['results']]), ('QA 미승인', ['U21']))
        answer = t4['W-RA-RESPONSE']
        self.assertEqual((answer['kind'], answer['status']['label'], answer['now']), ('document', '초안', '미제출 · 모의 질의(실제 아님)'))

    def test_a_restating_record_is_history_not_progress(self):
        pfs = self.items('2026-04-21')['W-PFS-EXPL']
        restated = next(e for e in pfs['events'] if e['unit_id'] == 'U16')
        self.assertFalse(restated['changed'])
        self.assertEqual(pfs['last_change'], '2026-03-25')


class ServiceTests(Scenario):
    def service(self, chat):
        return WorkDraftService(self.store, Path(self.tmp.name)/'drafts', chat_factory=lambda folder: chat)

    def wait(self, service, draft_id):
        for _ in range(200):
            record = service.get(self.project_id, draft_id)
            if record['state'] != 'running':
                return record
            time.sleep(.02)
        self.fail('draft did not finish')

    def test_a_draft_runs_once_and_keeps_its_inputs_answer_and_audit(self):
        chat = FakeChat()
        service = self.service(chat)
        try:
            point = POINTS['T1']
            summary = service.submit(self.project_id, {k: point[k] for k in ('task', 'question', 'as_of', 'audience')})
            record = self.wait(service, summary['id'])
            self.assertEqual(record['state'], 'completed')
            self.assertEqual(len(chat.calls), 1)
            self.assertEqual([c['label'] for c in record['draft']['columns']], ['판단', '근거', '공백·확인 필요', '다음 행동'])
            self.assertEqual(record['audit']['all_citations_in_supplied_context'], True)
            self.assertEqual({u['unit_id'] for u in record['selected']}, {'U01', 'U02', 'U03', 'U04', 'U05', 'U06'})
            self.assertEqual(service.list(self.project_id)[0]['id'], summary['id'])
            reopened = WorkDraftService(self.store, Path(self.tmp.name)/'drafts')
            self.assertEqual(reopened.get(self.project_id, summary['id'])['draft'], record['draft'])
            with self.assertRaises(KeyError):
                reopened.get('f' * 32, summary['id'])
        finally:
            service.close()

    def test_one_draft_at_a_time_and_failures_are_recorded(self):
        gate = Event()
        service = self.service(FakeChat(gate=gate, fail=True))
        try:
            point = POINTS['T2']
            payload = {k: point[k] for k in ('task', 'question', 'as_of', 'audience')}
            first = service.submit(self.project_id, payload)
            with self.assertRaises(BlockingIOError):
                service.submit(self.project_id, payload)
            gate.set()
            record = self.wait(service, first['id'])
            self.assertEqual(record['state'], 'failed')
            self.assertIn('provider unavailable', record['error'])
        finally:
            service.close()

    def test_invalid_requests_fail_before_any_model_call(self):
        chat = FakeChat()
        service = self.service(chat)
        try:
            for payload in [{'task': 'unknown', 'question': 'q', 'as_of': '2026-03-06'},
                            {'task': 'result_review', 'question': 'q', 'as_of': '2026-02-30'},
                            {'task': 'result_review', 'question': 'q', 'as_of': '2026-01-01'},
                            {'task': 'result_review', 'question': 'q', 'as_of': '2026-03-06', 'extra': 1}]:
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    service.submit(self.project_id, payload)
            self.assertEqual(chat.calls, [])
        finally:
            service.close()


class LegacyStageTests(unittest.TestCase):
    def test_stage_numbers_and_dates_are_not_mixed(self):
        context = {'unit_id': 'X', 'available_from': 1, 'statement_role': 'experiment_result', 'speaker': 'a',
                   'source': {'document_id': 'd', 'version': '1', 'section': 's', 'source_kind': 'k'}, 'scope_ids': [], 'events': []}
        self.assertEqual(memo_workflow.project([{'context': context}], 2), [])
        with self.assertRaises(ValueError):
            memo_workflow.project([{'context': context}], '2026-03-06')


if __name__ == '__main__':
    unittest.main()
