import unittest
from rag import selection_candidate as s
from rag.local_trial_scope import requested,finalize
from rag.answer_conditions import label
from tests.test_selection_candidate import evidence


class LocalTrialScopeTests(unittest.TestCase):
    def raw(self,cid):
        return {'study':'none','item':'model heading','source_label':'model heading','value_id':cid,'exact':True,'conditions':[]}

    def resolved(self,c,cid,e,q):
        out=s.resolve({'status':'answered','items':[self.raw(cid)],'premise_source_ids':[]},c,e,q,{})
        out['validation']={}
        return out

    def test_two_requested_trials_do_not_admit_third_trial_table(self):
        q='AA-7 유도시험 및 AA-8 유지시험의 cough 비율은?'
        e=evidence('Table 3: Trial ZZ-9 adverse reactions\n<table><tr><td>Reaction</td><td>Rate</td></tr><tr><td>Cough</td><td>14%</td></tr></table>\nInduction (AA-7): cough (6% vs 2%).\nMaintenance (AA-8): cough (17% vs 9%).')
        c=s.catalog(e,q)
        wrong=next(x for x in c.values() if x['kind']=='table_cell' and x['value']=='14%')
        self.assertFalse(wrong.get('value_eligible',True))
        self.assertEqual(wrong['local_trial_scope']['present_identifiers'],['ZZ-9'])
        good=[x for x in c.values() if x['kind']=='source_paragraph' and x.get('source_sentence') and x['value'].lstrip().startswith(('Induction','Maintenance'))]
        self.assertEqual(len(good),2)
        out=s.resolve({'status':'answered','items':[self.raw(x['id']) for x in good],'premise_source_ids':[]},c,e,q,{})
        out['validation']={}
        finalize(out,c,q)
        self.assertEqual(out['status'],'answered');self.assertEqual(out['validation']['local_trial_coverage']['missing'],[])

    def test_one_trial_does_not_satisfy_two_trial_request(self):
        q='Study AA-7 and Trial AA-8 cough rates?';e=evidence('Trial AA-7: cough (6% vs 2%).')
        c=s.catalog(e,q);p=next(x for x in c.values() if x['kind']=='source_paragraph')
        out=self.resolved(c,p['id'],e,q);finalize(out,c,q)
        self.assertEqual(out['status'],'partial');self.assertEqual(out['validation']['local_trial_coverage']['missing'],['AA-8'])

    def test_unrelated_page_trial_does_not_bind_table_without_caption(self):
        q='Trial AA-7의 cough 비율은?';e=evidence('<table><tr><td>Reaction</td><td>Rate</td></tr><tr><td>Cough</td><td>14%</td></tr></table>')
        e['E2']['text']='Trial AA-7';c=s.catalog(e,q)
        p=next(x for x in c.values() if x['kind']=='table_cell' and x['value']=='14%')
        self.assertEqual(p['local_trial_scope']['status'],'unknown');self.assertTrue(p.get('value_eligible',True))
        out=self.resolved(c,p['id'],e,q);finalize(out,c,q)
        self.assertEqual(out['status'],'partial');self.assertFalse(out['items'][0]['exact'])

    def test_metadata_caption_is_not_source_trial_proof(self):
        q='Trial AA-7의 cough 비율은?';e=evidence('<table><tr><td>Reaction</td><td>Rate</td></tr><tr><td>Cough</td><td>14%</td></tr></table>')
        e['E1']['structure']['caption']='Table 3: Trial AA-7';c=s.catalog(e,q)
        p=next(x for x in c.values() if x['value']=='14%')
        self.assertEqual(p['local_trial_scope']['status'],'unknown')

    def test_gap_closes_caption_inheritance(self):
        q='Trial AA-7의 cough 비율은?';e=evidence('Table 3: Trial AA-7\n[UNSUPPLIED SOURCE GAP]\n<table><tr><td>Reaction</td><td>Rate</td></tr><tr><td>Cough</td><td>14%</td></tr></table>')
        c=s.catalog(e,q);p=next(x for x in c.values() if x['value']=='14%')
        self.assertEqual(p['local_trial_scope']['status'],'unknown')

    def test_native_caption_is_displayed_with_own_table_cell(self):
        q='Trial AA-7의 cough 비율은?';e=evidence('Table 3: Trial AA-7 cough rates\n<table><tr><td>Reaction</td><td>Rate</td></tr><tr><td>Cough</td><td>14%</td></tr></table>')
        c=s.catalog(e,q);p=next(x for x in c.values() if x['value']=='14%')
        out=self.resolved(c,p['id'],e,q);finalize(out,c,q)
        self.assertEqual(out['status'],'answered');self.assertIn('Trial AA-7 cough rates',label(out['items'][0]))

    def test_original_exclusions_and_alternatives_are_not_flattened(self):
        for q in ('AA-7 시험 제외하고 AA-8 시험', 'Trial AA-7 or Trial AA-8', 'AA-7 시험이 아니라 AA-8 시험'):
            self.assertIsNone(requested(q))

    def test_request_numbers_without_trial_identifier_roles_do_not_activate_guard(self):
        self.assertIsNone(requested('PASI-75와 두 군의 dose를 비교해줘'))
        self.assertIsNone(requested('Cmax and 12-week outcomes'))
        self.assertIsNone(requested('ABP-654 시험의 투여 용량은?'))


if __name__=='__main__':unittest.main()
