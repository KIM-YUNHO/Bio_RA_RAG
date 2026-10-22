"""Source preservation tests independent of product, agency and page IDs."""
import unittest
from pipelines.pipeline_h.html_source import parse
from preprocess.tables.table_quality import inspect_table

class SourceTests(unittest.TestCase):
    def units(self,s):return parse(s,'unseen:document','source-version')['units']
    def test_nested_lists_have_no_parent_child_duplicates(self):
        units=self.units('<main><h2>Quality</h2><ul><li>Parent<ul><li>Child</li></ul></li></ul></main>')
        self.assertEqual([u['text'] for u in units],['Quality','Parent','Child'])
        self.assertEqual(len({u['id'] for u in units}),3)
    def test_inline_order_and_wrapper_text_preserved(self):
        units=self.units('<main>Lead <span>inline</span><p>Middle</p>Tail</main>')
        self.assertEqual([u['text'] for u in units],['Lead inline','Middle','Tail'])
        self.assertEqual(len({u['id'] for u in units}),3)
    def test_long_body_does_not_depend_on_preview_page_size(self):
        text='IL-12 and IL-23; 130 mg in 26 mL. '*1000
        units=self.units('<main><p>'+text+'</p></main>')
        self.assertEqual(units[0]['text'],text.strip())
    def test_table_spans_and_scientific_superscript_preserved(self):
        units=self.units('<main><table><tr><th rowspan="2">Dose</th><th>AUC<sub>inf</sub></th></tr><tr><td>10<sup>3</sup></td></tr></table></main>')
        self.assertIn('rowspan="2"',units[0]['text']);self.assertIn('<sub>inf</sub>',units[0]['text'])
        self.assertIn('<sup>3</sup>',units[0]['text']);self.assertEqual(inspect_table(units[0]['text'])['rows'],2)
    def test_noncontent_exclusions_and_original_offsets(self):
        raw='<main><nav>Menu</nav><p>Fcγ; 45 mg/0.5 mL</p><script>hidden()</script></main>'
        result=parse(raw,'unseen','version');self.assertEqual(len(result['units']),1)
        ref=result['units'][0]['dom_ref'];self.assertEqual(raw[ref['start']:ref['end']],'<p>Fcγ; 45 mg/0.5 mL</p>')
        self.assertEqual(len(result['explicit_exclusions']),2)
    def test_table_caption_preserved_as_its_own_source(self):
        units=self.units('<main><table><caption>Table 1: Concentrations</caption><tr><td>37</td></tr></table></main>')
        self.assertEqual(units[0]['text'],'Table 1: Concentrations')
        self.assertEqual(units[0]['tag'],'caption');self.assertEqual(units[1]['kind'],'table')
    def test_compact_cell_paragraphs_do_not_merge_numbers(self):
        units=self.units('<main><table><tr><td><p>45</p><p>90</p></td></tr></table></main>')
        self.assertIn('45<br>90',units[0]['text']);self.assertNotIn('4590',units[0]['text'])

if __name__=='__main__':unittest.main()
