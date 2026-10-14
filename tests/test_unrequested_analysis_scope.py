import unittest
from rag.contract_match import match_request
from rag.contract_terms import atoms
from rag.literal_requests import compile_requests


class UnrequestedAnalysisScopeTests(unittest.TestCase):
    def checked(self,question,observations=(),relations=()):
        c=compile_requests(question)
        c['requests'][0]['normalization_status']='grounded'
        f={'relationship_status':'structural_candidate','value_binding_status':'whole_cell',
           'observations':[{'dimension':'endpoint','value':'Cmax'},{'dimension':'statistic','value':'GMR'},
               *({'dimension':d,'value':v} for d,v in observations)],'explicit_relations':list(relations)}
        return match_request(c,c['requests'][0],f)

    def test_unrequested_ada_population_and_protein_adjustment_are_not_overall(self):
        for d,v in [('analysis_set','ADA_negative'),('analysis_set','ADA_positive'),('covariate','protein_adjusted')]:
            self.assertEqual('mismatch',self.checked('Cmax GMR',[(d,v)])['status'])
        for q,d,v in [('ADA 음성 Cmax GMR','analysis_set','ADA_negative'),
            ('ADA 양성 Cmax GMR','analysis_set','ADA_positive'),('단백질 보정 Cmax GMR','covariate','protein_adjusted')]:
            self.assertEqual('match',self.checked(q,[(d,v)])['status'])

    def test_weight_stratum_is_not_a_baseline_weight_covariate(self):
        relation={'dimension':'weight','values':['51','83'],'unit':'kg'}
        self.assertEqual('mismatch',self.checked('Cmax GMR',relations=[relation])['status'])
        self.assertEqual('match',self.checked('체중 51~83 kg Cmax GMR',relations=[relation])['status'])
        self.assertEqual('match',self.checked('Cmax GMR',[('covariate','body_weight'),('analysis_model','ANCOVA')])['status'])

    def test_secondary_analysis_is_not_an_unqualified_result(self):
        self.assertEqual('mismatch',self.checked('Cmax GMR',[('analysis_timing','post_hoc')])['status'])
        self.assertEqual('match',self.checked('사후 Cmax GMR',[('analysis_timing','post_hoc')])['status'])
        self.assertEqual('unknown',self.checked('사후 Cmax GMR',[('analysis_timing','ad_hoc')])['status'])

    def test_unknown_requested_conditions_do_not_become_mismatch(self):
        self.assertEqual('unknown',self.checked('ADA 음성 Cmax GMR')['status'])

    def test_incidence_rate_is_not_an_ada_population_request(self):
        self.assertFalse(any(a['dimension']=='analysis_set' for a in atoms('ADA 양성률')))


if __name__=='__main__':unittest.main()
