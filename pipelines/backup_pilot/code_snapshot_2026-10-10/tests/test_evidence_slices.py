import unittest
from rag.evidence_slices import view, GAP


class Counter:
    def one(self,text):return len(text)


class Slices(unittest.TestCase):
    def chunk(self,text,kind='text'):
        unit = {'text':text,'unit_id':'u','kind':kind,'source_refs':[{'pdf_page':45}]}
        chunk = {'chunk_id':'c','text':text,'provenance':{'source_spans':[{'unit_id':'u',
            'representation_start':0,'representation_end':len(text),'separator_before':'','role':'content'}]},
            'representation':{'unit_ids':['u'],'origins':{'u':'native_text_layer'}}}
        return chunk, {'u':unit}

    def test_exact_matching_sentence_survives_with_source_offsets(self):
        text = 'Unrelated background.\nAUCinf ratio was 0.99.\nOther background.'
        c, units = self.chunk(text)
        got = view(c,units,'AUCinf ratio',25,Counter())
        self.assertEqual(got['text'],'AUCinf ratio was 0.99.\n')
        span=got['provenance']['source_spans'][0]
        self.assertEqual(text[span['representation_start']:span['representation_end']],got['text'])

    def test_table_header_and_whole_target_row_are_preserved(self):
        text='<table><tr><th>Endpoint</th><th>SB17/EU</th></tr><tr><td>Cmax</td><td>0.90</td></tr><tr><td>AUCinf</td><td>0.99</td></tr></table>'
        c, units = self.chunk(text,'table')
        got = view(c,units,'AUCinf',len(text)-1,Counter())
        self.assertIn('<th>SB17/EU</th>',got['text'])
        self.assertIn('<tr><td>AUCinf</td><td>0.99</td></tr>',got['text'])
        self.assertNotIn('<td>Cmax</td>',got['text'])
        self.assertIn(GAP,got['text'])

    def test_merged_table_cannot_be_partially_selected(self):
        c, units = self.chunk('<table><tr><td rowspan="2">Arm</td><td>1</td></tr><tr><td>2</td></tr></table>','table')
        self.assertIsNone(view(c,units,'Arm',30,Counter()))

    def test_merged_heading_preserves_both_source_rows_and_the_complete_endpoint_row(self):
        text=('<table><tr><td>Dose</td><td colspan="2">45 mg</td></tr>'
            '<tr><td>Parameter</td><td>Median</td><td>Mean</td></tr>'
            '<tr><td>Cmax</td><td>2.4</td><td>2.7</td></tr>'
            '<tr><td>Other</td><td>12345</td><td>67890</td></tr></table>')
        c,units=self.chunk(text,'table')
        got=view(c,units,'Cmax median mean',len(text)-20,Counter())
        self.assertIn('<td colspan="2">45 mg</td>',got['text'])
        self.assertIn('<td>Median</td><td>Mean</td>',got['text'])
        self.assertIn('<tr><td>Cmax</td><td>2.4</td><td>2.7</td></tr>',got['text'])
        self.assertNotIn('<td>Other</td>',got['text'])


if __name__ == '__main__': unittest.main()
