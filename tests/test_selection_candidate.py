import unittest
from rag import selection_candidate as s


def evidence(text):
    return {'E1':{'text':text,'product_id':'SB17','document_id':'synthetic','version_id':'v',
        'structure':{'section_id':'section'}},
        'E2':{'text':'Study SB17-1001','product_id':'SB17','document_id':'synthetic','version_id':'v',
        'structure':{'section_id':'section'}}}


class SelectionCandidateTests(unittest.TestCase):
    def test_stronger_same_row_AND_support_removes_weaker_selection(self):
        q='Cmax의 단백질 보정, ADA 음성 GMR'
        e=evidence('<table><tr><th>Analysis</th><th>Cmax GMR</th></tr><tr><td>Protein adjusted</td><td>1.18</td></tr><tr><td>Protein adjusted and ADA negative</td><td>1.09</td></tr></table>')
        c=s.catalog(e,q)
        self.assertFalse(next(x.get('value_eligible',True) for x in c.values() if x['value']=='1.18'))
        self.assertTrue(next(x.get('value_eligible',True) for x in c.values() if x['value']=='1.09'))

    def test_another_table_cannot_dominate(self):
        q='Cmax의 단백질 보정, ADA 음성 GMR'
        e=evidence('<table><tr><th>Analysis</th><th>Cmax GMR</th></tr><tr><td>Protein adjusted</td><td>1.18</td></tr></table>\n<table><tr><th>Analysis</th><th>Cmax GMR</th></tr><tr><td>Protein adjusted and ADA negative</td><td>1.09</td></tr></table>')
        c=s.catalog(e,q)
        self.assertTrue(next(x.get('value_eligible',True) for x in c.values() if x['value']=='1.18'))

    def test_whole_prose_roles_are_audited_without_binding_every_number(self):
        q='Tmax 중앙값과 단위를 알려줘'
        e=evidence('The median Tmax was 8.5 days after a 90 mg dose.')
        c=s.catalog(e,q);contract,audits=s.audits(c,e,q)
        whole=[a for cid,a in audits.items() if c[cid]['kind']=='source_paragraph']
        self.assertTrue(whole)
        self.assertTrue(any(x['status']=='match' for a in whole for x in a['checks']))
        for cid,a in audits.items():self.assertNotEqual('literal_number',c[cid]['kind'])
        e=evidence('The median Tmax was 8.5 days and mean Cmax was 24.1 ng/mL.')
        c=s.catalog(e,q);_,audits=s.audits(c,e,q)
        self.assertFalse(any(x['status']=='match' for a in audits.values() for x in a['checks']))

    def test_table_and_prose_matching_never_share_table_dominance(self):
        q='Tmax 중앙값과 단위'
        e=evidence('Median Tmax was 8.5 days.\n\n<table><tr><th>Parameter</th><th>Median</th></tr><tr><td>Tmax (days)</td><td>8.5</td></tr></table>')
        c=s.catalog(e,q);_,audits=s.audits(c,e,q)
        self.assertTrue(any(a['fact'].get('table_id') for a in audits.values()))
        self.assertTrue(any(a['fact'].get('value_binding_status')=='whole_source_text' for a in audits.values()))

    def test_prose_pk_unit_request_keeps_complete_values_and_units(self):
        q='CL/F와 V/F 값과 단위는?'
        e=evidence('In the population pharmacokinetic analysis CL/F and V/F were 0.72 L/day and 19.4 L, respectively.')
        c=s.catalog(e,q)
        self.assertTrue(any(x.get('value_eligible',True) and 'L/day' in x['value'] and '19.4 L' in x['value'] for x in c.values()))
        self.assertFalse(any(x.get('value_eligible',True) and x['kind']=='literal_number' for x in c.values()))

    def test_uncompiled_original_requests_cannot_be_called_complete(self):
        q='Drug A, EU-Stelara, US-Stelara 군의 Tmax 중앙값과 범위는?'
        e=evidence('Median Tmax was 8.5 days.');c=s.catalog(e,q)
        out={'status':'answered','items':[],'answer_ko':'','validation':{}}
        s.finalize(out,c,e,q,{})
        self.assertNotEqual('answered',out['status'])
        self.assertIn('range',out['validation']['unresolved_original_pk_requests']['uncompiled_roles'])

    def test_unique_narrative_counts_are_completed_without_API(self):
        q='SB17-1001 시험의 군별 PK set 인원은?'
        e=evidence('PK analysis sets included n = 61 in the Drug A group, n = 62 in the Reference B group and n =\n63 in the Reference C group.')
        c=s.catalog(e,q)
        out=s.resolve({'status':'not_found','items':[],'premise_source_ids':[]},c,e,q,{'studies':['ST_SB17_1001']})
        self.assertEqual({'61','62','63'},{i['value'] for i in out['items']})
        self.assertEqual(3,len(out['typed_count_completion']['added_source_ids']))
        self.assertEqual({'Drug A','Reference B','Reference C'},{x['value'] for i in out['items'] for x in i['conditions'] if x['dimension']=='arm'})

    def test_conflicting_counts_and_wrong_study_do_not_auto_complete(self):
        q='SB17-1001 시험의 군별 PK set 인원은?'
        e=evidence('PK set included n = 61 in the Drug A group. PK set included n = 62 in the Drug A group.')
        c=s.catalog(e,q)
        out=s.resolve({'status':'not_found','items':[],'premise_source_ids':[]},c,e,q,{'studies':['ST_SB17_1001']})
        self.assertFalse(out['items'])
        e['E2']['text']='Study CT-P43 1.2';c=s.catalog(e,q)
        out=s.resolve({'status':'not_found','items':[],'premise_source_ids':[]},c,e,q,{'studies':['ST_SB17_1001']})
        self.assertFalse(out['items'])

    def test_unsupported_original_condition_is_visible_in_rendered_answer(self):
        q='SB17 Cmax GMR에서 EU-Stelara 제외'
        e=evidence('<table><tr><th>Endpoint</th><th>GMR</th></tr><tr><td>Cmax</td><td>1.13</td></tr></table>')
        c=s.catalog(e,q);cid=next(k for k,v in c.items() if v['value']=='1.13')
        item={'source_label':'Cmax GMR','value':'1.13','conditions':[],'evidence':['E1'],'exact':True,'source_selection':{'value_id':cid}}
        out={'status':'answered','items':[item],'answer_ko':'Cmax GMR: 1.13','validation':{}}
        s.finalize(out,c,e,q,{})
        self.assertEqual(out['status'],'partial')
        self.assertFalse(out['items'][0]['exact'])
        self.assertIn('조건 미확정',out['answer_ko'])
        self.assertIn('1.13',out['answer_ko'])
