import unittest
from rag.ra_statistical_tables import requested,signature,hits

MEAN_SD={('cmax','mean'),('cmax','sd')}


def stat_table(statistic,endpoint='Cmax (ng/mL)'):
    return ('<table><tr><th>Parameter</th><th>Statistic</th><th>Arm A</th><th>Arm B</th></tr>'
            '<tr><td rowspan="2">'+endpoint+'</td><td>'+statistic+'</td><td>17</td><td>23</td></tr>'
            '<tr><td>SD</td><td>2</td><td>3</td></tr></table>')


class StatisticalTableRetrievalTests(unittest.TestCase):
    def table(self,statistic):
        return stat_table(statistic)

    def test_distinction_sentence_does_not_change_requested_statistic(self):
        self.assertEqual(requested('Cmax 산술평균과 표준편차를 정리해줘. 중앙값이나 기하평균과 구분해줘.'),
                         {('cmax','mean'),('cmax','sd')})
        self.assertEqual(requested('Tmax 중앙값·범위와 t1/2 평균·표준편차를 확인해줘.'),
                         {('tmax','median'),('tmax','range'),('t1/2','mean'),('t1/2','sd')})
        self.assertEqual(requested('Cmax 산술평균과 표준편차를 정리하고, 중앙값이나 기하평균과 구분해줘.'),
                         {('cmax','mean'),('cmax','sd')})
        self.assertEqual(requested('Cmax 중앙값을 확인하고, 산술평균과 구분해줘.'),{('cmax','median')})

    def test_header_row_bindings_distinguish_arithmetic_from_geometric_mean(self):
        arithmetic=signature(self.table('Mean'))
        geometric=signature(self.table('Geometric Mean'))
        self.assertIn(('cmax','mean'),arithmetic);self.assertIn(('cmax','sd'),arithmetic)
        self.assertNotIn(('cmax','mean'),geometric)
        self.assertIn(('cmax','geometric_mean'),geometric)
        self.assertNotIn(('cmax','mean'),signature(self.table('Geometric Least Square Mean')))

    def test_only_scope_filtered_closed_tables_can_add_candidates(self):
        base={'document_id':'d','version_id':'v','product_id':'SB17','structure':{'kind':'table_candidate'}}
        chunks=[{**base,'text':self.table('Mean')},{**base,'text':self.table('Geometric Mean')},
                {**base,'text':'Cmax arithmetic Mean SD footnote only.'}]
        request='Cmax 산술평균과 표준편차'
        self.assertEqual(hits(request,[0,1,2],chunks),[(2.0,0)])
        self.assertEqual(hits(request,[1,2],chunks),[])

    def test_other_literal_trial_cannot_supply_matching_numeric_structure(self):
        chunks=[{'product_id':'SB17','document_id':'d','version_id':'v',
                 'structure':{'kind':'table_candidate'},'text':'Study SB17-3001 '+self.table('Mean')}]
        self.assertEqual(hits('SB17-1001 Cmax 산술평균과 표준편차',[0],chunks),[])

    def test_header_match_does_not_override_complex_analysis_or_part_requirements(self):
        self.assertFalse(requested('Part 2 Cmax 평균·표준편차'))
        self.assertFalse(requested('Cmax 보정 평균과 90% 신뢰구간'))
        self.assertFalse(requested('Cmax 평균과 SD를 정리해줘. 하위군 결과도 함께.'))


