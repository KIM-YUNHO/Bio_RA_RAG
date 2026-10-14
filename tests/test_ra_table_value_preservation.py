"""Counterexamples for table identity, exact values and post-review omissions."""
from copy import deepcopy
from decimal import Decimal
import json
import unittest

from rag import ra_obligations, ra_quantities, ra_review, ra_spans, ra_table_citations, ra_research
from test_ra_citation_completion import block, claim

TABLE = '''Table 8. PK Analysis
<table><tr><th rowspan="2">Parameter</th><th rowspan="2">Statistic</th><th>SB17</th><th>EU Stelara</th><th>US Stelara</th></tr>
<tr><th>N = 67</th><th>N = 67</th><th>N = 67</th></tr>
<tr><td rowspan="3">Cmax (ng/mL)</td><td>Mean</td><td>5095</td><td>5689</td><td>5420</td></tr>
<tr><td>SD</td><td>1498</td><td>1877</td><td>1659</td></tr>
<tr><td>Median</td><td>5045</td><td>5480</td><td>5400</td></tr></table>'''


def selected(evidence, text, quotes):
    spans, _ = ra_spans.catalog(evidence)
    aliases = [next(a for a,s in spans.items() if s['quote']==q) for q in quotes]
    return claim(evidence,text,aliases)


