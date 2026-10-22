import unittest
from copy import deepcopy
from rag.retrieval.search import filter_idx
from rag.index.document_structure import identify, source_segments, VERSION
from rag.retrieval.clinical_retrieval import cues
from preprocess.tables.table_caption_ownership import separate


class ScopeContextUpdate(unittest.TestCase):
    def test_product_information_default_does_not_override_cohort_or_invalid_flags(self):
        base = {'source':'FDA','jurisdiction':'US','product_id':'unseen','doc_group':'label',
                'document_metadata':{'family':'product_information','default_search':False}}
        held = deepcopy(base); held['document_metadata']['evaluation_cohort']='holdout'
        bad = deepcopy(base); bad['document_metadata']['default_search']='false'
        other = deepcopy(base); other['document_metadata']['family']='correspondence'
        self.assertEqual(filter_idx([base,held,bad,other]).tolist(),[0])
        self.assertEqual(filter_idx([base,held,bad,other],include_nondefault=True).tolist(),[0,3])

    def test_population_headings_split_exact_source_but_prose_does_not(self):
        text='Subcutaneous Adult Dosage Regimen\nGive 20 mg.\nSubcutaneous Pediatric Dosage Regimen\nGive 10 mg.'
        ranges=source_segments(text)
        self.assertEqual(''.join(text[a:b] for a,b in ranges),text)
        self.assertEqual(sum(bool(identify(text[a:b].strip(),version=VERSION)) for a,b in ranges),2)
        self.assertIsNone(identify('The adult dose is given once daily.',version=VERSION))
        self.assertIsNone(identify('Subcutaneous Adult Dosage Regimen',version='sections-5'))

    def test_adult_dose_is_preferred_without_boosting_child_or_pk_summary(self):
        def chunk(text,path):return {'text':text,'structure':{'section_path':[path]}}
        adult=chunk('For adults administer 20 mg subcutaneously.','Adult Dosage')
        child=chunk('Pediatric patients receive 10 mg subcutaneously.','Pediatric Dosage')
        self.assertIn('printed_requested_adult_dosage',cues('adult dose and route',adult,topic='dosing'))
        self.assertNotIn('printed_requested_adult_dosage',cues('adult dose and route',child,topic='dosing'))
        self.assertFalse(cues('adult 20 mg Cmax',adult,topic='pk_results'))

    def test_pk_set_count_cue_does_not_prefer_endpoint_table_n_column(self):
        query='Number of subjects in the PK analysis set'
        def table(text):return {'text':text,'structure':{'kind':'table_candidate'}}
        counts=table('Pharmacokinetic analysis set (PKS) Number of subjects: 31, 32, 33')
        results=table('Pharmacokinetic analysis set (PKS) C_{max} N n Estimate 31 30 1.02')
        cue='printed_requested_analysis_set_counts'
        self.assertIn(cue,cues(query,counts,topic='n_subjects'))
        self.assertNotIn(cue,cues(query,results,topic='n_subjects'))
        self.assertIn(cue,cues(query+' for Cmax',results,topic='n_subjects'))

    def test_distinct_header_detached_compatible_fragment_and_cells_preserved(self):
        def asset(uid,top,columns):
            return {'asset_id':uid,'pdf_page':1,'bbox_input_px':[10,top,90,top+10],
                    'input_image_size':[100,100], 'logical_group_id':'g',
                    'selected':{'content':'<table><tr>'+''.join('<td>'+c+'</td>' for c in columns)+'</tr></table>'}}
        first=asset('a',10,['Study ID','Study design','Treatment'])
        same=asset('b',25,['Study ID','Study design','Treatment'])
        other=asset('c',40,['Analysis set','Number of subjects','Population'])
        data=asset('d',55,['Results','12','13'])
        groups=[{'group_id':'g','kind':'table','caption_asset_id':'cap','caption_part_asset_ids':[],
                 'member_asset_ids':['a','b','c','d'],'context_asset_ids':[],'relations':[]}]
        before=[a['selected']['content'] for a in [first,same,other,data]]
        got=separate([first,same,other,data],groups)
        self.assertEqual([r['asset_id'] for r in got],['c'])
        self.assertEqual(groups[0]['member_asset_ids'],['a','b','d'])
        self.assertEqual([a['selected']['content'] for a in [first,same,other,data]],before)
        self.assertEqual(separate([first,same,other,data],groups),[])

    def test_reassembling_table_context_preserves_printed_notes(self):
        from tests.preprocessing.test_visual_groups import asset
        from preprocess.tables.table_context import assemble
        cap=asset('cap','table_title',[10,10,900,30],'Table 2. Outcomes')
        table=asset('body','table',[10,40,900,100],'Endpoint Value',
                    html='<table><tr><td>Endpoint</td><td>Value</td></tr></table>')
        table['selected']['content']=table['base']['content']
        note=asset('note','table_footnote',[10,110,900,130],'Note: source population')
        cap['logical_group_id']=table['logical_group_id']='g'
        group={'group_id':'g','document_id':cap['document_id'],'version_id':cap['version_id'],
               'kind':'table','caption_asset_id':'cap','caption_part_asset_ids':['cap'],
               'member_asset_ids':['body'],'relations':[]}
        rows=[cap,table,note];groups=[group]
        assemble(rows,groups,'test');before=deepcopy(groups)
        assemble(rows,groups,'test')
        self.assertEqual(groups,before)
        self.assertEqual(group['context_roles']['note'],'footnote')


if __name__=='__main__':unittest.main()
