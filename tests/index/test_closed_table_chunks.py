import unittest
from tests.index.test_chunking import CharacterCounter
from rag.index.preprocessed_chunks import table_parts,DeferredTable
from preprocess.tables.table_quality import inspect_table

class ClosedTableChunkTests(unittest.TestCase):
    def test_spanned_blocks_keep_literal_headers_and_source_offsets(self):
        header='<table><tr><th>Endpoint</th><th colspan="2">Arms</th></tr>'
        blocks=['<tr><td rowspan="2">'+name+'</td><td>'+str(v)+'</td><td>'+str(v+1)+'</td></tr><tr><td>'+str(v+2)+'</td><td>'+str(v+3)+'</td></tr>'
            for name,v in [('Alpha',17),('Beta',37),('Gamma',57)]]
        html=header+''.join(blocks)+'</table>'
        parts=table_parts(html,CharacterCounter(),220,'',8192)
        self.assertEqual(len(parts),3)
        for i,p in enumerate(parts):
            text=''.join(f.text for f in p.fragments)
            self.assertEqual(inspect_table(text)['issues'],[])
            self.assertIn(blocks[i],text);self.assertEqual(p.reason,'table_closed_rows')
            for f in p.fragments:self.assertEqual(html[f.start:f.end],f.text)
    def test_header_body_span_and_over_budget_block_defer(self):
        html='<table><tr><th rowspan="2">E</th><th>Arm</th></tr><tr><td>17</td></tr><tr><td>Beta</td><td>19</td></tr></table>'
        with self.assertRaisesRegex(DeferredTable,'header_crosses_body'):table_parts(html,CharacterCounter(),40,'',8192)
        html='<table><tr><th>E</th><th>Arm</th></tr><tr><td rowspan="2">Alpha</td><td>'+('7'*140)+'</td></tr><tr><td>19</td></tr></table>'
        with self.assertRaisesRegex(DeferredTable,'merged'):table_parts(html,CharacterCounter(),100,'',8192)