class RequestWordingTests(unittest.TestCase):
    """The same request in other wordings must give the same retrieval target."""

    def assertAll(self,expected,questions):
        for question in questions:
            with self.subTest(question=question):
                self.assertEqual(requested(question),expected)

    def test_sentence_split_order_and_language_do_not_change_the_target(self):
        self.assertAll(MEAN_SD,[
            'SB17-1001의 Cmax 산술평균과 표준편차를 정리해줘.',
            'SB17-1001의 Cmax를 확인해줘. 산술평균과 표준편차도 알려줘.',
            '산술평균과 표준편차를 알려줘. 대상은 SB17-1001의 Cmax야.',
            'SB17-1001에서 산술평균과 표준편차로 Cmax를 정리해줘.',
            'Mean and SD of Cmax in SB17-1001',
            'SB17-1001 Cmax 평균(SD)',
            'SB17-1001의 최고혈중농도 평균과 표준편차',
            'SB17-1001 C$_{max}$ mean and standard deviation'])

    def test_contrast_markers_exclude_only_the_contrasted_statistic(self):
        self.assertAll(MEAN_SD,[
            'Cmax의 기하평균이 아닌 산술평균과 SD',
            'Cmax 평균과 SD를 알려줘. 중앙값은 제외해줘.',
            'Cmax 평균과 SD. 기하평균비(GMR)와 구분해줘.',
            'Report the arithmetic mean and SD of Cmax, not the geometric mean.',
            'Cmax mean and standard deviation rather than median or geometric mean'])
        self.assertEqual(requested('Cmax 중앙값 말고 산술평균을 알려줘'),{('cmax','mean')})
        # "구분해서" without a contrast particle asks for both, side by side.
        self.assertEqual(requested('Cmax 중앙값과 평균을 구분해서 알려줘'),{('cmax','median'),('cmax','mean')})

    def test_statistics_after_an_endpoint_list_apply_to_every_listed_endpoint(self):
        both=MEAN_SD|{('aucinf','mean'),('aucinf','sd')}
        self.assertAll(both,['Cmax, AUCinf 산술평균과 표준편차','Cmax와 AUCinf의 평균·SD',
                             'Cmax(ng/mL)와 AUCinf(ng·h/mL)의 평균과 표준편차','Mean and SD of Cmax and AUCinf'])

    def test_each_statistic_binds_to_its_own_endpoint_in_either_word_order(self):
        both={('tmax','median'),('tmax','range'),('t1/2','mean'),('t1/2','sd')}
        self.assertAll(both,[
            'Tmax 중앙값·범위와 t1/2 평균·표준편차',
            't1/2 평균·표준편차와 Tmax 중앙값·범위',
            'Tmax와 반감기를 확인해줘. Tmax는 중앙값·범위, 반감기는 평균·표준편차로.',
            'the median and range of Tmax and the mean and SD of terminal half-life',
            'Tmax median and range, and t1/2 mean and SD'])

    def test_printed_endpoint_variants_share_one_key(self):
        auc={('aucinf','mean'),('aucinf','sd')}
        self.assertAll(auc,['AUC0-inf 평균과 표준편차','AUC0–∞ 평균과 표준편차','AUC_{0-inf} 평균과 표준편차'])
        half={('t1/2','mean'),('t1/2','sd')}
        self.assertAll(half,['t½ 평균과 표준편차','반감기 평균과 표준편차','terminal half-life mean and SD'])
        self.assertEqual(requested('최고 혈중 농도 도달 시간의 중앙값과 범위'),{('tmax','median'),('tmax','range')})
        self.assertEqual(requested('CL/F 평균과 표준편차'),{('cl/f','mean'),('cl/f','sd')})
        # Steady-state and dose-normalized parameters are not the single-dose endpoint.
        self.assertEqual(requested('Cmax,ss 평균과 SD'),{('cmax,ss','mean'),('cmax,ss','sd')})
        self.assertEqual(requested('AUCinf/D 평균'),{('aucinf/d','mean')})

    def test_scope_word_range_is_not_a_statistic(self):
        self.assertEqual(requested('SB17-1001 Cmax 평균과 SD를 EMA 평가 범위에서 알려줘'),MEAN_SD)
        self.assertEqual(requested('Cmax 평균과 SD over the study duration'),MEAN_SD)


