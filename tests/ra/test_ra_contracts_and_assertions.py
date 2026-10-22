from copy import deepcopy
import unittest
from pydantic import ValidationError
from rag.ra import ra_models, ra_assertions, ra_quantities, ra_source_constraints, ra_requirements


def proposal(text, basis='source_statement'):
    return {'id': 'C1', 'text': text, 'kind': 'source_fact', 'products': ['SB17'],
        'claim_basis': basis, 'task_ids': ['T1'], 'sources': [{'evidence': 'E1', 'quote': '45 mg single SC dose'}]}


class BoundaryTests(unittest.TestCase):
    def test_contract_rejects_coercion_extra_fields_and_unknown_task(self):
        c=proposal('SB17은 45 mg SC를 투여했다.');c.pop('id');c.pop('sources');c['citation_span_ids']=['S1']
        ra_models.SpanDraft.model_validate({'claims':[c],'gaps':[]},context={'task_ids':['T1']})
        for field,value in [('text',45),('unknown_field','x'),('task_ids',['T2'])]:
            broken=deepcopy(c);broken[field]=value
            with self.assertRaises(ValidationError):
                ra_models.SpanDraft.model_validate({'claims':[broken],'gaps':[]},context={'task_ids':['T1']})

    def test_completion_cannot_hide_rejected_or_missing_requirements(self):
        for gaps,rejected in [(['Need dose'],[]),([],['C2'])]:
            with self.assertRaises(ValidationError):
                ra_models.ResultState(status='answered',claim_ids=['C1'],rejected_ids=rejected,gaps=gaps)

    def test_absence_tail_is_separated_before_review_without_adding_facts(self):
        c=proposal('SB17 시험은 45mg SC로 실시되었으며, 정맥군 데이터는 포함되어 있지 않다.','absence_of_evidence')
        ready,rejected,changes=ra_assertions.prepare([c])
        self.assertEqual(ready[0]['text'],'SB17 시험은 45mg SC로 실시되었다.')
        self.assertEqual(ready[0]['sources'],c['sources'])
        self.assertEqual(ready[0]['claim_basis'],'source_statement')
        self.assertEqual(rejected[0]['reason'],'partial_retrieval_cannot_establish_document_absence')
        self.assertEqual(len(changes),1)

    def test_conditional_or_unsplittable_absence_is_left_to_clause_review(self):
        for text in ['SB17 데이터가 없으면 비교를 수행하였고, 결과는 보고되지 않았다.',
                     'SB17의 해당 투여군 결과는 보고되지 않았다.']:
            c=proposal(text,'absence_of_evidence')
            ready,rejected,changes=ra_assertions.prepare([c])
            self.assertEqual(ready,[c]);self.assertFalse(changes)

    def test_verified_study_id_is_distinct_from_dose_but_wrong_study_is_not(self):
        block={'product_id':'SB17','structure':{'heading_anchors':[{'kind':'study','title':'PK study SB17-1001',
            'heading_location':{'block_id':'literal-study-heading'}}]}}
        c=proposal('SB17-1001에서 SB17은 45 mg SC를 투여하였다.')
        self.assertIsNone(ra_quantities.check(c,{'E1':block}))
        c['text']='SB17-3001에서 SB17은 45 mg SC를 투여하였다.'
        self.assertEqual(ra_quantities.check(c,{'E1':block}),'claim_number_not_in_final_citations')
        c['text']='SB17-1001에서 SB17은 135 mg SC를 투여하였다.'
        self.assertEqual(ra_quantities.check(c,{'E1':block}),'claim_number_not_in_final_citations')

    def test_bound_quote_needs_route_single_dose_and_approval_predicates(self):
        c=proposal('SB17은 45 mg IV를 투여하였다.')
        self.assertEqual(ra_source_constraints.check(c,{}),'claim_route_not_in_selected_sources')
        c['text']='SB17은 45 mg 단일 SC 주사를 투여하였다.';c['sources'][0]['quote']='45 mg SC injection'
        self.assertEqual(ra_source_constraints.check(c,{}),'single_dose_qualifier_not_in_selected_sources')
        c['text']='SB17의 대조약은 EU 승인 Stelara다.';c['sources'][0]['quote']='EU-Stelara'
        self.assertEqual(ra_source_constraints.check(c,{}),'comparator_approval_not_in_selected_sources')

    def test_qtpp_permission_cannot_be_generalized(self):
        c=proposal('비EEA 대조약을 EEA 대조약과 함께 사용할 수 있다.')
        c['sources'][0]['quote']='Combined use of the non-EEA comparator and EEA reference is acceptable for development of the Quality Target Product Profile.'
        self.assertEqual(ra_source_constraints.check(c,{}),'selected_permission_scope_not_preserved')
        c['text']='QTPP 개발에는 비EEA 대조약을 EEA 대조약과 함께 사용할 수 있다.'
        self.assertIsNone(ra_source_constraints.check(c,{}))

    def test_coverage_recognizes_actual_comparator_and_route_words(self):
        self.assertIn('route',ra_requirements.conditions('투여 용량과 경로')['facets'])
        self.assertIn('comparator',ra_requirements.conditions('compared with ustekinumab (US) and ustekinumab (EU)')['facets'])
        self.assertIn('document_status',ra_requirements.conditions('Date for coming into effect 30 April 2015')['facets'])

    def test_comparison_cannot_borrow_another_products_qualifier(self):
        c={'text':'SB17의 대조약은 EU 승인 Stelara이고 ABP654의 대조약은 EU 승인 ustekinumab이다.',
           'products':['SB17','ABP654'],'sources':[{'evidence':'E1','quote':'EU-approved Stelara'},
                                                {'evidence':'E2','quote':'ustekinumab (EU)'}]}
        e={'E1':{'product_id':'SB17'},'E2':{'product_id':'ABP654'}}
        self.assertEqual(ra_source_constraints.check(c,e),'comparator_approval_not_in_selected_sources')
        c['text']='SB17과 ABP654 모두 90 mg SC를 투여했다.'
        c['sources'][0]['quote']='45 mg SC';c['sources'][1]['quote']='90 mg SC'
        self.assertEqual(ra_source_constraints.check(c,e),'shared_comparison_quantity_not_in_each_product_source')

    def test_trailing_request_object_cannot_be_omitted(self):
        with self.assertRaises(ValidationError):
            ra_models.RequestedDraft.model_validate({'request_results':{'R1':{'claims':[],'gaps':[]}}},
                context={'request_ids':['R1','R2']})

    def test_approval_modifier_is_separated_from_sourced_comparator_identity(self):
        c=proposal('SB17의 대조약은 유럽에서 승인된 ustekinumab(EU)와 미국에서 승인된 ustekinumab(US)이다.')
        c['sources'][0]['quote']='compared with ustekinumab (EU) and ustekinumab (US)'
        ready,rejected,changes=ra_assertions.prepare([c],{'E1':{'product_id':'SB17'}})
        self.assertIn('EU 버전의',ready[0]['text']);self.assertNotIn('승인',ready[0]['text'])
        self.assertEqual(ready[0]['sources'],c['sources'])
        self.assertFalse(rejected[0]['coverage_affecting'])

    def test_coverage_links_verified_fact_across_task_labels_without_borrowing_quotes(self):
        task={'id':'T1','request':'EMA SB17의 용량과 경로','requested_conditions':ra_requirements.conditions('용량과 경로')}
        c=proposal('SB17은 45 mg SC를 투여했다.');c['task_ids']=['T2'];c['sources'][0]['quote']='45 mg SC'
        blocks={'E1':{'product_id':'SB17','jurisdiction':'EU','document_id':'d1','version_id':'v1',
            'structure':{'heading_anchors':[{'kind':'study','title':'PK study SB17-1001',
                'heading_location':{'block_id':'literal-study-heading'}}]}}}
        row=ra_requirements.coverage(task['request'],{'tasks':[task]},blocks,[c])['tasks'][0]
        self.assertTrue(row['minimum_requirements_met'])
        blocks['E1']['jurisdiction']='US'
        self.assertFalse(ra_requirements.coverage(task['request'],{'tasks':[task]},blocks,[c])['tasks'][0]['minimum_requirements_met'])

    def test_source_predicate_and_timing_scopes_cannot_be_strengthened(self):
        c=proposal('품질, 안전성 및 유효성을 입증해야 한다.')
        c['sources'][0]['quote']='The comparator must be representative of the reference product.'
        self.assertEqual(ra_source_constraints.check(c,{}),'claim_quality_safety_efficacy_predicate_not_in_selected_sources')
        c['text']='SB17의 PK 분석은 99일 동안 진행되었다.'
        c['sources'][0]['quote']='Blood samples for PK analysis were collected during 99 days of the PK sampling period.'
        self.assertEqual(ra_source_constraints.check(c,{}),'sampling_period_not_analysis_duration')

    def test_historical_date_survives_unverified_current_validity(self):
        c=proposal('현재 버전이며, EMA 지침은 2014년에 채택되었다.')
        c['products']=[]
        ready,rejected,_=ra_assertions.prepare([c])
        self.assertEqual(ready[0]['text'],'EMA 지침은 2014년에 채택되었다.')
        self.assertEqual(ready[0]['sources'],c['sources'])
        self.assertEqual(rejected[0]['reason'],'stored_guideline_does_not_certify_current_validity')

    def test_bridging_request_requires_own_quoted_bridging_fact(self):
        question='EMA bridging requirements'
        task={'id':'T1','request':question,'requested_conditions':ra_requirements.conditions(question)}
        c=proposal('EMA bridging requirements');c['products']=[]
        c['sources'][0]['quote']='The comparator must be representative.'
        row=ra_requirements.coverage(question,{'tasks':[task]},{'E1':{'jurisdiction':'EU'}},[c])['tasks'][0]
        self.assertFalse(row['minimum_requirements_met'])
        c['sources'][0]['quote']='Additional bridging data are needed.'
        self.assertTrue(ra_requirements.coverage(question,{'tasks':[task]},{'E1':{'jurisdiction':'EU'}},[c])['tasks'][0]['minimum_requirements_met'])

    def test_population_and_region_need_selected_source_words(self):
        c=proposal('SB17-1001은 건강인을 대상으로 했다.')
        c['sources'][0]['quote']='SB17-1001 was conducted in France.'
        self.assertEqual(ra_source_constraints.check(c,{}),'claim_population_not_in_selected_sources')
        c['text']='EEA 승인 대조약과의 유사성을 입증해야 한다.'
        c['sources'][0]['quote']='The comparator should be representative of the EEA reference product.'
        self.assertEqual(ra_source_constraints.check(c,{}),'representativeness_not_biological_similarity')
        c['sources'][0]['quote']='Quality requirements for a biosimilar.'
        self.assertEqual(ra_source_constraints.check(c,{}),'claim_comparator_region_not_in_selected_sources')

    def test_final_date_keeps_own_proof_without_borrowed_scope(self):
        c=proposal("'EMA Guideline'는 2015년 4월 30일부터 효력이 발생하며, 일반 원칙을 설명한다.")
        c['products']=[];c['sources'][0]['quote']='Date for coming into effect 30 April 2015'
        ready,rejected=ra_assertions.preserve_sourced_date(c)
        self.assertEqual(ready['text'],"'EMA Guideline'는 2015년 4월 30일부터 효력이 발생한다.")
        self.assertEqual(ready['sources'],c['sources'])
        self.assertEqual(rejected[0]['text'],'일반 원칙을 설명한다.')

    def test_comparator_and_follow_up_are_not_borrowed_from_context(self):
        c=proposal('SB17의 대조약은 EU 버전의 ustekinumab이다.')
        c['sources'][0]['quote']='A single SC injection in healthy subjects.'
        self.assertEqual(ra_source_constraints.check(c,{}),'comparator_identity_not_in_selected_sources')
        c['text']='SB17 연구는 99일 동안 평가를 포함하여 진행되었다.'
        c['sources'][0]['quote']='After a single-dose injection, subjects were followed for 99 days.'
        self.assertEqual(ra_source_constraints.check(c,{}),'follow_up_period_not_total_study_duration')

    def test_representative_fact_survives_unproved_similarity_prefix(self):
        c=proposal('대조약은 참조약과 유사해야 하며, 비EEA 대조약이 참조약을 대표할 수 있음을 입증해야 한다.')
        c['products']=[]
        c['sources'][0]['quote']='The comparator must be representative of the EEA reference product.'
        ready,rejected=ra_assertions.preserve_representativeness(c)
        self.assertEqual(ready['text'],'비EEA 대조약이 참조약을 대표할 수 있음을 입증해야 한다.')
        self.assertEqual(ready['sources'],c['sources'])
        self.assertEqual(rejected[0]['reason'],'representativeness_not_biological_similarity')


if __name__=='__main__':unittest.main()
