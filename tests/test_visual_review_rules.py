import copy
import tempfile
import unittest
from pathlib import Path
import fitz
from preprocess.table_quality import inspect_table
from preprocess.visual_groups import build_visual_groups
from preprocess.ingest import from_inputs
from preprocess.native_visual_context import recover
from tests.test_visual_groups import asset
from rag.retrieval.search import filter_idx, Bm25, dense_hits
import numpy as np


class ReviewRuleTests(unittest.TestCase):
    def test_missing_treatment_header_is_detected_with_identical_numbers(self):
        html='<table><tr><td>Placebo</td><td>45 mg</td></tr><tr><td>10</td><td>20</td></tr></table>'
        q=inspect_table(html,'Ustekinumab Placebo 45 mg 10 20',True)
        self.assertFalse(q['missing_numbers'])
        self.assertIn('native_label_tokens_missing',q['issues'])
        self.assertIn('ustekinumab',q['missing_label_tokens'])

    def test_scanned_no_native_reference_does_not_claim_label_loss(self):
        q=inspect_table('<table><tr><td>Header</td></tr><tr><td>10</td></tr></table>')
        self.assertNotIn('native_label_tokens_missing',q['issues'])

    def test_header_only_fragment_continues_and_retains_original_cells(self):
        head='<table><tr><td></td><td colspan="2">Drug</td></tr><tr><td></td><td>45 mg</td><td>90 mg</td></tr></table>'
        body='<table><tr><td></td><td>N=20</td><td>N=30</td></tr><tr><td>Baseline</td><td>3</td><td>4</td></tr><tr><td>Change</td><td>-1</td><td>-2</td></tr></table>'
        rows=[asset('cap','table_title',[80,860,800,880],'Table 7: Endpoints'),
            asset('h','table',[80,880,800,940],'',html=head),
            asset('b','table',[80,60,800,400],'',page=2,html=body)]
        originals=copy.deepcopy(rows)
        groups,_=build_visual_groups(rows)
        g=next(g for g in groups if g['label']=='Table 7')
        self.assertEqual(g['member_asset_ids'],['h','b'])
        self.assertEqual(g['continuation_status'],'data_fragment_continuation_candidate')
        self.assertEqual(g['pdf_pages'],[1,2])
        self.assertEqual(rows[2]['base'],originals[2]['base'])
        rows[2]['bbox_input_px']=[80,400,800,800]
        self.assertNotIn('b',next(g for g in build_visual_groups(rows)[0] if g['label']=='Table 7')['member_asset_ids'])

    def test_side_members_join_centered_caption_without_crossing_other_caption(self):
        rows=[asset('l','chart',[150,120,210,180],'Swab'),asset('m','chart',[300,120,360,180],'Pad'),
            asset('r','chart',[440,120,510,180],'Box'),asset('cap','figure_title',[290,240,350,260],'Figure B'),
            asset('head','paragraph_title',[50,280,800,310],'Step 3'),asset('other','chart',[300,400,550,550],'Device'),
            asset('cap2','figure_title',[300,580,380,600],'Figure C')]
        groups,_=build_visual_groups(rows)
        g=next(g for g in groups if g['label']=='Figure B')
        self.assertEqual(g['member_asset_ids'],['l','m','r'])
        self.assertNotIn('other',g['member_asset_ids'])

    def test_native_legend_recovered_but_next_heading_not_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'doc.pdf'
            with fitz.open() as pdf:
                p=pdf.new_page(width=400,height=500)
                p.insert_text((100,260),'Placebo (N=20)')
                p.insert_text((100,285),'Drug (N=30)')
                p.insert_text((100,340),'Next section')
                pdf.save(path)
            doc=from_inputs([path],Path(tmp)/'out')[0]
            rows=[asset('f','chart',[80,80,280,230],'',doc=doc.document_id),
                asset('heading','paragraph_title',[90,325,300,345],'Next section',doc=doc.document_id)]
            with fitz.open(path) as pdf:
                found=recover(doc,pdf[0],rows,'page.png',[400,500])
            self.assertEqual(len(found),2)
            self.assertTrue(all(a['native_figure_owner_id']=='f' for a in found))
            self.assertFalse(any(a['numeric_evidence_eligible'] for a in found))


class SearchPolicyTests(unittest.TestCase):
    def test_default_and_explicit_scopes_filter_bm25_and_dense_consistently(self):
        base={'product_id':'BMAB1200','doc_group':'label','jurisdiction':'US','header':'Drug','text':'dose 45 mg'}
        chunks=[dict(base),dict(base,document_metadata={'default_search':False,'evaluation_cohort':'development'}),
            dict(base,document_metadata={'default_search':True,'evaluation_cohort':'product_test_locked'}),
            dict(base,representation={'retrieval_eligible':False}),
            dict(base,document_metadata={'default_search':'false'})]
        idx=filter_idx(chunks)
        self.assertEqual(idx.tolist(),[0])
        self.assertEqual([i for _,i in Bm25(chunks).hits('dose',idx)],[0])
        self.assertEqual([i for _,i in dense_hits(np.ones(2),np.ones((5,2)),idx)],[0])
        self.assertEqual(filter_idx(chunks,include_nondefault=True).tolist(),[0,1])
        self.assertEqual(filter_idx(chunks,include_nondefault=True,include_evaluation=True).tolist(),[0,1,2])


if __name__=='__main__':unittest.main()
