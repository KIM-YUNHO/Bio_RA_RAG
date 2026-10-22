from copy import deepcopy
import unittest
from preprocess.tables.projection_grid import reconstruct
from preprocess.tables.table_quality import inspect_table

def source():
    rows=[('Arm','Value'),('Group A','31'),('Group B','47')];tokens=[]
    for r,texts in enumerate(rows):
        for c,text in enumerate(texts):tokens.append({'text':text,'confidence':.99,'bbox':[c*100+5,r*55+5,c*100+85,r*55+35]})
    # Wrapped material overlaps the first line of its own physical row.
    tokens.append({'text':'(2.7)','confidence':.98,'bbox':[105,85,185,103]})
    return {'image_size':[200,165],'tokens':tokens}

class ProjectionTests(unittest.TestCase):
    def test_wrapped_source_cell_keeps_own_column_and_row(self):
        p=reconstruct(source());self.assertIsNotNone(p)
        self.assertEqual(p['logical_grid']['column_count'],2);self.assertEqual(len(p['logical_grid']['rows']),3)
        self.assertEqual(p['logical_grid']['rows'][1]['cells'][1]['text'],'31\n(2.7)')
        self.assertEqual(inspect_table(p['html'])['issues'],[]);self.assertFalse(p['numeric_evidence_eligible'])
        self.assertEqual(sum(len(c['ocr_token_indices']) for c in p['physical_cells']),7)
    def test_incomplete_grid_overlapping_heading_or_low_confidence_stays_unresolved(self):
        x=source();x['tokens'].pop(4);self.assertIsNone(reconstruct(x))
        x=source();x['tokens'][0]['bbox'][2]=190;self.assertIsNone(reconstruct(x))
        x=source();x['tokens'][2]['confidence']=.79;self.assertIsNone(reconstruct(x))
