import unittest
from rag import selection_candidate as s
from rag.literal_source_slots import unique


class LiteralSourceSlotsTests(unittest.TestCase):
    def evidence(self,values=('1.31 (1.07, 1.62)','0.76 (0.62, 0.91)'),heading='Ratio of geometric means (90% CI)',study='SB17-1001',caption=True):
        title='Table 47 Statistical assessment of PK parameters (ANOVA)\n' if caption else ''
        text=title+'<table><tr><th>Endpoint</th><th>'+heading+' 1</th></tr>'
        text+=''.join('<tr><td>'+endpoint+'</td><td>'+v+'</td></tr>' for endpoint,v in zip(('Cmax','AUCinf'),values))
        text+='</table>\n1 Ratio of geometric means SB17/EU-Stelara'
        return {'E1':{'text':text,'product_id':'SB17','document_id':'synthetic','version_id':'one','structure':{'section_id':'own'}},
                'E2':{'text':'Study '+study,'product_id':'SB17','document_id':'synthetic','version_id':'one','structure':{'section_id':'own'}}}

    def select(self,e,q='SB17-1001 시험에서 EU Stelara 대비 Cmax, AUCinf GMR와 90% CI는?',studies=None):
        u={'studies':['ST_SB17_1001'] if studies is None else studies,'topic':'gmr'}
        c=s.catalog(e,q)
        return unique(c,e,q,u),c,q,u

    def test_all_unique_whole_cells_cover_fields_once_without_rewriting_values(self):
        e=self.evidence();r,c,q,u=self.select(e)
        self.assertIsNotNone(r)
        self.assertEqual(4,len(r['_proof']['request_source_ids']))
        self.assertEqual(2,len(r['items']))
        actual=s.resolve({k:v for k,v in r.items() if k!='_proof'},c,e,q,u)
        self.assertCountEqual(['1.31 (1.07, 1.62)','0.76 (0.62, 0.91)'],[i['value'] for i in actual['items']])
        self.assertTrue(all(i['own_analysis_binding'] for i in actual['items']))
        self.assertEqual(0,r['_proof']['new_model_calls'])

    def test_changed_numbers_follow_source_without_expected_answer_values(self):
        e=self.evidence(('2.73 (2.18, 3.04)','0.51 (0.41, 0.67)'));r,c,_,_=self.select(e)
        self.assertCountEqual(['2.73 (2.18, 3.04)','0.51 (0.41, 0.67)'],[c[i['value_id']]['value'] for i in r['items']])

    def test_ci_only_request_still_requires_own_ratio_analysis_binding(self):
        q='SB17-1001 EU Stelara 대비 Cmax AUCinf 90% CI'
        e=self.evidence();c=s.catalog(e,q);u={'studies':['ST_SB17_1001'],'topic':'pk_results'}
        r=unique(c,e,q,u);self.assertIsNotNone(r)
        e=self.evidence(caption=False);c=s.catalog(e,q)
        self.assertIsNone(unique(c,e,q,u))

    def test_missing_slot_or_level_is_not_automatically_complete(self):
        for e in [self.evidence(('1.31 (1.07, 1.62)',)),self.evidence(heading='Ratio of geometric means (CI)')]:
            self.assertIsNone(self.select(e)[0])

    def test_equal_values_at_different_locations_remain_ambiguous(self):
        e=self.evidence();e['E1']['text']+='\n'+e['E1']['text']
        self.assertIsNone(self.select(e)[0])

    def test_actual_study_link_and_analysis_are_required(self):
        for e in [self.evidence(study='CT-P43 1.2'),self.evidence(caption=False)]:
            self.assertIsNone(self.select(e)[0])
        e=self.evidence();e['E2']['structure']['section_id']='unrelated'
        self.assertIsNone(self.select(e)[0])
        self.assertIsNone(self.select(self.evidence(),studies=['ST_SB17_1001','ST_CTP43_1_2'])[0])

    def test_extra_or_unparsed_requests_cannot_be_completed_from_subset(self):
        e=self.evidence();prefix='SB17-1001 EU Stelara 대비 Cmax AUCinf GMR와 90% CI '
        for q in [prefix+'및 SD',prefix+'단위',prefix+'비흡연자',prefix+'분석군 인원',prefix+'US 군 제외',prefix+'각 하위집단']:
            self.assertIsNone(self.select(e,q)[0])


if __name__=='__main__':unittest.main()
