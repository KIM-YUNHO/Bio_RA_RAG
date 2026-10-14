"""Regression checks for the two observed errors without writing expected values into extraction."""
import unittest
from pipelines.pipeline_h.policy import words,FOOTNOTE,glyph_gate,candidate_gate
from pipelines.pipeline_h.structure import table_html,parsed_rows
from pipelines.pipeline_h.prepare import read,ROOT
from pipelines.pipeline_h.metadata_tools import normalize_block_metadata
from pipelines.pipeline_h.structure import cell_structure_issues

class PolicyTests(unittest.TestCase):
    def test_footnote_preserves_label_identity(self):
        self.assertEqual(words([['conventional therapy']]),words([['conventional therapy $ ^{a} $']]))
        self.assertEqual(words([['conventional therapies']]),words([['conventional therapies $^{a}$']]))
    def test_scientific_powers_not_stripped(self):
        text='10 $^{3}$ mg m $^{2}$ m $^{a}$ and AUC $_{inf}$'
        self.assertEqual(FOOTNOTE.sub('',text),text)
        self.assertEqual(words([['AUCinf exposure']]),words([['AUC $_{inf}$ exposure']]))
    def test_only_flagged_glyph_can_change(self):
        self.assertTrue(glyph_gate('FcyRIlla (V158) binding','FcγRIIIa (V158) binding')['accepted'])
        self.assertFalse(glyph_gate('FcyRIlla (V158) binding','FcγRIIIa (V159) binding')['accepted'])
        self.assertFalse(glyph_gate('FcyRIlla (V158) binding','FcγRIIIa (V158) increased binding')['accepted'])
    def test_hc_saved_candidate_now_passes_without_text_edit(self):
        path=ROOT/'pipelines/pipeline_h/vendor/compare_study_standalone/output/abp654_design_20261014_v2/design_improved/targeted_paddle.json'
        result=next(r for r in read(path)['results'] if r['job']['document']=='ABP654_HC_p034_035.pdf')
        gate=candidate_gate(result['table'],result['content'],result['job']['original_table'])
        self.assertTrue(gate['accepted'],gate)
        self.assertGreaterEqual(gate['existing_label_word_recall'],.9)
        broken=[['unrelated description' for c in row] for row in result['table']]
        self.assertFalse(candidate_gate(broken,result['content'],result['job']['original_table'])['accepted'])
    def test_raw_spans_preserved_in_selected_html(self):
        block={'n_rows':2,'n_cols':2,'cells':[{'r':0,'c':0,'rs':1,'cs':2,'text':'Group','header':True},
              {'r':1,'c':0,'rs':1,'cs':1,'text':'3.5 µg/mL'},{'r':1,'c':1,'rs':1,'cs':1,'text':'α & β'}]}
        content=table_html(block);self.assertIn('colspan="2"',content)
        self.assertEqual(parsed_rows(content),[['Group',''],['3.5 µg/mL','α & β']])
    def test_overlapping_source_cells_are_not_silently_promoted(self):
        block={'n_rows':1,'n_cols':2,'cells':[{'r':0,'c':0,'cs':2},{'r':0,'c':1}]}
        self.assertIn('overlapping_raw_cell_spans',cell_structure_issues(block))
    def test_catalog_vocabulary_is_preserved_for_speaker_rules(self):
        meta={'source':'EMA','family':'assessment_report','document_type':'assessment-report'}
        blocks=[{'type':'text','text':'The Agency conclusion','section_path':['2.4. Discussion on efficacy'],'speaker':'unknown'}]
        normalized=normalize_block_metadata(blocks,meta)
        self.assertEqual(meta['source'],'EMA');self.assertEqual(blocks[0]['speaker'],'regulator')
        self.assertEqual(normalized['normalized_doc_type'],'assessment_report')
    def test_actual_table_context_repair_preserves_cell_identity_and_variants(self):
        path=ROOT/'pipelines/pipeline_h/runs/glyph_table_context_regression/vlm/glyph_table_context.json'
        result=read(path);table=parsed_rows(result['content'])
        gate=candidate_gate(table,result['content'],result['job']['original_table'])
        self.assertTrue(gate['accepted'],gate);self.assertEqual(len(gate['glyph_cell_checks']),4)
        broken=[list(row) for row in table];broken[19][1]=broken[19][1].replace('V158','V159')
        self.assertFalse(candidate_gate(broken,result['content'],result['job']['original_table'])['accepted'])

if __name__=='__main__':unittest.main()
