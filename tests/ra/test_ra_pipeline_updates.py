"""Original failures: missing guidance, cross-trial context, false completion."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from rag.ra import ra_requirements as req
from rag.ra.ra_parent_context import candidates, guideline_candidates


def task(request):
    return {'id': 'T1', 'request': request, 'queries': ['PK dose route'], 'source_roles': ['assessment']}


def chunk(key, study='SB17-1001', page=1, **overrides):
    return {'chunk_id': key, 'document_id': 'd', 'version_id': 'v', 'product_id': 'SB17',
            'jurisdiction': 'EU', 'doc_group': 'assessment', 'pdf_page': page, 'header': '',
            'text': 'Study SB17-1001 in healthy subjects.',
            'structure': {'kind': 'text', 'section_path': [], 'heading_anchors': [
                {'kind': 'study', 'section_id': study, 'title': study, 'study_keys': [
                    'ST_SB17_1001' if study == 'SB17-1001' else 'ST_SB17_3001']}]},
            'representation': {'retrieval_eligible': True, 'description_review_required': False}, **overrides}


def claim(text, source='E1', products=None):
    return {'id': 'C1', 'text': text, 'task_ids': ['T1'], 'products': products or ['SB17'],
            'claim_basis': 'source_statement', 'sources': [{'evidence': source, 'quote': text}]}


class RequestTests(unittest.TestCase):
    def test_quoted_source_revision_is_identity_but_wrong_dates_still_fail(self):
        from rag.ra.ra_quantities import check
        block = chunk('a', title='Guideline on similar biological medicinal products (CHMP/437/04 Rev 1)')
        text = "'" + block['title'] + "'의 시행일은 2015년 4월 30일이다."
        c = claim(text); c['sources'][0]['quote'] = 'Date for coming into effect 30 April 2015*'
        self.assertIsNone(check(c, {'E1': block}))
        c['text'] = text.replace('30일', '29일')
        self.assertEqual(check(c, {'E1': block}), 'claim_number_not_in_final_citations')
        c['text'] = text.replace('Rev 1', 'Rev 2')
        self.assertEqual(check(c, {'E1': block}), 'claim_number_not_in_final_citations')
    def test_stored_adoption_page_cannot_certify_current_validity(self):
        from rag.ra.ra_answer import literal_check
        block = chunk('a', text='Adoption by CHMP 23 October 2014.', role='guideline', product_id=None)
        c = claim('이 문서는 현재 버전이다.'); c.update(kind='source_fact', products=[])
        c['sources'][0]['quote'] = block['text']
        kept, rejected = literal_check({'claims': [c], 'gaps': []}, {'E1': block}, [task('문서 상태')])
        self.assertFalse(kept)
        self.assertEqual(rejected[0]['reason'], 'stored_guideline_does_not_certify_current_validity')

    def test_generic_table_label_cannot_support_location_but_short_value_can(self):
        from rag.ra.ra_answer import literal_check
        block = chunk('a', text='<td>Treatments</td>\n<td>France</td>')
        c = claim('SB17 시험은 프랑스에서 진행되었다.'); c.update(kind='source_fact')
        c['sources'][0]['quote'] = '<td>Treatments</td>'
        kept, rejected = literal_check({'claims': [c], 'gaps': []}, {'E1': block},
            [task('시험 국가')], check_numbers=False)
        self.assertFalse(kept)
        self.assertEqual(rejected[0]['reason'], 'generic_heading_only_not_factual_support')
        c['sources'][0]['quote'] = '<td>France</td>'
        kept, rejected = literal_check({'claims': [c], 'gaps': []}, {'E1': block},
            [task('시험 국가')], check_numbers=False)
        self.assertFalse(rejected)
        self.assertEqual(len(kept), 1)
    def test_guidance_only_task_reads_guidance_and_keeps_queries(self):
        question = 'EMA 문서의 바이오시밀러 대조약 선택과 비EEA 대조약 브리징에 대한 설명을 요약해줘.'
        old = {**task(question), 'source_roles': ['guideline']}; routed = req.route_task(question, old)
        self.assertEqual(routed['queries'], old['queries'])
        self.assertEqual(routed['source_roles'], ['guideline'])
        self.assertEqual(routed['reading_source_roles'], ['guideline'])
        self.assertEqual(routed['required_source_roles'], ['guideline'])

    def test_keywords_never_add_a_guideline_role(self):
        for question in ('EMA SB17 건강인 PK 시험의 대조약과 투여 경로를 알려줘.',
                         '가이던스는 제외하고 EMA 바이오시밀러 대조약 선택 승인 사례를 알려줘.',
                         'EMA 바이오시밀러 지침을 요약해줘.'):
            self.assertNotIn('guideline', req.route_task(question, task(question))['source_roles'])

    def test_mixed_guidance_and_product_request_keeps_both_reading_paths(self):
        question = 'EMA 지침과 SB17 승인 선례를 비교해줘.'
        routed = req.route_task(question, {**task(question), 'source_roles': ['assessment', 'guideline']})
        self.assertEqual(routed['required_source_roles'], ['guideline'])
        self.assertNotIn('reading_source_roles', routed)

    def test_invented_guidance_task_cannot_authorize_a_guideline_route(self):
        routed = req.route_task('EMA SB17 시험 용량을 알려줘.', task('일반 지침을 요약해줘.'))
        self.assertEqual(routed['source_roles'], ['assessment'])

    def test_original_trailing_status_and_scope_requests_survive_planner_omission(self):
        question = 'EMA 바이오시밀러 대조약 선택을 설명해줘. 문서 상태와 적용 범위도 알려줘.'
        routed = req.route_task(question, task('EMA 바이오시밀러 대조약 선택을 설명해줘.'))
        self.assertIn('document_status', routed['requested_conditions']['facets'])
        self.assertIn('applicability', routed['requested_conditions']['facets'])
        self.assertEqual(len(routed['original_request_segments']), 2)

    def test_correct_other_dose_cannot_complete_false_requested_arm(self):
        question = 'SB17 건강인 PK 시험의 135mg 정맥 투여군 결과를 보여줘.'
        t = req.route_task(question, task(question))
        c = claim('SB17는 45mg 피하 투여를 사용했다.')
        result = req.coverage(question, {'tasks': [t]}, {'E1': chunk('a')}, [c])
        self.assertFalse(result['tasks'][0]['minimum_requirements_met'])
        self.assertFalse(any(row['satisfied'] for row in result['tasks'][0]['checks'] if row['kind'] in ('quantity', 'route')))

    def test_planned_facets_decide_what_completion_checks(self):
        def planned(question, facets):
            t = {**task(question), 'answer_facets': [{'facet': f, 'detail': '', 'items': []} for f in facets]}
            return req.route_task(question, t)
        question = 'SB17 건강인 PK 시험의 투여 용량과 경로를 알려줘.'
        t = planned(question, ['dose_route'])
        self.assertEqual(t['requested_conditions']['facets'], ['dose_route'])
        row = req.coverage(question, {'tasks': [t]}, {'E1': chunk('a')}, [claim('SB17는 45 mg을 피하 투여했다.')])['tasks'][0]
        self.assertTrue(row['completion_checkable'])
        self.assertTrue(next(c for c in row['checks'] if c['kind'] == 'facet')['satisfied'])
        # A printed dose in the question stays a literal requirement.
        question = 'SB17 건강인 PK 시험의 135mg 정맥 투여군 결과를 보여줘.'
        row = req.coverage(question, {'tasks': [planned(question, ['dose_route'])]}, {'E1': chunk('a')},
                           [claim('SB17는 45mg 피하 투여를 사용했다.')])['tasks'][0]
        self.assertFalse(row['minimum_requirements_met'])
        # A facet without a code-checkable value never closes completion by itself.
        question = 'SB17 PK 시험 설계를 알려줘.'
        row = req.coverage(question, {'tasks': [planned(question, ['design'])]}, {'E1': chunk('a')},
                           [claim('SB17-1001은 무작위 배정 시험이었다.')])['tasks'][0]
        self.assertFalse(row['completion_checkable'])

    def test_one_products_conditions_cannot_complete_other_product(self):
        question = 'SB17과 ABP654의 용량·경로와 대조약을 비교해줘.'
        t = req.route_task(question, task(question))
        c = claim('SB17는 45mg SC를 투여했고 대조약은 Stelara였다.')
        result = req.coverage(question, {'tasks': [t]}, {'E1': chunk('a')}, [c])
        self.assertFalse(result['tasks'][0]['minimum_requirements_met'])
        self.assertTrue(all(not row['satisfied'] for row in result['tasks'][0]['checks'] if row.get('product_id') == 'ABP654'))

    def test_source_role_requires_a_kept_claim_citing_that_role(self):
        t = req.route_task('EMA 가이던스를 요약해줘.', {**task('EMA 가이던스를 요약해줘.'), 'source_roles': ['guideline']})
        c = claim('제품 선례를 확인했다.'); c['products'] = []
        result = req.coverage(t['request'], {'tasks': [t]}, {'E1': chunk('a')}, [c])
        self.assertFalse(result['tasks'][0]['minimum_requirements_met'])


class StudyTests(unittest.TestCase):
    def test_guideline_context_keeps_version_and_skips_toc(self):
        seed = {'document_id': 'g', 'version_id': 'v', 'jurisdiction': 'EU', 'chunk_id': 'p6'}
        def page(key, text, number, **overrides):
            return {**seed, 'chunk_id': key, 'text': text, 'pdf_page': number, **overrides}
        index = SimpleNamespace(chunks=[page('p1', 'Adoption by CHMP 23 October 2014', 1),
            page('p2', '1.2 Scope .......... 3', 2), page('p3', '1.2. Scope\nBiological medicines', 3),
            page('foreign', '1.2. Scope\nOther version', 3, version_id='old')])
        self.assertEqual([c['chunk_id'] for c in guideline_candidates(index, seed, '문서 상태와 적용 범위')], ['p1', 'p3'])
    def test_parent_expansion_is_source_scoped_and_prefers_dose_conditions(self):
        seed = chunk('seed')
        dose = chunk('dose', text='Study SB17-1001 administered a single dose of 45 mg subcutaneous.', page=2)
        others = [chunk('phase3', study='SB17-3001', text='90 mg SC in patients'),
                  chunk('foreign', version_id='foreign', text='45 mg SC'),
                  chunk('carried', text='Study SB17-3001 administered 90 mg SC.'),
                  chunk('figure', structure={**seed['structure'], 'kind': 'figure_candidate'}, text='45mg SC'),
                  chunk('protected', text='45 mg SC')]
        corpus = SimpleNamespace(chunks=[seed, dose, *others], evidence_adapter=SimpleNamespace())
        picked = candidates(corpus, seed, {c['chunk_id'] for c in [seed, dose, *others[:-1]]},
                            'SB17 건강인 PK 시험의 135mg 정맥 투여군 결과', ['healthy subjects results'])
        self.assertEqual([c['chunk_id'] for c in picked], ['dose'])

    def test_different_headings_for_same_trial_are_not_cross_trial_conflicts(self):
        first, second = chunk('a'), chunk('b')
        second['structure']['heading_anchors'][0].update(section_id='discussion', title='PK study SB17-1001')
        c = claim('SB17의 용량은 45mg이다.'); c['sources'].append({'evidence': 'E2', 'quote': '45mg'})
        self.assertIsNone(req.study_conflict(c, {'E1': first, 'E2': second}))
        second['structure']['heading_anchors'][0]['title'] = 'Study SB17-3001'
        self.assertEqual(req.study_conflict(c, {'E1': first, 'E2': second}), 'single_assertion_citations_cross_study_boundaries')


if __name__ == '__main__': unittest.main()
