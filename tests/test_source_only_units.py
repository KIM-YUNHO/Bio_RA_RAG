import unittest
from rag import selection_candidate as s
from rag.answer_conditions import label
from rag.prose_list_context import intro
from tests.test_selection_candidate import evidence

class SourceOnlyUnitsTests(unittest.TestCase):
    def test_repeated_recommended_dose_keeps_its_own_local_role(self):
        text='For patients weighing 83 kg or less, the recommended dosage is 37 mg initially and 5 weeks later, followed by 37 mg every 13 weeks.'
        e=evidence(text);q='체중 83 kg 이하의 권장 용량과 초기 및 유지 투여 간격은?';c=s.catalog(e,q)
        doses=[x for x in c.values() if x['kind']=='literal_number' and x['value']=='37 mg']
        self.assertEqual(len(doses),2)
        self.assertTrue(all(x.get('value_eligible',True) and x.get('printed_administered_dose') for x in doses))

    def test_equal_ingredient_mass_is_not_a_repeat_regimen_dose(self):
        text='The recommended dose is 37 mg, and a syringe contains sucrose 37 mg.'
        e=evidence(text);q='권장 투여 용량은?';c=s.catalog(e,q)
        doses=[x for x in c.values() if x['kind']=='literal_number' and x['value']=='37 mg']
        self.assertEqual(len(doses),2)
        self.assertTrue(doses[0].get('value_eligible',True));self.assertFalse(doses[1].get('value_eligible',True))

    def raw(self,cid,title='wrong model title',conditions=None):
        return {'study':'none','item':title,'source_label':title,'value_id':cid,'exact':True,'conditions':conditions or []}

    def test_ingredient_masses_keep_names_volume_and_own_units(self):
        text='Each 0.7 mL syringe contains histidine (0.39 mg) and sucrose (47 mg).'
        e=evidence(text);q='시린지의 histidine 및 sucrose 함량은?';c=s.catalog(e,q)
        self.assertTrue(all(x.get('value_eligible') is False for x in c.values() if x['kind']=='literal_number'))
        p=next(x for x in c.values() if x['kind']=='source_paragraph' and x['value']==text)
        i=s.resolve({'status':'answered','items':[self.raw(p['id'],'투여량')],'premise_source_ids':[]},c,e,q,{})['items'][0]
        self.assertEqual(i['value'],text);self.assertNotIn('투여',label(i));self.assertEqual(label(i),'원문 인용')

    def test_generated_age_boundary_is_not_display_evidence(self):
        text='All patients, especially those greater than 47 years, should be monitored.'
        e=evidence(text);q='모니터링 대상 조건은?';c=s.catalog(e,q);p=next(x for x in c.values() if x['value']==text)
        i=s.resolve({'status':'answered','items':[self.raw(p['id'],'47세 이상')],'premise_source_ids':[]},c,e,q,{})['items'][0]
        self.assertNotIn('이상',label(i));self.assertIn('greater than 47',i['value']);self.assertEqual(i['model_source_label'],'47세 이상')

    def test_untyped_range_is_whole_even_when_endpoint_grammar_is_unknown(self):
        text='Subjects were stratified by centre and weight (low: 41 to <54 kg; high: 54 to 79 kg, 79 inclusive).'
        e=evidence(text);q='층화 변수와 두 체중 구간은?';c=s.catalog(e,q)
        self.assertTrue(all(x.get('value_eligible') is False for x in c.values() if x['kind']=='literal_number'))
        self.assertTrue(any(x.get('value_eligible',True) and '41 to <54 kg' in x['value'] and '79 inclusive' in x['value'] for x in c.values()))

    def test_closed_list_retains_comparison_lead_in(self):
        text='Reactions occurring more often in Drug A than placebo were:\n• Induction: cough (9% vs 3%).\n• Maintenance: cough (21% vs 16%).'
        e=evidence(text);q='유도 및 유지의 두 군 발생률은?';c=s.catalog(e,q)
        bullet=next(x for x in c.values() if x['kind']=='source_paragraph' and x['value'].lstrip().startswith('• Induction'))
        i=s.resolve({'status':'answered','items':[self.raw(bullet['id'])],'premise_source_ids':[]},c,e,q,{})['items'][0]
        self.assertEqual(i['source_list_intro']['quote'],'Reactions occurring more often in Drug A than placebo were:')
        self.assertIn('Drug A than placebo',label(i))

    def test_gap_and_new_paragraph_prevent_list_inheritance(self):
        for middle in ('\n[UNSUPPLIED SOURCE GAP]\n','\n\nA different topic follows.\n\n'):
            text='Reactions for Drug A were:'+middle+'• Cough (9%).';e=evidence(text);c=s.catalog(e,'이상반응은?')
            bullet=next(x for x in c.values() if x['value'].lstrip().startswith('•'))
            self.assertIsNone(intro(bullet,e))

    def test_same_whole_cell_deduplicates_without_losing_groups(self):
        text='<table><tr><td>Study</td><td>Dose</td></tr><tr><td>Repeat</td><td>Drug low (4 mg/kg), high (31 mg/kg); control (31 mg/kg)</td></tr></table>'
        e=evidence(text);q='저용량 고용량 및 대조군 용량은?';c=s.catalog(e,q)
        cell=next(x for x in c.values() if x['kind']=='table_cell' and 'Drug low' in x['value'])
        out=s.resolve({'status':'answered','items':[self.raw(cell['id'],'low',[{'dimension':'dose','source_id':cell['id']}]),self.raw(cell['id'],'control')],'premise_source_ids':[]},c,e,q,{})
        self.assertEqual(len(out['items']),1);self.assertIn('control (31 mg/kg)',out['items'][0]['value'])
        self.assertNotIn('low',label(out['items'][0]));self.assertEqual(len(out['duplicate_whole_source_units']),1)

    def test_prompt_context_is_not_an_allowed_answer(self):
        e=evidence('Each 0.7 mL syringe contains histidine (0.39 mg).');q='histidine 함량은?';c=s.catalog(e,q)
        for block in e.values():block.update(title='Source',pdf_page=1,document_type='assessment')
        m=s.prompt(q,{'studies':[],'english_query':'Ingredient quantities'},{'product':'SB17','jurisdiction':'EU','studies':[]},e,c)
        self.assertIn('Allowed ANSWER value IDs',m[1]['content'])
        numbers={cid for cid,x in c.items() if x['kind']=='literal_number'}
        self.assertTrue(numbers.isdisjoint(s.value_ids(c)))

if __name__=='__main__':unittest.main()
