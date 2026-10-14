import unittest
from rag.question_anchors import catalog, expand, schema
from rag.contract_query import resolve
from tests import test_contract_query as fixtures


class QuestionAnchorTests(unittest.TestCase):
    def test_shared_literal_source_can_be_reused_without_occurrence_drift(self):
        q='20 mg 및 40 mg의 tmax 중앙값과 평균'
        c=catalog(q); find=lambda value:next(i for i,a in c.items() if a['value']==value)
        group={'id':'pk','field_ids':[find('median'),find('mean')],'literal_fields':[],
               'conditions':[{'operator':'eq','anchor_ids':[find('tmax')]}],'literal_conditions':[],
               'axes':[{'options':[{'operator':'eq','anchor_ids':[find('20 mg')]},{'operator':'eq','anchor_ids':[find('40 mg')]}]}]}
        r=fixtures.ContractQueryTests().response();r['requirements']={'request_groups':[group],'non_request_mentions':[]}
        out=resolve(q,r)
        self.assertEqual(len(out['requirements']['requests']),4)
        self.assertEqual(out['requirements']['status'],'anchored_candidate')
        self.assertTrue(all(req['conditions'][0]['values'][0]['anchor']['occurrence']==0 for req in out['requirements']['requests']))

    def test_coordinated_reference_ids_have_own_literal_spans(self):
        q='CT-P43 EU·US Stelara 대비 Cmax GMR'
        c=catalog(q)
        for value,quote in [('EU-Stelara','EU'),('US-Stelara','US')]:
            a=next(a for a in c.values() if a['value']==value)
            self.assertEqual(a['quote'],quote);self.assertEqual(q[a['start']:a['end']],quote)

    def test_unknown_ID_and_mixed_dimension_conditions_fail(self):
        q='Cmax 20 mg GMR';c=catalog(q);find=lambda value:next(i for i,a in c.items() if a['value']==value)
        group={'id':'x','field_ids':[find('GMR')],'literal_fields':[],'literal_conditions':[],
               'conditions':[{'operator':'ordered_pair','anchor_ids':[find('Cmax'),find('20 mg')]}],'axes':[]}
        with self.assertRaises(ValueError):expand(q,{'request_groups':[group],'non_request_mentions':[]})
        group['conditions']=[];group['field_ids']=['Q999']
        with self.assertRaises(ValueError):expand(q,{'request_groups':[group],'non_request_mentions':[]})

    def test_comparison_cannot_be_single_arm_or_condition_used_as_field(self):
        q='SB17 EU Stelara 대비 Cmax GMR';c=catalog(q);find=lambda value:next(i for i,a in c.items() if a['value']==value)
        group={'id':'x','field_ids':[find('GMR')],'literal_fields':[],'literal_conditions':[],
               'conditions':[{'operator':'eq','anchor_ids':[find('EU-Stelara')]}],'axes':[]}
        with self.assertRaises(ValueError):expand(q,{'request_groups':[group],'non_request_mentions':[]})
        group['conditions']=[];group['field_ids']=[find('EU-Stelara')]
        with self.assertRaises(ValueError):expand(q,{'request_groups':[group],'non_request_mentions':[]})

    def test_inventory_is_question_only_and_repeated_mentions_keep_offsets(self):
        c=catalog('Cmax 평균, 별도로 Cmax 중앙값')
        mentions=[a for a in c.values() if a['value']=='Cmax']
        self.assertEqual([a['anchor']['occurrence'] for a in mentions],[0,1])
        self.assertNotEqual(mentions[0]['start'],mentions[1]['start'])

    def test_typed_schema_disallows_endpoint_fields_for_statistical_requests(self):
        from rag.understand_query import SCHEMA
        q='20 mg 및 40 mg의 tmax 중앙값과 평균';c=catalog(q)
        variants=schema(q,SCHEMA['schema'])['schema']['properties']['requirements']['properties']['request_groups']['items']['anyOf']
        s=next(v['properties'] for v in variants if v['properties']['field_ids']['maxItems'])
        allowed=s['field_ids']['items']['enum']
        self.assertEqual({c[i]['value'] for i in allowed},{'median','mean'})
        self.assertTrue(all(len(v['properties']['operator']['enum'])==3 for v in s['conditions']['items']['anyOf']))

    def test_product_name_without_comparison_does_not_create_an_arm_condition(self):
        self.assertFalse(any(a['dimension']=='comparison' for a in catalog('ABP654 모노그래프 tmax 평균').values()))

    def test_two_comparisons_are_alternatives_to_answer_not_simultaneous_AND(self):
        from rag.contract_query import expand_groups
        from tests.test_request_contract import term
        p={'request_groups':[{'id':'x','fields':[term('statistic','GMR')],'axes':[],
             'conditions':[{'dimension':'comparison','operator':'unordered_pair','values':[{'value':v,'anchor':{'quote':v,'occurrence':0}} for v in pair]} for pair in [('A','B'),('A','C')]]}], 'non_request_mentions':[]}
        with self.assertRaises(ValueError):expand_groups(p)


if __name__=='__main__':unittest.main()
