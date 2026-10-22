import unittest
from rag.retrieval.clinical_retrieval import cues,rank
from rag.evidence.answer_conditions import missing,unresolved_scopes,explicit_conflicts

class ClinicalCues(unittest.TestCase):
    def chunk(self,text,path=()):return {'text':text,'structure':{'caption':'','section_path':list(path)}}

    def test_dose_cue_requires_printed_amount_and_administration(self):
        self.assertTrue(cues('TX-12 dose and route',self.chunk('Subjects received a single dose of 30 mg by SC injection.',['Treatments'])))
        self.assertFalse(cues('TX-12 dose and route',self.chunk('A single dose was studied.',['Treatments'])))

    def test_primary_and_covariate_analyses_remain_distinct(self):
        adjusted=self.chunk('Primary Pharmacokinetic Parameters with Weight as a Covariate. ANCOVA ad-hoc analysis. Ratio 1.12')
        primary=self.chunk('Statistical Comparison of Primary Pharmacokinetic Parameters. Geometric ratio 1.04')
        self.assertFalse(cues('GMR primary results',adjusted))
        self.assertTrue(cues('GMR primary results',primary))
        self.assertTrue(cues('GMR ANCOVA weight covariate',adjusted))
        self.assertFalse(cues('GMR ANCOVA weight covariate',primary))

    def test_missing_dose_and_unqualified_analysis_are_withheld(self):
        item={'conditions':[{'dimension':'analysis','quote':'Pharmacokinetic analysis set'}]}
        absent=missing([item],'90 mg Cmax GMR',{'english_query':'','studies':[]})
        self.assertEqual(set(absent[0]['dimensions']),{'dose','endpoint','comparison','analysis'})
        self.assertTrue(unresolved_scopes(absent))
        self.assertFalse(unresolved_scopes([{'item_index':0,'dimensions':['route']}]))

    def test_unspecified_gmr_does_not_accept_posthoc_as_primary(self):
        item={'conditions':[{'dimension':'analysis','quote':'Post-hoc weight-adjusted analysis','evidence':'E1'}]}
        self.assertTrue(explicit_conflicts([item],'GMR and confidence interval',{'english_query':''}))
        self.assertFalse(explicit_conflicts([item],'Post-hoc ANCOVA GMR',{'english_query':''}))

    def test_pk_median_query_does_not_boost_label_dosage(self):
        query='Cmax median mean after single 30 mg subcutaneous dose'
        label=self.chunk('Recommended dosage: administer 30 mg by subcutaneous injection.',['Dosage'])
        self.assertFalse(cues(query,label,topic='pk_results'))
        table=self.chunk('<table><tr><td>Cmax</td><td>Median</td><td>Mean</td></tr></table>')
        table['structure']['kind']='table_candidate'
        self.assertTrue(cues(query,table,topic='pk_results'))

    def test_printed_other_study_does_not_receive_requested_dose_boost(self):
        source=self.chunk('Study SB17-3001. Subjects received 45 mg by SC injection.',['Treatments'])
        source['product_id']='SB17';source['structure']['heading_studies']=['ST_SB17_1001']
        self.assertFalse(cues('SB17-1001 dose and route',source,['ST_SB17_1001'],'dosing'))

    def test_unrequested_antibody_subgroup_is_not_boosted_as_overall_primary_result(self):
        source=self.chunk('Primary Pharmacokinetic Parameters by Post-dose Anti-drug Antibody. ADA Status. Ratio 1.04')
        self.assertFalse(cues('GMR primary results',source))
        self.assertTrue(cues('GMR by ADA status',source))

if __name__=='__main__':unittest.main()
