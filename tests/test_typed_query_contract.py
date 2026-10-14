import json
import unittest
from rag import contract_query,selection_candidate as selector
from rag.question_anchors import catalog,schema,expand
from rag.contract_terms import atoms
from rag.request_contract import ground
from rag.understand_query import SCHEMA
from tests.test_request_contract import term
from tests.test_selection_candidate import evidence

def understanding():
    return {'intent':'fact_lookup','products':[],'studies':[],'study_scope':'unspecified','study_mentions':[],
            'jurisdictions':[],'jurisdiction_mentions':[],'doc_groups':[],'topic':'pk_results',
            'english_query':'AUCinf geometric mean ratio','keywords':['AUCinf']}

class TypedQueryContractTests(unittest.TestCase):
    def test_bounded_original_lists_keep_all_slots_with_one_intent_call(self):
        q='CT-P43 EU Stelara 대비 AUCinf, Cmax GMR와 90% CI'
        calls=[]
        def client(messages,json_schema=None,provider=None):
            calls.append(json_schema['name'])
            self.assertNotIn('evidence',messages[1]['content'].lower())
            return {'text':json.dumps(understanding()),'model_reported':'offline','prompt_tokens':12,'completion_tokens':8}
        r=contract_query.understand(q,chat_client=client)
        self.assertEqual(calls,['query_understanding'])
        self.assertEqual(r['request_contract_method'],'bounded_original_question_grammar')
        req=r['request_contract']
        self.assertEqual(len(req['requests']),4)
        self.assertEqual(req['status'],'anchored_candidate')
        self.assertTrue(r['translation_omissions'])
        self.assertEqual({v['value'] for x in req['requests'] for c in x['conditions'] if c['dimension']=='endpoint' for v in c['values']},{'AUCinf','Cmax'})

    def test_result_and_criterion_cannot_share_a_provider_field_group(self):
        q='Week 8 Cmax GMR 주 분석과 동등성 한계'
        anchors=catalog(q);fid=lambda value:next(i for i,a in anchors.items() if a['value']==value)
        variants=schema(q,SCHEMA['schema'])['schema']['properties']['requirements']['properties']['request_groups']['items']['anyOf']
        for v in variants:
            if v['properties']['field_ids']['maxItems']==0:continue
            ids=v['properties']['field_ids']['items']['enum']
            self.assertFalse(fid('GMR') in ids and fid('margin') in ids)
        g={'id':'x','field_ids':[fid('GMR'),fid('margin')],'literal_fields':[],
           'conditions':[],'literal_conditions':[],'axes':[]}
        with self.assertRaises(ValueError):expand(q,{'request_groups':[g],'non_request_mentions':[]})

    def test_comparison_options_are_only_provider_axes(self):
        q='CT-P43 EU Stelara 대비 Cmax GMR';anchors=catalog(q)
        variants=schema(q,SCHEMA['schema'])['schema']['properties']['requirements']['properties']['request_groups']['items']['anyOf']
        for v in variants:
            props=v['properties']
            self.assertTrue(all('ordered_pair' not in s['properties']['operator']['enum'] for s in props['conditions']['items']['anyOf']))
            self.assertTrue(any('ordered_pair' in v['properties']['options']['items']['properties']['operator']['enum'] for v in props['axes']['items']['anyOf']))

    def test_unknown_only_fields_have_no_selectable_placeholder_or_occurrence_index(self):
        variants=schema('새 측정값은?',SCHEMA['schema'])['schema']['properties']['requirements']['properties']['request_groups']['items']['anyOf']
        self.assertEqual(len(variants),1)
        p=variants[0]['properties']
        self.assertEqual(p['field_ids']['maxItems'],0)
        self.assertEqual(p['literal_fields']['minItems'],1)
        self.assertEqual(p['literal_fields']['items']['properties']['occurrence']['enum'],[0])
        self.assertEqual(p['conditions']['maxItems'],0)
        self.assertEqual(p['axes']['maxItems'],0)

    def test_spelled_arm_counts_are_source_roles_without_special_document_rules(self):
        for value in ('five-arm','two arm','18-arm','three-arm'):
            self.assertIn(('attribute','arms'),{(a['dimension'],a['value']) for a in atoms(value)})

    def test_optional_unknown_quote_cannot_erase_complete_required_support(self):
        q='Cmax GMR';req=ground(q,{'requests':[{'id':'r','field':term('statistic','GMR'),
                       'conditions':[{'dimension':'endpoint','operator':'eq','values':[{'value':'Cmax','anchor':{'quote':'Cmax','occurrence':0}}]}]}],
                       'non_request_mentions':[]})
        e=evidence('<table><tr><th>Endpoint</th><th>GMR</th></tr><tr><td>Cmax</td><td>1.13</td></tr></table>\n\nA separate related observation remains unconfirmed.')
        u={'request_contract':req,'studies':[],'topic':'pk_results'}
        cs=selector.catalog_for_query(e,q,u)
        value=next(c for c in cs.values() if c['kind']=='table_cell' and c['value']=='1.13')
        related=next(c for c in cs.values() if c['kind']=='source_paragraph' and 'separate related' in c['value'])
        out={'items':[{'source_selection':{'value_id':c['id']},'conditions':[],'exact':True,
             'source_label':'Source','value':c['value'],'evidence':['E1']} for c in (value,related)],
             'status':'answered','answer_ko':'','validation':{}}
        selector._finalize_independent_contract(out,cs,e,q,u)
        self.assertEqual(out['status'],'answered')
        self.assertEqual(out['validation']['independent_question_coverage']['missing'],[])
        self.assertFalse(out['items'][1]['exact'])

if __name__=='__main__':unittest.main()
