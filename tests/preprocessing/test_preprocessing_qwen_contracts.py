"""Structured output and task boundaries for difficult regulatory visual regions."""
import json
import unittest
from unittest.mock import patch

from preprocess.hybrid_config import QwenSettings
from preprocess.qwen_adapter import QwenAdapter, validate_answer
from preprocess.qwen_contracts import task_fingerprint, PROMPT_VERSIONS, SYSTEMS
from preprocess.table_representation import render_table,render_structured_table
from tests.preprocessing.test_preprocessing_hybrid import FIGURE, PANEL, GOOD, reply, table_answer


class ContractTests(unittest.TestCase):
    def test_figure_prompt_change_does_not_change_table_fingerprint(self):
        model={'name':'local','digest':'weights'}
        before={task:task_fingerprint(model,task) for task in ('describe_figure','repair_table')}
        with patch.dict(PROMPT_VERSIONS,{'describe_figure':'new-figure'}), patch.dict(SYSTEMS,{'describe_figure':'new instruction'}):
            self.assertEqual(task_fingerprint(model,'repair_table'),before['repair_table'])
            self.assertNotEqual(task_fingerprint(model,'describe_figure'),before['describe_figure'])

    def test_table_prompt_change_does_not_change_figure_fingerprint(self):
        model={'name':'local','digest':'weights'}
        before={task:task_fingerprint(model,task) for task in ('describe_figure','repair_table')}
        with patch.dict(PROMPT_VERSIONS,{'repair_table':'new-table'}):
            self.assertEqual(task_fingerprint(model,'describe_figure'),before['describe_figure'])
            self.assertNotEqual(task_fingerprint(model,'repair_table'),before['repair_table'])

    def test_generation_receives_roles_and_no_internal_panel_paths(self):
        adapter=QwenAdapter(QwenSettings())
        calls=[]
        adapter.request=lambda path,body: calls.append(body) or reply(FIGURE)
        context={'target_page':2,'image_roles':[{'image_index':1,'role':'target_crop','pdf_page':2}],
            'logical_group':{'label':'Figure 3','layout_region_count':1,'scope':'entire_caption_anchored_figure',
                'panel_regions':[{'panel_index':0,'bbox_relative_1000':[0,0,1000,1000],
                                  'asset_id':'private-id','source_refs':[{'path':'private-path'}]}]}}
        adapter.generate('describe_figure',[b'original crop bytes'],context)
        content=calls[0]['messages'][1]['content']
        self.assertIn('target_crop',content)
        self.assertNotIn('private-path',content)
        self.assertNotIn('private-id',content)

    def test_valid_panel_scales_keep_source_associations(self):
        answer={**FIGURE,'panels':[
            {**PANEL,'position':'top','x_scale':'linear','y_scale':'linear','scale_label':'Linear Scale'},
            {**PANEL,'panel_index':1,'position':'bottom','x_scale':'linear','y_scale':'logarithmic','scale_label':'Semi-logarithmic Scale'}]}
        context={'logical_group':{'panel_regions':[{'asset_id':'a','source_refs':[{'pdf_page':1}]},
                                                  {'asset_id':'b','source_refs':[{'pdf_page':1}]}]}}
        checked=validate_answer('describe_figure',reply(answer),'Time Value A B Week 12 Linear Scale Semi-logarithmic Scale','',context)
        self.assertEqual(checked['issues'],[])
        self.assertEqual([p['asset_id'] for p in checked['panel_region_associations']],['a','b'])

    def test_duplicate_indices_and_missing_panel_require_review(self):
        answer={**FIGURE,'panels':[PANEL,PANEL]}
        checked=validate_answer('describe_figure',reply(answer),'Time Value A B Week 12','')
        self.assertIn('invalid_panel_order_or_identity',checked['issues'])
        context={'logical_group':{'panel_regions':[{},{}]}}
        checked=validate_answer('describe_figure',reply(FIGURE),'Time Value A B Week 12','',context)
        self.assertIn('panel_count_differs_from_layout_regions',checked['issues'])

    def test_unsupported_log_scale_and_curve_interpretation_require_review(self):
        answer={**FIGURE,'panels':[{**PANEL,'y_scale':'logarithmic'}],
                'summary':'Both curves show similar trends.'}
        checked=validate_answer('describe_figure',reply(answer),'Time Value A B Week 12','')
        self.assertIn('logarithmic_scale_without_printed_label',checked['issues'])
        self.assertIn('chart_interpretation_outside_description_task',checked['issues'])

    def test_row_cell_representation_renders_valid_html_without_changing_visible_text(self):
        answer=table_answer(GOOD)
        html,issues=render_table(answer['rows'])
        self.assertEqual(html,GOOD)
        self.assertEqual(issues,[])
        answer['rows'][1]['cells'][0]['text']='A < B & C'
        html,_=render_table(answer['rows'])
        self.assertIn('A &lt; B &amp; C',html)

    def test_real_spans_are_preserved_but_remain_source_unverified(self):
        original='<table><tr><th colspan="2">Group</th></tr><tr><td>A</td><td>10</td></tr><tr><td>B</td><td>20</td></tr></table>'
        answer=table_answer(original)
        checked=validate_answer('repair_table',reply(answer),'Group A 10 B 20','')
        self.assertEqual(checked['answer']['table_html'],original)
        self.assertIn('merged_cell_geometry_requires_source_review',checked['issues'])
        self.assertFalse(checked['merged_cell_geometry_preserved'])
        self.assertFalse(checked['numeric_evidence_eligible'])

    def test_covered_positions_are_not_blank_cells_and_cannot_overlap(self):
        answer=table_answer(GOOD)
        answer['rows'][1]['cells'][0]['row_span']=2
        html,issues=render_structured_table(answer)
        self.assertEqual(html,'');self.assertIn('table_cells_overlap',issues)
        answer['rows'][2]['cells'].pop(0)
        html,issues=render_structured_table(answer)
        self.assertEqual(issues,[]);self.assertIn('<td rowspan="2">A</td>',html)
        self.assertIn('<tr><td>20</td></tr>',html)

    def test_missing_positions_bounds_and_order_fail_without_filling_cells(self):
        answer=table_answer(GOOD);answer['rows'][1]['cells'].pop()
        self.assertIn('table_positions_missing',render_structured_table(answer)[1])
        answer=table_answer(GOOD);answer['rows'][1]['cells'][1]['col_span']=2
        self.assertIn('table_span_out_of_bounds',render_structured_table(answer)[1])
        answer=table_answer(GOOD);answer['rows'].reverse()
        self.assertIn('invalid_table_row_order',render_structured_table(answer)[1])

    def test_header_body_boundaries_cannot_be_invented_by_a_span(self):
        answer=table_answer(GOOD);answer['rows'][0]['cells'][0]['row_span']=2
        answer['rows'][1]['cells'].pop(0)
        self.assertIn('table_span_crosses_header_body',render_structured_table(answer)[1])

    def test_flat_legacy_reply_requires_an_explicit_migration(self):
        answer=table_answer(GOOD);answer.pop('column_count')
        for row in answer['rows']:
            row.pop('row_index')
            for cell in row['cells']:
                for key in ('column_index','row_span','col_span'):cell.pop(key)
        checked=validate_answer('repair_table',reply(answer),'Group N A 10 B 20','')
        self.assertIn('invalid_response_schema',checked['issues'])

    def test_explicit_blank_cell_is_kept_blank(self):
        rows=[{'section':'header','cells':[{'text':'Time','state':'visible'}]},
              {'section':'body','cells':[{'text':'','state':'blank'}]}]
        html,issues=render_table(rows)
        self.assertIn('<td></td>',html)
        self.assertEqual(issues,[])
        rows[1]['cells'][0]['text']='copied from context'
        self.assertIn('blank_cell_contains_text',render_table(rows)[1])

    def test_redacted_cell_cannot_contain_public_label(self):
        answer=table_answer(GOOD)
        answer['rows'][1]['cells'][0].update(text='Active substance (b) (4)',state='redacted')
        checked=validate_answer('repair_table',reply(answer),'','Group N A 10 B 20 (b) (4)')
        self.assertIn('redacted_cell_contains_visible_text',checked['issues'])
        self.assertIn('source_redactions_require_review',checked['issues'])

    def test_omitted_source_redaction_marker_cannot_pass(self):
        checked=validate_answer('repair_table',reply(table_answer(GOOD)),'','Group N A 10 B 20 (b) (4)')
        self.assertIn('source_redaction_marker_not_preserved',checked['issues'])
        self.assertIn('source_redactions_require_review',checked['issues'])

    def test_original_merged_structure_requires_review_of_flat_candidate(self):
        checked=validate_answer('repair_table',reply(table_answer(GOOD)),'Group N A 10 B 20','',
                                {'table_layout':{'source_contains_spans':True}})
        self.assertIn('source_merged_cells_require_review',checked['issues'])
        self.assertFalse(checked['merged_cell_geometry_preserved'])

    def test_legacy_span_schema_cannot_be_silently_reused(self):
        answer=table_answer(GOOD)
        answer['rows'][1]['cells'][0]['row_span']=201
        checked=validate_answer('repair_table',reply(answer),'','')
        self.assertIn('invalid_response_schema',checked['issues'])

    def test_figure_number_before_sentence_period_is_not_dropped(self):
        from preprocess.table_quality import numbers
        self.assertEqual(numbers('Figure 3. Mean concentrations'),numbers('Figure 3 mean concentrations'))
        self.assertEqual(numbers('Value 1.08.'),numbers('Value 1.08'))


if __name__=='__main__':
    unittest.main()
