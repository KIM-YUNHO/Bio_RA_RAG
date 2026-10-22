from copy import deepcopy
import unittest
from preprocess.native_table_fragments import fragment,eligible,binding,VERSION,ORIGIN
from preprocess.ingest import stable_hash
from preprocess.table_representation import render_structured_table
from preprocess.table_quality import inspect_table

def cell(col,text,span=1):return {'column_index':col,'row_span':span,'col_span':1,'text':text,'state':'visible'}

def grid():
    rows=[{'row_index':0,'section':'body','cells':[cell(0,'Endpoint'),cell(1,'Statistic'),cell(2,'Arm A')]}]
    for label,vals in [('Alpha',['17','21']),('Beta',['37','41'])]:
        i=len(rows)
        rows.extend([{'row_index':i,'section':'body','cells':[cell(0,label,2),cell(1,'Mean'),cell(2,vals[0])]},
            {'row_index':i+1,'section':'body','cells':[cell(1,'Median'),cell(2,vals[1])]}])
    rows.append({'row_index':5,'section':'body','cells':[cell(0,'Unclosed'),cell(1,'n'),cell(2,'13')]})
    g={'column_count':3,'rows':rows,'caption':None,'footnotes':[],'uncertainties':[]}
    html,issues=render_structured_table(g);assert not issues
    return {'geometry_status':'source_borders_and_glyph_coverage_checked','logical_grid':g,'html':html,'quality':inspect_table(html)}

class NativeFragmentTests(unittest.TestCase):
    def test_complete_physical_groups_retained_boundary_excluded(self):
        p=fragment(grid());self.assertEqual(p['closed_source_row_groups'],[[1,3],[3,5]])
        self.assertEqual(p['excluded_source_rows'],[5]);self.assertNotIn('Unclosed',p['html'])
        self.assertIn('<th>Arm A</th>',p['html']);self.assertEqual(p['cross_page_relationship'],'unresolved')
        self.assertFalse(p['numeric_evidence_eligible']);self.assertFalse(p['semantic_approval'])
    def test_spanning_header_or_unverified_geometry_cannot_supply_fragment(self):
        g=grid();g['logical_grid']['rows'][0]['cells'][0]['row_span']=3
        self.assertIsNone(fragment(g))
        g=grid();g['geometry_status']='model_inferred';self.assertIsNone(fragment(g))
    def test_source_identity_and_derived_representation_are_bound(self):
        a={k:k for k in ('asset_id','document_id','version_id','pdf_page','source_refs','bbox_pdf','bbox_input_px','input_image_size','base')}
        g=grid();p=fragment(g);a['selected']={'origin':ORIGIN,'content':p['html']}
        a['native_table_fragment']={'version':VERSION,'source_binding_sha256':stable_hash(binding(a)),'grid':g,'fragment':p}
        self.assertTrue(eligible(a));b=deepcopy(a);b['version_id']='other';self.assertFalse(eligible(b))
        b=deepcopy(a);b['selected']['content']=b['selected']['content'].replace('17','170');self.assertFalse(eligible(b))
