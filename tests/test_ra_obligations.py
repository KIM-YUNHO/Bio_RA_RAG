from copy import deepcopy
import json
import unittest
from pydantic import ValidationError

from rag import ra_answer,ra_models,ra_obligations,ra_spans
from test_ra_citation_completion import block


class ObligationTests(unittest.TestCase):
    def test_product_fields_and_comparison_are_explicit_and_bounded(self):
        q='SB17과 ABP654 건강인 PK 시험 용량·경로, 대조약 구성과 시험 조건의 차이를 설명해줘.'
        rows=ra_obligations.build(q)
        self.assertEqual(len(rows),7)
        for p in ['SB17','ABP654']:
            self.assertEqual({r['facet'] for r in rows if r['product']==p},{'dose_route','comparator','design'})
        self.assertEqual(rows[-1]['facet'],'comparison')
        self.assertEqual(len({r['id'] for r in rows}),7)

    def test_parts_population_stratification_and_assay_targets(self):
        rows=ra_obligations.build('CT-P43 1.1의 시험 설계와 Part 1·Part 2의 서로 다른 목적을 설명하라.')
        self.assertEqual([r['part'] for r in rows if r['facet']=='purpose'],['1','2'])
        rows=ra_obligations.build('CT-P43 1.2의 대상군과 층화 기준을 제시하라.')
        self.assertEqual([r['facet'] for r in rows],['population','stratification'])
        rows=ra_obligations.build('Steqeyma의 PK/면역원성 analyte는 무엇인가?')
        self.assertEqual([r['facet'] for r in rows],['assay_targets'])

    def test_bridging_modalities_and_trailing_document_request_are_retained(self):
        rows=ra_obligations.build('EMA 대조약 선택과 비EEA 브리징을 요약해줘. 문서 상태와 적용 범위도 알려줘.')
        self.assertEqual([r['facet'] for r in rows],['reference_selection','analytical_bridge','clinical_bridge','document_status_scope'])
        self.assertEqual(rows[-1]['request_id'],'R2')

    def test_simple_and_oversized_questions_keep_original_path_without_dropping_fields(self):
        self.assertEqual(ra_obligations.build('EMA SB17의 용량과 경로를 알려줘.'),[])
        q='SB17 ABP654 CTP43 BMAB1200 BAT2206의 용량과 대조약과 시험 조건 차이, 추적 기간, 체중·BMI 선정 기준을 설명해줘.'
        self.assertEqual(ra_obligations.build(q),[])

    def test_navigation_prefers_complete_comparator_text_without_copying_it(self):
        evidence={'E1':block('Subjects received EU-approved Stelara or US-licensed Stelara.'),
                  'E2':block('Primary objective (Part 2):'),
                  'E3':block('Subjects received EU-approved Stelara.',product='ABP654')}
        rows=ra_obligations.build('SB17과 ABP654의 용량과 대조약을 비교해줘.')
        hints=ra_obligations.pointers('SB17과 ABP654의 용량과 대조약을 비교해줘.',evidence,rows)
        spans,_=ra_spans.catalog(evidence)
        rid=next(r['id'] for r in rows if r['product']=='SB17' and r['facet']=='comparator')
        self.assertEqual(hints[rid][0],'S1')
        self.assertTrue(all(spans[a]['evidence']=='E1' for a in hints[rid]))

    def test_contract_rejects_omitted_requirement_wrong_product_and_empty_gap(self):
        rows=ra_obligations.build('SB17과 ABP654의 용량과 대조약을 비교해줘.')
        expected={r['id']:r for r in rows}
        value={'requirement_results':{k:{'claims':[],'gaps':['Need source']} for k in expected}}
        ra_models.ObligationDraft.model_validate(value,context={'requirements':expected})
        broken=deepcopy(value);broken['requirement_results'].pop(next(iter(expected)))
        with self.assertRaises(ValidationError):ra_models.ObligationDraft.model_validate(broken,context={'requirements':expected})
        broken=deepcopy(value);broken['requirement_results'][next(iter(expected))]['gaps']=[]
        with self.assertRaises(ValidationError):ra_models.ObligationDraft.model_validate(broken,context={'requirements':expected})
        broken=deepcopy(value);key=next(k for k,r in expected.items() if r['product']=='SB17')
        broken['requirement_results'][key]['claims']=[{'citation_span_ids':['S1'],'text':'ABP654 has a dose.',
            'kind':'source_fact','products':['ABP654'],'task_ids':['T1'],'claim_basis':'source_statement'}]
        with self.assertRaises(ValidationError):ra_models.ObligationDraft.model_validate(broken,context={'requirements':expected,'task_ids':['T1']})

    def test_schema_requires_every_field_and_puts_citation_selection_first(self):
        evidence={'E1':block('Healthy subjects received 45 mg SC.')}
        question='SB17의 대상군과 층화 기준을 설명해줘.'
        contract=ra_answer.schema(evidence,[{'id':'T1'}],'spans',question=question)
        expected={r['id'] for r in ra_obligations.build(question)}
        self.assertEqual(contract['name'],'ra_requirement_grounded_answer')
        self.assertEqual(set(contract['schema']['properties']['requirement_results']['required']),expected)
        self.assertEqual(next(iter(contract['schema']['$defs']['claim_SB17']['properties'])),'citation_span_ids')
        payload=json.loads(ra_answer.messages(question,{'tasks':[{'id':'T1','request':question}]},evidence,'spans')[1]['content'])
        self.assertEqual({r['id'] for r in payload['question_requirements']},expected)
        self.assertFalse(payload['source_navigation_not_proof'][next(r['id'] for r in payload['question_requirements'] if r['facet']=='stratification')])

    def test_generation_schema_binds_product_citations_before_assertion(self):
        evidence={'E1':block('Healthy subjects received 45 mg SC.'),
                  'E2':block('Healthy subjects received 90 mg SC.',product='ABP654')}
        contract=ra_answer.schema(evidence,[{'id':'T1'}],'spans',question='SB17과 ABP654의 용량과 대조약을 알려줘.')
        defs=contract['schema']['$defs']
        self.assertEqual(defs['ids_SB17']['enum'],['S1'])
        self.assertEqual(defs['ids_ABP654']['enum'],['S2'])
        self.assertEqual(defs['claim_SB17']['properties']['products']['items']['enum'],['SB17'])

    def test_bridging_reading_cues_are_exact_runtime_source_substrings(self):
        text='Bridging will always include data from analytical studies and may also include data from clinical PK and/or PD bridging studies for all three products.'
        evidence={'E1':block(text,product=None)}
        rows=ra_obligations.build('EMA 브리징을 설명해줘.')
        cues=ra_obligations.reading_cues(evidence,rows)
        self.assertEqual(len(cues),2)
        self.assertTrue(all(cue['exact_reading_cue'] in text for hints in cues.values() for cue in hints))
        self.assertEqual(ra_obligations.reading_cues({'E1':block('General principles.',product=None)},rows),{})

    def test_unrequested_unselected_count_is_omitted_without_replacing_a_value(self):
        from test_ra_citation_completion import claim
        evidence={'E1':block('The population was healthy Japanese male subjects.',product='CTP43')}
        original=claim(evidence,'건강한 일본 남성 327명이다.');original['products']=['CTP43']
        row={'facet':'population'}
        reduced,changes=ra_obligations.omit_unrequested_population_count(original,row,evidence,'대상군을 알려줘.')
        self.assertEqual(reduced['text'],'건강한 일본 남성이다.');self.assertTrue(changes)
        self.assertEqual(reduced['sources'],original['sources'])
        reduced,changes=ra_obligations.omit_unrequested_population_count(original,row,evidence,'대상군 인원을 알려줘.')
        self.assertEqual(reduced,original);self.assertFalse(changes)
        original['text']='건강한 일본 남성을 대상으로 하였으며, 327명의 피험자가 등록되었다.'
        reduced,changes=ra_obligations.omit_unrequested_population_count(original,row,evidence,'대상군을 알려줘.')
        self.assertEqual(reduced['text'],'건강한 일본 남성을 대상으로 하였다.')

    def test_review_retains_only_already_reviewed_part_identity(self):
        from rag import ra_review
        from test_ra_citation_completion import claim
        evidence={'E1':block('Primary objective (Part 2):\n\nTo demonstrate pharmacokinetic similarity.')}
        parent=claim(evidence,'Part 2의 목적은 약동학적 유사성을 입증하는 것이다.',('S1','S2'))
        raw={'claims':{'C1':{'selected_support':'supported','context_support':'supported','clauses':[{
            'text':parent['text'],'kind':'source_fact','products':['SB17'],'claim_basis':'source_statement',
            'verdict':'supported','final_span_ids':['S2'],'reason':'Selected proof'}]}},
            'coverage':{'T1':{'status':'partial','reason':'Coverage separate'}},'gaps':[]}
        kept,rejected,_=ra_review.apply(raw,evidence,[parent],[{'id':'T1'}])
        self.assertFalse(rejected);self.assertEqual(kept[0]['reviewed_identity_span_ids_retained'],['S1'])
        self.assertEqual(set(s['citation_alias'] for s in kept[0]['sources']),{'S1','S2'})
        parent=claim(evidence,parent['text'],('S2',))
        kept,rejected,_=ra_review.apply(raw,evidence,[parent],[{'id':'T1'}])
        self.assertFalse(kept);self.assertEqual(rejected[0]['reason'],'claim_number_not_in_final_citations')

    def test_whole_supported_proposal_keeps_its_proof_without_review_added_facts(self):
        from rag import ra_review
        from test_ra_citation_completion import claim
        evidence={'E1':block('Healthy subjects received 45 mg SC.\n\nThe confidence interval was 90%.')}
        parent=claim(evidence,'SB17은 건강인에게 45 mg SC로 투여했다.',('S1','S2'))
        row={'selected_support':'supported','context_support':'supported','clauses':[{
            'text':'SB17은 45 mg SC로 투여했고 90% 신뢰구간을 계산했다.','kind':'source_fact','products':['SB17'],
            'claim_basis':'source_statement','verdict':'supported','final_span_ids':['S1','S2'],'reason':'Selected proof'}]}
        kept,rejected,_=ra_review.apply({'claims':{'C1':row}},evidence,[parent],[{'id':'T1'}],truth_only=True)
        self.assertFalse(rejected);self.assertEqual(kept[0]['text'],parent['text'])
        self.assertEqual(kept[0]['sources'],parent['sources']);self.assertTrue(kept[0]['reviewed_proposal_preserved'])
        parent['text']='SB17은 건강인에게 135 mg SC로 투여했다.'
        kept,rejected,_=ra_review.apply({'claims':{'C1':row}},evidence,[parent],[{'id':'T1'}],truth_only=True)
        self.assertFalse(any('135' in c['text'] or c.get('reviewed_proposal_preserved') for c in kept))

    def test_runtime_tracks_each_requirement_and_keeps_unanswered_field_visible(self):
        evidence={'E1':block('Healthy subjects participated in the study.')}
        question='SB17의 대상군과 층화 기준을 설명해줘.'
        tasks=[{'id':'T1','request':question,'source_roles':['assessment']}]
        class Tokens:
            def one(self,value):return len(value)
            def messages(self,values):return sum(len(m['content']) for m in values)
        class Client:
            tokenizer=Tokens();input_limit=100000
            def __init__(self):self.names=[]
            def __call__(self,messages,json_schema=None,provider=None):
                self.names.append(json_schema['name'])
                data=json.loads(messages[1]['content'])
                if json_schema['name']=='ra_requirement_grounded_answer':
                    rows=data['question_requirements'];value={r['id']:{'claims':[],'gaps':['Need source']} for r in rows}
                    population=next(r['id'] for r in rows if r['facet']=='population')
                    value[population]={'claims':[{'citation_span_ids':['S1'],'text':'SB17은 건강인을 대상으로 했다.',
                        'kind':'source_fact','products':['SB17'],'task_ids':['T1'],'claim_basis':'source_statement'}],'gaps':[]}
                    return {'text':json.dumps({'requirement_results':value})}
                proposal=data['proposed_claims'][0]
                return {'text':json.dumps({'claims':{'C1':{'selected_support':'supported','context_support':'supported',
                    'clauses':[{'text':proposal['text'],'kind':proposal['kind'],'products':proposal['products'],
                        'claim_basis':proposal['claim_basis'],'verdict':'supported','final_span_ids':['S1'],'reason':'Selected proof'}]}}})}
        client=Client();result=ra_answer.generate(question,{'tasks':tasks},evidence,client,'spans')
        self.assertEqual(client.names,['ra_requirement_grounded_answer','ra_selected_claim_truth'])
        self.assertEqual(len(result['claims']),1)
        rows=result['question_requirement_coverage']['requirements']
        self.assertTrue(next(r for r in rows if r['facet']=='population')['has_verified_claim'])
        self.assertFalse(next(r for r in rows if r['facet']=='stratification')['has_verified_claim'])
        self.assertTrue(any('층화' in gap for gap in result['gaps']))
        self.assertEqual(result['status'],'partial')

    def test_split_document_scope_is_not_lost_for_omitting_the_title_word(self):
        evidence={'E1':block('This guideline addresses quality requirements.',product=None)}
        question='EMA 브리징을 설명해줘. 문서 상태와 적용 범위도 알려줘.'
        tasks=[{'id':'T1','request':question}]
        class Tokens:
            def one(self,value):return len(value)
            def messages(self,values):return sum(len(m['content']) for m in values)
        class Client:
            tokenizer=Tokens();input_limit=100000
            def __call__(self,messages,json_schema=None,provider=None):
                data=json.loads(messages[1]['content'])
                if json_schema['name']=='ra_requirement_grounded_answer':
                    rows=data['question_requirements'];value={r['id']:{'claims':[],'gaps':['Need source']} for r in rows}
                    key=next(r['id'] for r in rows if r['facet']=='document_status_scope')
                    value[key]={'claims':[{'citation_span_ids':['S1'],'text':'이 지침의 적용 범위는 품질 요구 사항이다.',
                        'kind':'source_fact','products':[],'task_ids':['T1'],'claim_basis':'source_statement'}],'gaps':[]}
                    return {'text':json.dumps({'requirement_results':value})}
                return {'text':json.dumps({'claims':{'C1':{'selected_support':'supported','context_support':'supported',
                    'clauses':[{'text':'적용 범위는 품질 요구 사항이다.','kind':'source_fact','products':[],
                        'claim_basis':'source_statement','verdict':'supported','final_span_ids':['S1'],'reason':'Selected proof'}]}}})}
        result=ra_answer.generate(question,{'tasks':tasks},evidence,Client(),'spans')
        self.assertIn('적용 범위는 품질 요구 사항이다.',result['claims'][0]['text'])
        self.assertTrue(next(r for r in result['question_requirement_coverage']['requirements']
            if r['facet']=='document_status_scope')['has_verified_claim'])

    def test_population_slot_cannot_be_filled_with_stratification_and_background(self):
        row={'facet':'population','product':'CTP43'}
        self.assertFalse(ra_obligations.matches(row,{'text':'체중에 따라 층화하였다.','products':['CTP43']}))
        self.assertTrue(ra_obligations.matches(row,{'text':'건강한 일본인 남성을 대상으로 했다.','products':['CTP43']}))
        row['facet']='comparison';row['product']=None
        self.assertFalse(ra_obligations.matches(row,{'text':'SB17의 조건이 달랐다.','products':['SB17']}))

    def test_truth_review_cannot_add_a_quantity_from_another_selected_paragraph(self):
        from rag import ra_review
        from test_ra_bound_review import blocks,proposal,review,clause,TASK
        parents=proposal(text='SB17은 45 mg SC로 투여하였다.',ids=('S2','S3'))
        parents[0]['requirement_id']='R1_F1';parents[0]['requirement_ids']=['R1_F1']
        extra=clause(text='SB17은 45 mg SC로 투여하였고 90% 신뢰구간을 계산하였다.',ids=('S2','S3'))
        kept,rejected,_=ra_review.apply(review([extra]),blocks(),parents,[TASK])
        self.assertFalse(kept)
        self.assertEqual(rejected[0]['reason'],'review_added_quantity_not_in_proposed_assertion')
        kept,rejected,_=ra_review.apply(review([clause(text=parents[0]['text'],ids=('S3',))]),blocks(),parents,[TASK])
        self.assertTrue(kept);self.assertFalse(rejected)
        parents[0]['text']='SB17은 건강인을 대상으로 했다.'
        extra=clause(text='SB17은 201명의 건강인을 대상으로 했다.',ids=('S2',))
        kept,rejected,_=ra_review.apply(review([extra]),blocks(),parents,[TASK])
        self.assertFalse(kept)
        self.assertTrue(rejected)


if __name__=='__main__':unittest.main()
