import unittest
from rag.evidence.source_selection import catalog
from rag.evidence.contract_relations import prose_fact
from rag.evidence.prose_relation_scope import measure_bundle,linked
from tests.contract_match import condition

RESULT='According to the primary targeted estimand, an estimated difference between Treatment A and Treatment B was -1.20% (90% confidence interval [CI]: -4.20%, 1.80%).'
DEFINITION='In Study SB17-3001, the primary endpoint was percent change in PASI at Week 9.'
MARGIN='The similarity margin recommended by the Agency was ±11%.'

def evidence(text):
    return {'E1':{'text':text,'product_id':'SB17','document_id':'synthetic','version_id':'v',
        'structure':{'section_id':'section'}},
        'E2':{'text':'Study SB17-1001','product_id':'SB17','document_id':'synthetic','version_id':'v',
        'structure':{'section_id':'section'}}}

def sentence(e,needle):return next(c for c in catalog(e).values() if c['kind']=='source_paragraph' and c.get('source_sentence') and needle in c['value'])

class ProseRelationScopeTests(unittest.TestCase):
    def test_endpoint_definition_and_trial_have_exact_local_provenance(self):
        e=evidence(DEFINITION+' '+RESULT+' '+MARGIN)
        c=sentence(e,'estimated difference')
        f=prose_fact(c,e,['ST_SB17_3001'],'efficacy')
        self.assertEqual(f['scope_audit']['requested'][0]['status'],'explicit_candidate')
        self.assertEqual(condition(f,'endpoint','eq',['PASI'])['status'],'match')
        self.assertEqual(condition(f,'timepoint','eq',['Week 9'])['status'],'match')
        self.assertEqual(condition(f,'analysis_role','eq',['primary'])['status'],'match')
        for stat in ('difference','CI90'):self.assertEqual(condition(f,'statistic','eq',[stat])['status'],'match')
        for atom in f['observations']:
            if atom.get('source_range'):
                start,end=atom['source_range'];self.assertEqual(e['E1']['text'][start:end],atom['quote'])

    def test_criterion_inherits_only_trial_not_the_result_conditions(self):
        e=evidence(DEFINITION+' '+RESULT+' '+MARGIN)
        f=prose_fact(sentence(e,'similarity margin'),e,['ST_SB17_3001'],'efficacy')
        self.assertEqual(f['scope_audit']['requested'][0]['status'],'explicit_candidate')
        self.assertFalse(any(a['dimension'] in ('endpoint','timepoint','analysis_role') for a in f['observations']))
        self.assertEqual(condition(f,'attribute','eq',['margin'])['status'],'match')

    def test_paragraph_gaps_and_tables_break_trial_inheritance(self):
        for boundary in ('\n\n','\n[UNSUPPLIED SOURCE GAP]\n','<table><tr><td>Unrelated</td></tr></table>'):
            e=evidence(DEFINITION+boundary+RESULT)
            start=e['E1']['text'].index(RESULT)
            c={'kind':'source_paragraph','evidence':'E1','value':RESULT,'quote':RESULT,
               'start':start,'end':start+len(RESULT),'quote_start':start,'quote_end':start+len(RESULT)}
            f=prose_fact(c,e,['ST_SB17_3001'],'efficacy')
            self.assertEqual(f['scope_audit']['requested'][0]['status'],'unknown')

    def test_multiple_trials_or_a_part_branch_are_not_single_trial_proof(self):
        for prefix in ('Study SB17-1001 was described. '+DEFINITION,DEFINITION+' Part 2 was also evaluated.'):
            e=evidence(prefix+' '+RESULT)
            f=prose_fact(sentence(e,'estimated difference'),e,['ST_SB17_3001'],'efficacy')
            self.assertEqual(f['scope_audit']['requested'][0]['status'],'unknown')

    def test_different_own_endpoint_or_visit_is_never_overwritten(self):
        e=evidence(DEFINITION+' '+RESULT.replace('primary targeted estimand','primary targeted estimand for Cmax at Week 7'))
        f=prose_fact(sentence(e,'estimated difference'),e,['ST_SB17_3001'],'efficacy')
        self.assertEqual(condition(f,'endpoint','eq',['PASI'])['status'],'mismatch')
        self.assertEqual(condition(f,'timepoint','eq',['Week 9'])['status'],'mismatch')

    def test_measure_bundle_requires_one_complete_assertion(self):
        self.assertEqual(measure_bundle(RESULT)['roles'],['difference','CI90'])
        self.assertIsNone(measure_bundle('A difference was discussed with a 90% CI.'))
        self.assertIsNone(measure_bundle(RESULT+' '+RESULT.replace('-1.20','-0.90')))
        self.assertIsNone(measure_bundle(RESULT.replace('-4.20%, 1.80%','-4.20%')))

    def test_text_location_is_required_for_antecedent_links(self):
        e=evidence(DEFINITION+' '+RESULT)
        c=sentence(e,'estimated difference')
        c={**c,'quote':c['quote']+' fabricated suffix'}
        self.assertIsNone(linked(c,e['E1'],['ST_SB17_3001']))

if __name__=='__main__':unittest.main()
