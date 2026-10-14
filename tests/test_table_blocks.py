from copy import deepcopy
import unittest
from rag.grounded_table_graph import build,validate_graph,selected_fact
from rag.source_selection import catalog
from rag.contract_relations import table_fact
from rag.evidence_slices import table_ranges
from rag.preprocessed_chunks import table_parts

HTML='<table><tr><th>Parameter</th><th>Arm A</th></tr><tr><td>Tmax (days)</td><td></td></tr><tr><td>Mean (SD)</td><td>7.14 (2.218)</td></tr><tr><td>Median</td><td>7.0</td></tr><tr><td>CL/F (L/day)</td><td></td></tr><tr><td>Mean (SD)</td><td>0.617 (0.131)</td></tr></table>'

class RowBlockTests(unittest.TestCase):
    def test_own_block_endpoint_linked_and_next_heading_closes_inheritance(self):
        evidence={'E1':{'text':HTML}};graph=build(evidence['E1']);self.assertTrue(validate_graph(graph,HTML))
        candidates=catalog(evidence)
        t=next(c for c in candidates.values() if c['kind']=='table_cell' and c['value']=='7.14 (2.218)')
        f=table_fact(t,evidence,graph=graph)
        self.assertIn(('endpoint','tmax'),[(a['dimension'],a['value']) for a in f['observations']])
        self.assertTrue(any(candidates[i]['value']=='Tmax (days)' for i in t['context_ids']))
        c=next(c for c in candidates.values() if c['kind']=='table_cell' and c['value']=='0.617 (0.131)')
        f=table_fact(c,evidence,graph=graph)
        self.assertNotIn(('endpoint','tmax'),[(a['dimension'],a['value']) for a in f['observations']])
        self.assertIn(('endpoint','CL/F'),[(a['dimension'],a['value']) for a in f['observations']])
    def test_forged_cross_block_relation_fails_graph_validation(self):
        graph=build({'text':HTML});table=graph['tables'][0]
        head=next(c for c in table['cells'] if c['value']=='Tmax (days)')
        value=next(c for c in table['cells'] if c['value']=='0.617 (0.131)')
        fact=next(f for f in table['facts'] if f['value_node']==value['id'])
        fact['relations'].append({'kind':'row_block_context','node_id':head['id'],'status':'structural_candidate'})
        with self.assertRaisesRegex(ValueError,'locality'):validate_graph(graph,HTML)
    def test_budget_slices_keep_subordinate_rows_with_standalone_heading(self):
        pieces=table_ranges(HTML)
        piece=next(HTML[a:b] for a,b,required in pieces if '0.617' in HTML[a:b])
        self.assertIn('CL/F (L/day)',piece)
    def test_large_td_header_table_repeats_arm_headers_after_split(self):
        import sys
        from pathlib import Path
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        from test_chunking import CharacterCounter
        html=HTML.replace('<th>','<td>').replace('</th>','</td>')
        parts=table_parts(html,CharacterCounter(),230,'',8192)
        self.assertGreater(len(parts),1)
        for p in parts:self.assertIn('Arm A',''.join(f.text for f in p.fragments))