class PrintedHeaderTests(unittest.TestCase):
    def table(self,endpoint,statistic='Mean'):
        return stat_table(statistic,endpoint)

    def test_header_spelling_and_unparsed_units_do_not_hide_a_statistic_table(self):
        for endpoint,key in [('C $ max $ (ng/mL)','cmax'),('C max (ng/mL)','cmax'),
                             ('AUC0-inf (ng·h/mL)','aucinf'),('AUC0–∞ (μg·h/mL)','aucinf'),
                             ('AUC $ inf $ (hr*ng/mL)','aucinf'),('t½ (hr)','t1/2'),('Half-life (h)','t1/2')]:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(signature(self.table(endpoint)),{(key,'mean'),(key,'sd')})

    def test_min_max_rows_are_a_range_and_endpoint_names_are_not(self):
        table=('<table><tr><th>Parameter</th><th>Statistic</th><th>A</th></tr>'
               '<tr><td rowspan="3">C max (ng/mL)</td><td>Median</td><td>17</td></tr>'
               '<tr><td>Min</td><td>2</td></tr><tr><td>Max</td><td>30</td></tr></table>')
        self.assertEqual(signature(table),{('cmax','median'),('cmax','range')})
        self.assertEqual(signature(self.table('C max (ng/mL)','n')),{('cmax','sd')})


class TableChoiceTests(unittest.TestCase):
    def chunk(self,text,caption=''):
        return {'document_id':'d','version_id':'v','product_id':'SB17','structure':{'kind':'table_candidate','caption':caption},
                'text':(caption+'\n' if caption else '')+text}

    def mean_table(self,endpoint='Cmax (ng/mL)'):
        return stat_table('Mean',endpoint)

    def test_every_scoped_table_is_compared_before_choosing(self):
        chunks=[self.chunk(self.mean_table(),'Table 3. Cmax by visit') for _ in range(7)]
        score={6:0.9,2:0.8}
        found=hits('Cmax 산술평균과 표준편차',range(7),chunks,limit=3,relevance=lambda i:score.get(i,0.0))
        self.assertEqual([i for _,i in found],[6,2,0])

    def test_unrequested_subgroup_or_conflicting_qualifier_is_ranked_after_the_plain_table(self):
        chunks=[self.chunk(self.mean_table(),'Table 9. Cmax in ADA-positive subjects'),
                self.chunk(self.mean_table(),'Table 8. Summary of PK parameters (PK analysis set)'),
                self.chunk(self.mean_table(),'Table 10. Cmax in ADA-negative subjects')]
        plain=hits('Cmax 산술평균과 표준편차',[0,1,2],chunks)
        self.assertEqual(plain[0][1],1)
        negative=[i for _,i in hits('ADA 음성 대상자의 Cmax 산술평균과 표준편차',[0,1,2],chunks)]
        self.assertEqual(negative,[2,1])

    def test_table_for_another_population_is_never_promoted(self):
        patients=self.chunk(self.mean_table(),'Table 14. Summary of PK parameters in adult patients with psoriasis')
        self.assertEqual(hits('건강인 시험의 Cmax 산술평균과 표준편차',[0],[patients]),[])
        self.assertEqual([i for _,i in hits('Cmax 산술평균과 표준편차',[0],[patients])],[0])

    def test_model_adjusted_ls_geometric_mean_is_not_the_descriptive_geometric_mean(self):
        ls=self.chunk(stat_table('Geometric Least Square Mean'))
        self.assertEqual(hits('Cmax 기하평균',[0],[ls]),[])
        self.assertEqual([i for _,i in hits('Cmax LS geometric mean',[0],[ls])],[0])
        self.assertEqual(requested('Cmax 기하 최소제곱평균'),{('cmax','geometric_ls_mean')})

    def test_endpoints_printed_in_separate_tables_are_both_retrieved(self):
        chunks=[self.chunk(self.mean_table('Cmax (ng/mL)')),self.chunk(self.mean_table('Cmax (ng/mL)')),
                self.chunk(self.mean_table('AUCinf (ng·h/mL)'))]
        found=hits('Cmax와 AUCinf의 산술평균과 표준편차',[0,1,2],chunks,limit=2)
        self.assertEqual(sorted(i for _,i in found),[0,2])

    def test_partial_statistics_for_an_endpoint_do_not_count(self):
        sd_only=('<table><tr><th>Parameter</th><th>Statistic</th><th>A</th></tr>'
                 '<tr><td>Cmax (ng/mL)</td><td>SD</td><td>2</td></tr></table>')
        self.assertEqual(hits('Cmax 산술평균과 표준편차',[0],[self.chunk(sd_only)]),[])


if __name__=='__main__':unittest.main()