class TableValuePreservation(unittest.TestCase):
    def setUp(self):
        self.evidence={'E1':block(TABLE)}

    def complete(self,text,quotes):
        parent=selected(self.evidence,text,quotes)
        return ra_table_citations.complete([parent],self.evidence)[0][0]

    def test_rowspan_columns_and_exact_context_are_selected_before_review(self):
        parent=self.complete('SB17의 Cmax 산술평균은 5,095 ng/mL이다.',['<td>5095</td>'])
        quotes=[s['quote'] for s in parent['sources']]
        self.assertIn('<th>SB17</th>',quotes)
        self.assertIn('<td>Mean</td>',quotes)
        self.assertIn('<td rowspan="3">Cmax (ng/mL)</td>',quotes)
        self.assertNotIn('<td>5689</td>',quotes)
        self.assertIsNone(ra_table_citations.check(parent,self.evidence))
        self.assertIsNone(ra_quantities.check(parent,self.evidence))
        for s in parent['sources']:
            self.assertEqual(TABLE[s['source_range'][0]:s['source_range'][1]],s['quote'])

    def test_wrong_value_unit_group_and_statistic_remain_rejected(self):
        for text,quote,guard in [
            ('SB17의 Cmax 산술평균은 5096 ng/mL이다.','<td>5095</td>',ra_quantities.check),
            ('SB17의 Cmax 산술평균은 5095 h이다.','<td>5095</td>',ra_quantities.check),
            ('SB17의 Cmax 산술평균은 5689 ng/mL이다.','<td>5689</td>',ra_table_citations.check),
            ('SB17의 Cmax 산술평균은 5045 ng/mL이다.','<td>5045</td>',ra_table_citations.check),
        ]:
            with self.subTest(text=text):
                parent=self.complete(text,[quote])
                self.assertIsNotNone(guard(parent,self.evidence))

    def test_review_cannot_drop_headers_or_borrow_another_proposals_value(self):
        parent=self.complete('SB17의 Cmax 산술평균은 5095 ng/mL이고 표준편차는 1498 ng/mL이다.',
                             ['<td>5095</td>','<td>1498</td>'])
        value=next(s['citation_alias'] for s in parent['sources'] if s['quote']=='<td>5095</td>')
        raw={'claims':{'C1':{'selected_support':'insufficient','context_support':'insufficient',
            'clauses':[{'text':'SB17의 Cmax 산술평균은 5,095 ng/mL이다.','kind':'source_fact',
                'products':['SB17'],'claim_basis':'source_statement','verdict':'supported',
                'final_span_ids':[value],'reason':'Selected mean cell'}]}}}
        task={'id':'T1','request':'Cmax 평균·SD'}
        kept,rejected,_=ra_review.apply(raw,self.evidence,[parent],[task],truth_only=True)
        self.assertFalse(rejected);self.assertEqual(len(kept),1)
        self.assertTrue(kept[0]['reviewed_identity_span_ids_retained'])
        self.assertTrue({s['citation_span_id'] for s in kept[0]['sources']} <=
                        {s['citation_span_id'] for s in parent['sources']})
        payload=json.loads(ra_review.messages('Cmax',{'tasks':[task]},self.evidence,[parent],truth_only=True)[1]['content'])
        self.assertTrue(any('table_binding' in s for s in payload['proposed_claims'][0]['selected_sources']))
        outsider=next(a for a,s in ra_spans.catalog(self.evidence)[0].items() if s['quote']=='<td>1659</td>')
        raw['claims']['C1']['clauses'][0]['final_span_ids']=[outsider]
        kept,rejected,_=ra_review.apply(raw,self.evidence,[parent],[task],truth_only=True)
        self.assertFalse(kept)
        self.assertTrue(rejected)

    def test_completion_does_not_take_headers_from_another_table_or_packet(self):
        other=TABLE.replace('Cmax (ng/mL)','Tmax (h)').replace('5095','5096')
        self.evidence={'E1':block(TABLE),'E2':block(other,version='other')}
        parent=self.complete('SB17의 Cmax 평균은 5095 ng/mL이다.',['<td>5095</td>'])
        self.assertEqual({s['evidence'] for s in parent['sources']},{'E1'})
        broken=TABLE.replace('<td>5689</td>','')
        self.assertFalse(ra_table_citations.layouts(broken))

    def test_format_equivalence_does_not_round_or_treat_markup_as_evidence(self):
        self.assertEqual(ra_quantities.numbers('5,095.00'),ra_quantities.numbers('5095'))
        self.assertNotEqual(ra_quantities.numbers('145.592'),ra_quantities.numbers('145.59'))
        self.assertNotEqual(ra_quantities.numbers('5,09'),ra_quantities.numbers('509'))
        self.assertNotIn('6',ra_quantities.numbers('<td rowspan="6">5095</td>'))
        self.assertEqual(ra_quantities.quantities('60 kg/m²'),{(Decimal(60),'kg/m2')})
        self.assertEqual(ra_quantities.quantities('2025년 10월 12일'),set())
        self.assertEqual(ra_quantities.numbers('Module 5.3.3.1'),{'5.3.3.1'})

    def test_comparison_includes_follow_up_and_eligibility_for_every_product(self):
        question='SB17과 ABP654의 PK 시험을 비교해줘. 용량·경로·비교약·설계·추적 기간과 체중·BMI 선정 기준을 설명해줘.'
        rows=ra_obligations.build(question)
        for product in ['SB17','ABP654']:
            self.assertTrue({'dose_route','comparator','design','follow_up','eligibility'} <=
                {r['facet'] for r in rows if r['product']==product})
        statistics=ra_obligations.build('SB17·EU Stelara·US Stelara의 Cmax 산술평균과 표준편차를 정리해줘.')
        self.assertEqual([r['facet'] for r in statistics],['table_statistics'])

    def test_narrowed_review_records_missing_supported_values(self):
        parent=self.complete('SB17의 Cmax 산술평균은 5095 ng/mL이고 표준편차는 1498 ng/mL이다.',
                             ['<td>5095</td>','<td>1498</td>'])
        rows=ra_obligations.build('SB17의 Cmax 산술평균과 표준편차를 알려줘.')
        parent['requirement_ids']=[rows[0]['id']]
        narrowed=deepcopy(parent);narrowed['text']='SB17의 Cmax 산술평균은 5095 ng/mL이다.'
        result=ra_obligations.final_coverage(rows,[narrowed],[parent],self.evidence)[0]
        self.assertEqual(result['proposed_supported_value_tokens_missing_from_final'],['1498'])
        self.assertFalse(result['observable_values_preserved'])
        header_only=deepcopy(narrowed);header_only['text']='Cmax 표에는 Mean·SD 항목이 있다.'
        result=ra_obligations.final_coverage(rows,[header_only],[parent],self.evidence)[0]
        self.assertFalse(result['has_verified_claim'])

    def test_statistic_distinction_does_not_require_unrequested_extra_values(self):
        rows=ra_obligations.build('SB17의 Cmax 평균과 표준편차를 알려줘. 중앙값·기하평균과 구분해줘.')
        distinction=next(r for r in rows if r['facet']=='statistic_identity')
        self.assertTrue(ra_obligations.matches(distinction,{'products':['SB17'],
            'text':'산술평균·표준편차는 중앙값 및 기하평균과 서로 다른 통계량이다.'}))
        self.assertEqual(ra_obligations.build('Cmax 평균과 표준편차를 알려줘.'),[])

    def test_uncited_table_cannot_change_a_supported_prose_dose(self):
        self.evidence['E2']=block('Healthy subjects received 45 mg SC.')
        spans,_=ra_spans.catalog(self.evidence)
        a=next(a for a,s in spans.items() if s['evidence']=='E2')
        parent=claim(self.evidence,'SB17은 45 mg SC를 투여했다.',[a])
        self.assertIsNone(ra_table_citations.check(parent,self.evidence))

    def test_exact_missing_sd_cell_is_added_before_review_and_order_stays_bound(self):
        text=('Cmax 산술평균은 SB17 5095 ng/mL, EU Stelara 5689 ng/mL, US Stelara 5420 ng/mL이며, '
              '표준편차는 각각 1498 ng/mL, 1877 ng/mL, 1659 ng/mL이다.')
        parent=selected(self.evidence,text,['<td>5095</td>','<td>5689</td>','<td>5420</td>',
                                          '<td>1498</td>','<td>1877</td>','<td>SD</td>'])
        ready,changes=ra_table_citations.complete([parent],self.evidence)
        self.assertIn('<td>1659</td>',[s['quote'] for s in ready[0]['sources']])
        self.assertEqual(len(changes[0]['added_value_span_ids']),1)
        self.assertIsNone(ra_table_citations.check(ready[0],self.evidence))
        wrong=deepcopy(ready[0]);wrong['text']=text.replace('1498 ng/mL, 1877','1877 ng/mL, 1498')
        self.assertIsNotNone(ra_table_citations.check(wrong,self.evidence))
        no_seed=selected(self.evidence,'US Stelara Cmax 표준편차는 1659 ng/mL이다.',['<th>US Stelara</th>'])
        self.assertFalse(ra_table_citations.complete([no_seed],self.evidence)[1])

    def test_shared_header_unit_supports_bare_group_values_without_changing_statistic(self):
        text='Cmax(ng/mL) 중앙값은 SB17 5045, EU Stelara 5480, US Stelara 5400이다.'
        parent=self.complete(text,['<td>5045</td>','<td>5480</td>','<td>5400</td>'])
        row=ra_obligations.build('SB17의 Cmax 중앙값을 알려줘.')[0]
        self.assertTrue(ra_obligations.matches(row,parent))
        self.assertIsNone(ra_table_citations.check(parent,self.evidence))
        parent['text']=text.replace('중앙값','산술평균')
        self.assertIsNotNone(ra_table_citations.check(parent,self.evidence))

    def test_compact_review_preserves_exact_quotes_and_per_proposal_boundaries(self):
        first=self.complete('SB17 Cmax 평균은 5095 ng/mL이다.',['<td>5095</td>'])
        second=self.complete('US Stelara Cmax 표준편차는 1659 ng/mL이다.',['<td>1659</td>'])
        second['id']='C2'
        task={'id':'T1','request':'통계값'}
        original=ra_review.messages('통계값',{'tasks':[task]},self.evidence,[first,second],truth_only=True)
        compact=ra_review.compact_messages(original)
        before=json.loads(original[-1]['content']);after=json.loads(compact[-1]['content'])
        self.assertEqual(before['evidence_boundary'],after['evidence_boundary'])
        for p,q in zip(before['proposed_claims'],after['proposed_claims']):
            self.assertEqual(p['selected_span_ids'],q['selected_span_ids'])
            for a,b in zip(p['selected_sources'],q['selected_sources']):
                self.assertEqual(a['quote'],after['selected_source_catalog'][b['source_ref']]['quote'])
                self.assertEqual(a.get('table_binding'),b.get('table_binding'))
        contract=ra_review.schema(self.evidence,[first,second],[task],truth_only=True)
        outsider=next(s['citation_alias'] for s in second['sources'] if s['quote']=='<td>1659</td>')
        self.assertNotIn(outsider,contract['schema']['$defs']['span_C1']['enum'])
        self.assertNotIn('reading_context',after)

    def test_missing_facet_requires_actual_table_or_temporal_regimen(self):
        row=ra_obligations.build('SB17의 Cmax 중앙값을 알려줘.')[0]
        self.assertTrue(ra_research.facet_candidate(row,self.evidence))
        self.assertFalse(ra_research.facet_candidate(row,{'E1':block('Cmax was measured.')}))
        rows=ra_obligations.build('Steqeyma 용량을 확인해줘. 이후 피하 유지 용량과 투여 일정도 구분해줘.')
        schedule=next(r for r in rows if r['facet']=='dosing_schedule')
        self.assertFalse(ra_research.facet_candidate(schedule,{'E1':block('Refer to the maintenance dosing schedule.')}))
        regimen=block('The first subcutaneous maintenance dose is administered eight weeks after induction, then every eight weeks.')
        regimen['product_id']='CTP43'
        self.assertTrue(ra_research.facet_candidate(schedule,{'E1':regimen}))
        mixed=deepcopy(regimen)
        mixed['text']='Psoriatic Arthritis: every 12 weeks. Crohn disease: see maintenance dosing schedule.'
        schedule['context_pattern']=r'Crohn'
        self.assertFalse(ra_research.facet_candidate(schedule,{'E1':mixed}))

    def test_compound_labels_keep_mean_sd_and_regional_identity(self):
        table='''Table 4. PK summary
        <table><tr><th>Group</th><th>Tmax Median (Range) (h)</th><th>t1/2 Mean (SD) (h)</th></tr>
        <tr><td>Ustekinumab (US)</td><td>190.567 (48.00, 647.38)</td><td>556.998 (168.9308)</td></tr>
        <tr><td>Ustekinumab (EU)</td><td>188.550 (48.00, 649.12)</td><td>579.246 (148.8189)</td></tr></table>'''
        evidence={'E1':block(table)}
        parent=selected(evidence,'US Ustekinumab t1/2 평균(SD)은 556.998 h (168.9308 h)이다.',
                        ['<td>556.998 (168.9308)</td>'])
        parent=ra_table_citations.complete([parent],evidence)[0][0]
        self.assertIsNone(ra_table_citations.check(parent,evidence))
        wrong=deepcopy(parent);wrong['text']='US Ustekinumab t1/2 평균(SD)은 168.9308 h (556.998 h)이다.'
        self.assertIsNotNone(ra_table_citations.check(wrong,evidence))
        wrong['text']=parent['text'].replace('US Ustekinumab','EU Ustekinumab')
        self.assertIsNotNone(ra_table_citations.check(wrong,evidence))

    def test_product_identity_does_not_hide_clinical_numbers_and_study_heading_is_cited(self):
        from rag.ra_citation_completion import complete
        from test_ra_today_invariants import block as study_block
        text='PK similarity study in healthy adult subjects (20190230)\n\nEach subject received 90 mg SC.'
        evidence={'E1':study_block(text,product='ABP654',trial='20190230')}
        # Use the literal heading text which this source actually supplied.
        evidence['E1']['structure']['heading_anchors'][-1]['title']=text.split('\n')[0]
        parent=selected(evidence,'ABP 654 20190230에서는 90 mg SC를 투여했다.',['Each subject received 90 mg SC.'])
        parent['products']=['ABP654']
        ready,changes=complete([parent],evidence)
        self.assertTrue(any(c['reason']=='literal_study_heading_not_selected' for c in changes))
        self.assertIsNone(ra_quantities.check(ready[0],evidence))
        wrong=deepcopy(ready[0]);wrong['text']='ABP 654 20190230에서는 654 mg SC를 투여했다.'
        self.assertIsNotNone(ra_quantities.check(wrong,evidence))
        other=deepcopy(parent);other['text']='ABP 654 20201234에서는 90 mg SC를 투여했다.'
        self.assertIsNotNone(ra_quantities.check(complete([other],evidence)[0][0],evidence))

    def test_explicit_day_sampling_is_relevant_to_follow_up_requirement(self):
        row={'facet':'follow_up','product':'ABP654'}
        self.assertTrue(ra_obligations.matches(row,{'products':['ABP654'],
            'text':'Study 20190230에서는 투여 후 Day 112까지 혈청 검체를 채취해 평가했다.'}))
        self.assertFalse(ra_obligations.matches(row,{'products':['ABP654'],
            'text':'문서 112에서 Study 20190230을 소개했다.'}))

    def test_interpretation_restriction_is_not_a_separate_unsourced_fact_slot(self):
        instruction='병용 투여로 해석하지 말고 적용 조건을 써줘.'
        self.assertTrue(ra_obligations.answer_constraint_only(instruction))
        rows=ra_obligations.build('EMA 바이오시밀러 지침의 대조약 선택과 분석·임상 브리징 조건을 설명해줘. '+instruction)
        self.assertFalse(any(r['request_id']=='R2' for r in rows))
        self.assertFalse(ra_obligations.answer_constraint_only('실제 분포로 해석하지 말고 SB17 체중 선정 기준을 설명해줘.'))
        self.assertFalse(ra_obligations.answer_constraint_only('결론으로 단정하지 말고 결과값을 써줘.'))

    def test_split_analytical_requirement_retains_its_reviewed_conditional_scope(self):
        text=('If certain clinical and in vivo non-clinical studies use the non-EEA comparator, an acceptable bridge is needed. '
              'The bridging data will always include analytical studies comparing all three products.')
        evidence={'E1':block(text,product=None,trial='')}
        parent=claim(evidence,'비EEA 비교약으로 임상 또는 생체 내 비임상 연구를 수행한 경우, 연결을 확립해야 하며 세 제품의 분석 브리징 자료가 항상 필요하다.')
        parent['products']=[]
        alias=parent['sources'][0]['citation_alias']
        def clause(t):return {'text':t,'kind':'source_fact','products':[],
            'claim_basis':'source_statement','verdict':'supported','final_span_ids':[alias],'reason':'selected proof'}
        first='비EEA 비교약으로 임상 또는 생체 내 비임상 연구를 수행한 경우, 연결을 확립해야 한다.'
        second='분석 브리징 자료는 세 제품의 분석자료를 항상 포함해야 한다.'
        raw={'claims':{'C1':{'selected_support':'insufficient','context_support':'insufficient',
                            'clauses':[clause(first),clause(second)]}}}
        kept,rejected,_=ra_review.apply(raw,evidence,[parent],[{'id':'T1','request':'브리징 조건'}],truth_only=True)
        self.assertFalse(rejected);self.assertEqual(len(kept),2)
        self.assertTrue(kept[1]['text'].startswith(first.split(',')[0]+','))
        self.assertEqual(kept[1]['resolved_reviewed_condition']['reason'],'same_proposal_reviewed_bridge_scope')
        raw['claims']['C1']['clauses']=[clause(second)]
        kept,rejected,_=ra_review.apply(raw,evidence,[parent],[{'id':'T1','request':'브리징 조건'}],truth_only=True)
        self.assertFalse(kept);self.assertEqual(rejected[0]['reason'],'selected_clinical_bridge_condition_not_preserved')


if __name__=='__main__':
    unittest.main()
