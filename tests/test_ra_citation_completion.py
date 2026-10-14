from copy import deepcopy
import json
import unittest
from pydantic import ValidationError

from rag import ra_answer, ra_assertions, ra_citation_completion, ra_models, ra_quantities, ra_requirements, ra_source_constraints, ra_spans


def block(text, trial='SB17-1001', version='v1', product='SB17'):
    return {'text':text, 'document_id':'d1', 'version_id':version, 'product_id':product,
        'jurisdiction':'EU', 'structure':{'heading_anchors':[{'kind':'study','title':trial,
            'heading_location':{'block_id':'real-heading'}}]}, 'representation':{},
        'source_spans':[{'unit_id':text, 'representation_start':0,
                        'representation_end':len(text), 'separator_before':''}]}


def claim(evidence, text, ids=('S1',)):
    spans,_=ra_spans.catalog(evidence)
    row={'id':'C1','text':text,'kind':'source_fact','products':['SB17'],
        'claim_basis':'source_statement','task_ids':['T1'],'citation_span_ids':list(ids)}
    return ra_spans.resolve({'claims':[row],'gaps':[]},evidence,spans)['claims'][0]


class CompletionTests(unittest.TestCase):
    def test_pk_study_identity_needs_own_source_and_can_be_completed_before_review(self):
        evidence={'E1':block('Clinical Phase I study SB17-1001. Healthy subjects received a single dose of 45 mg as an SC injection.'),
                  'E2':block('PK study SB17-1001')}
        a=claim(evidence,'SB17의 건강인 PK 시험에서는 45 mg SC를 단회 투여했다.')
        self.assertEqual(ra_source_constraints.check(a,evidence),'claim_pk_study_not_in_selected_sources')
        ready,changes=ra_citation_completion.complete([a],evidence)
        self.assertIsNone(ra_source_constraints.check(ready[0],evidence))
        self.assertEqual(changes[0]['reason'],'claim_pk_study_not_in_selected_sources')
        self.assertTrue(changes[0]['reviewed_after_completion'])

    def test_pk_study_cannot_be_completed_from_other_trial_or_version(self):
        for kwargs in [{'trial':'SB17-3001'},{'version':'other'},{'product':'ABP654'}]:
            evidence={'E1':block('Healthy subjects received a single dose of 45 mg as an SC injection.'),
                      'E2':block('PK study',**kwargs)}
            a=claim(evidence,'SB17 건강인 PK 시험에서는 45 mg SC를 단회 투여했다.')
            ready,changes=ra_citation_completion.complete([a],evidence)
            self.assertFalse(changes)
            self.assertEqual(ready,[a])

    def test_design_arm_count_adds_same_study_design_not_an_unrelated_number(self):
        evidence={'E1':block('Healthy subjects participated in the trial.'),
                  'E2':block('A randomised double-blind three-arm parallel-group study was conducted.')}
        a=claim(evidence,'SB17은 건강인 대상 무작위 배정 이중 맹검 3군 평행 시험이다.')
        ready,changes=ra_citation_completion.complete([a],evidence)
        self.assertEqual(len(ready[0]['sources']),2)
        self.assertEqual(changes[0]['reason'],'design_cardinality_not_selected')
        self.assertIsNone(ra_quantities.check(ready[0],evidence))
        evidence['E2']=block('Three subjects participated in an assay.')
        _,changes=ra_citation_completion.complete([a],evidence)
        self.assertFalse(changes)

    def test_part_objective_title_is_selected_from_same_body_before_review(self):
        evidence={'E1':block('Primary objective (Part 2):\n\nTo demonstrate pharmacokinetic similarity.')}
        a=claim(evidence,'Part 2의 목적은 약동학적 유사성을 입증하는 것이다.',('S2',))
        ready,changes=ra_citation_completion.complete([a],evidence)
        self.assertEqual([s['citation_alias'] for s in ready[0]['sources']],['S2','S1'])
        self.assertEqual(changes[0]['boundary'],'same_supplied_body_block')
        a['text']='Part 1의 목적은 약동학적 유사성을 입증하는 것이다.'
        self.assertFalse(ra_citation_completion.complete([a],evidence)[1])
        evidence={'E1':block('To demonstrate pharmacokinetic similarity.'),
                  'E2':block('Primary objective (Part 2):')}
        a=claim(evidence,'Part 2의 목적은 약동학적 유사성을 입증하는 것이다.')
        self.assertFalse(ra_citation_completion.complete([a],evidence)[1])

    def test_optional_clinical_bridge_cannot_be_strengthened_to_mandatory(self):
        evidence={'E1':block('The bridge may also include data from clinical PK and/or PD bridging studies for all three products.')}
        a=claim(evidence,'임상 PK/PD 브리징 데이터를 포함해야 한다.')
        self.assertEqual(ra_source_constraints.check(a,evidence),'optional_clinical_bridging_strengthened_to_requirement')
        a['text']='임상 PK/PD 브리징 데이터가 포함될 수 있으며 필요성은 사례별로 결정된다.'
        self.assertIsNone(ra_source_constraints.check(a,evidence))

    def test_objective_part_heading_is_not_a_result_or_comparison(self):
        self.assertTrue(ra_answer.label_only_quote('Primary objective (Part 2):'))
        self.assertFalse(ra_answer.label_only_quote('To demonstrate PK similarity in terms of Cmax.'))

    def test_planned_population_is_not_actual_enrollment(self):
        evidence={'E1':block('The study was planned to be conducted in 327 subjects. The subjects were to be enrolled.')}
        a=claim(evidence,'SB17은 327명의 피험자를 모집하였다.')
        self.assertEqual(ra_source_constraints.check(a,evidence),'planned_population_not_actual_enrollment')
        a['text']='SB17은 327명의 피험자를 모집할 계획이었다.'
        self.assertIsNone(ra_source_constraints.check(a,evidence))
        a['text']='SB17은 327명의 피험자를 모집하였다.'
        a['sources'][0]['quote']+=' In total 327 subjects were enrolled.'
        self.assertIsNone(ra_source_constraints.check(a,evidence))
        a['text']='SB17은 1:1:1 비율로 무작위 배정되었다.'
        a['sources'][0]['quote']='The subjects were to be enrolled and randomised in a 1:1:1 ratio.'
        self.assertEqual(ra_source_constraints.check(a,evidence),'planned_design_not_observed_conduct')

    def test_pk_similarity_cannot_be_rendered_as_pharmacological_equivalence(self):
        evidence={'E1':block('To demonstrate pharmacokinetic (PK) similarity.')}
        a=claim(evidence,'SB17의 약리학적 동등성을 입증하였다.')
        self.assertEqual(ra_source_constraints.check(a,evidence),'pk_similarity_not_pharmacological_equivalence')
        a['text']='SB17의 약동학적 유사성을 입증하였다.'
        self.assertIsNone(ra_source_constraints.check(a,evidence))

    def test_general_pk_study_title_cannot_prove_an_assay_target(self):
        evidence={'E1':block('A PK and immunogenicity study of ustekinumab.')}
        a=claim(evidence,'SB17의 PK/면역원성 analyte는 ustekinumab이다.')
        self.assertEqual(ra_source_constraints.check(a,evidence),'assay_target_not_in_selected_sources')
        a['sources'][0]['quote']='The assay measured serum ustekinumab concentration.'
        self.assertIsNone(ra_source_constraints.check(a,evidence))

    def test_explicit_general_principles_scope_is_preserved_with_its_date(self):
        a={'products':[],'id':'C1','text':"EMA 지침은 2015년 4월 30일부터 효력이 발생하며, 일반 원칙을 설명한다.",
           'sources':[{'quote':'Date for coming into effect 30 April 2015'},
                      {'quote':'This Guideline outlines the general principles to be applied for similar biological medicinal products.'}]}
        ready,rejected=ra_assertions.preserve_sourced_date(a)
        self.assertEqual(ready,a);self.assertFalse(rejected)

    def test_same_supplied_study_repairs_population_before_review(self):
        evidence={'E1':block('Subjects received 45 mg SC.'),
                  'E2':block('Healthy subjects participated in this study.')}
        ready,changes=ra_citation_completion.complete([claim(evidence,'SB17은 건강인에게 45 mg SC로 투여했다.')],evidence)
        self.assertEqual([s['citation_alias'] for s in ready[0]['sources']],['S1','S2'])
        self.assertTrue(changes[0]['reviewed_after_completion'])
        self.assertEqual(ready[0]['sources'][1]['quote'],evidence['E2']['text'])

    def test_other_trial_version_product_and_unverified_heading_cannot_repair(self):
        for kwargs in [{'trial':'SB17-3001'}, {'version':'v2'}, {'product':'ABP654'}, {'trial':'Unknown study'}]:
            with self.subTest(kwargs=kwargs):
                evidence={'E1':block('Subjects received 45 mg SC.'),
                          'E2':block('Healthy subjects participated in this study.',**kwargs)}
                original=claim(evidence,'SB17은 건강인에게 45 mg SC로 투여했다.')
                ready,changes=ra_citation_completion.complete([original],evidence)
                self.assertFalse(changes); self.assertEqual(ready,[original])

    def test_wrong_dose_cannot_be_repaired_from_surrounding_context(self):
        evidence={'E1':block('Subjects received 45 mg SC.'),
                  'E2':block('Healthy subjects received 90 mg SC.')}
        ready,changes=ra_citation_completion.complete([claim(evidence,'SB17은 건강인에게 135 mg SC로 투여했다.')],evidence)
        self.assertFalse(changes); self.assertEqual(len(ready[0]['sources']),1)

    def test_completion_token_cap_is_global(self):
        evidence={'E1':block('Subjects received 45 mg SC.'),
                  'E2':block('Healthy subjects '+('participated '*400))}
        _,changes=ra_citation_completion.complete([claim(evidence,'SB17은 건강인에게 45 mg SC로 투여했다.')],evidence)
        self.assertFalse(changes)

    def test_duplicate_own_citations_do_not_merge_proof_or_decimal_values(self):
        evidence={'E1':block('Subjects received 3.5 mg SC.'),'E2':block('A 3.5 mg SC dose was given.')}
        a=claim(evidence,'SB17은 3.5 mg SC를 투여했다.');b=claim(evidence,a['text'],('S2',));b['id']='C2';b['task_ids']=['T2']
        original=deepcopy(a['sources'])
        kept,omitted=ra_assertions.deduplicate([a,b],evidence)
        self.assertEqual(len(kept),1);self.assertEqual(kept[0]['sources'],original)
        self.assertEqual(kept[0]['task_ids'],['T1','T2']);self.assertEqual(len(omitted),1)
        b['text']='SB17은 35 mg SC를 투여했다.'
        self.assertEqual(len(ra_assertions.deduplicate([a,b],evidence)[0]),2)

    def test_duplicate_different_study_or_version_is_retained(self):
        for kwargs in [{'trial':'SB17-3001'},{'version':'v2'}]:
            evidence={'E1':block('Subjects received 45 mg SC.'),'E2':block('A 45 mg SC dose was given.',**kwargs)}
            a=claim(evidence,'SB17은 45 mg SC를 투여했다.');b=claim(evidence,a['text'],('S2',));b['id']='C2'
            self.assertEqual(len(ra_assertions.deduplicate([a,b],evidence)[0]),2)

    def test_only_completely_recognized_requests_can_close(self):
        self.assertTrue(ra_requirements.fully_checkable_request('EMA에서 SB17 건강인 PK 시험의 실제 투여 용량과 경로를 알려줘.'))
        for text in ['SB17 용량과 경로 및 결과를 알려줘.', 'SB17 용량과 경로 차이를 알려줘.',
                     'EMA bridging requirements', 'SB17 적용 범위와 투여 용량']:
            self.assertFalse(ra_requirements.fully_checkable_request(text))

    def test_individually_supported_dose_and_route_cannot_join_distinct_trials(self):
        evidence={'E1':block('Subjects received 45 mg.'),'E2':block('An SC injection was given.',trial='SB17-3001')}
        a=claim(evidence,'SB17은 45 mg을 투여했다.');b=claim(evidence,'SB17은 SC를 투여했다.',('S2',));b['id']='C2'
        task={'id':'T1','request':'EMA SB17의 용량과 경로'}
        row=ra_requirements.coverage(task['request'],{'tasks':[task]},evidence,[a,b])['tasks'][0]
        self.assertFalse(row['minimum_requirements_met'])

    def test_nonblocking_rejections_can_be_logged_but_invalid_ids_fail(self):
        state=ra_models.ResultState(status='answered',claim_ids=['C1'],rejected_ids=['C2'],
                                  blocking_rejected_ids=[],gaps=[])
        self.assertEqual(state.rejected_ids,['C2'])
        with self.assertRaises(ValidationError):
            ra_models.ResultState(status='answered',claim_ids=['C1'],rejected_ids=['C2'],
                                  blocking_rejected_ids=['C3'],gaps=[])

    def test_runtime_ignores_rejected_noise_only_when_original_request_is_fulfilled(self):
        evidence={'E1':block('Healthy subjects received a single 45 mg SC dose.')}
        class Tokens:
            def one(self,value): return len(value)
            def messages(self,values): return sum(len(x['content']) for x in values)
        class Client:
            tokenizer=Tokens();input_limit=100000
            def __call__(self,messages,json_schema=None,provider=None):
                if json_schema['name']=='ra_request_grounded_answer':
                    a=claim(evidence,'SB17은 건강인에게 45 mg SC로 투여했다.')
                    a.pop('id');a.pop('sources');a['citation_span_ids']=['S1']
                    b={**a,'text':'SB17은 135 mg IV로 투여했다.'}
                    return {'text':json.dumps({'request_results':{'R1':{'claims':[a,b],'gaps':[]}}})}
                def clause(text,verdict):
                    return {'text':text,'kind':'source_fact','products':['SB17'],'claim_basis':'source_statement',
                        'verdict':verdict,'final_span_ids':['S1'],'reason':'Selected quote check'}
                return {'text':json.dumps({'claims':{
                    'C1':{'selected_support':'supported','context_support':'supported',
                        'clauses':[clause('SB17은 건강인에게 45 mg SC로 투여했다.','supported')]},
                    'C2':{'selected_support':'insufficient','context_support':'insufficient',
                        'clauses':[clause('SB17은 135 mg IV로 투여했다.','unsupported')]}}})}
        for question,expected in [('EMA SB17 건강인 시험의 용량과 경로','answered'),
                                  ('EMA SB17 건강인 시험의 135 mg IV 용량과 경로','partial'),
                                  ('EMA SB17 건강인 시험의 용량과 경로 및 결과','partial')]:
            task={'id':'T1','request':question,'source_roles':['assessment']}
            result=ra_answer.generate(question,{'tasks':[task]},evidence,Client(),'spans')
            self.assertEqual(result['status'],expected)
            self.assertEqual(len(result['claims']),1)
            self.assertTrue(result['validation']['rejected_items'])
            if expected=='answered': self.assertFalse(result['gaps'])


if __name__=='__main__':
    unittest.main()
