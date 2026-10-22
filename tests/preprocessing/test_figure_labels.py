"""Partial figure fields must never borrow another panel/caption or certify data."""
from copy import deepcopy
import unittest

from preprocess.figure_labels import project, support, references_for
from preprocess.visual_groups import build_visual_groups
from tests.preprocessing.test_visual_groups import asset
from tests.preprocessing.test_preprocessing_hybrid import FIGURE, PANEL


class FigureLabelsTests(unittest.TestCase):
    def test_current_ordinary_ocr_support_is_token_bound_and_legacy_projection_is_unchanged(self):
        member={'asset_id':'panel','source_refs':[{'pdf_page':1}],
            'native_reference':{'reliable':False,'text':''},'base':{},
            'light_region_ocr':{'provenance':{'ocr':{'tokens':[
                {'text':'Concentration (ng/mL)','confidence':.95,'bbox':[10,20,100,40]},
                {'text':'Unsupported','confidence':.5,'bbox':[10,50,100,70]}]}}}}
        refs=references_for(member,[],version='figure-labels-3')
        found=support('Concentration (ng/mL)',refs)
        self.assertEqual(found['token_index'],0)
        self.assertEqual(found['bbox_crop_px'],[10,20,100,40])
        self.assertIsNone(support('Unsupported',refs))
        self.assertEqual(references_for(member,[],version='figure-labels-2'),[])

    def test_crop_float_roundoff_does_not_allow_real_source_boundary_expansion(self):
        from preprocess.hybrid_validate import contains_crop
        region = [98.8202, 542.4, 504.6548, 635.52]
        observed = [98.82019805908203, 628.0703735351562, 504.65478515625, 639.52001953125]
        self.assertTrue(contains_crop(region, observed, 4))
        self.assertFalse(contains_crop(region, observed, 0))
        self.assertFalse(contains_crop(region, [98.8202,628,504.6548,639.521], 4))
        self.assertFalse(contains_crop(region, [100,550,99,560], 4))

    def fixture(self):
        rows = [asset('cap', 'figure_title', [100,50,800,75], 'Figure 3. Secret axis title'),
                asset('top', 'chart', [100,100,800,400], 'Time\nValue\nGroup A\nLinear Scale'),
                asset('bottom', 'chart', [100,450,800,750], 'Week\nConcentration\nGroup B')]
        # Region OCR, not caption text, is used as label support.
        groups, jobs = build_visual_groups(rows)
        parent = jobs[0]
        answer = deepcopy(FIGURE)
        answer['panels'] = [{**PANEL, 'position': 'top', 'x_axis': 'Time', 'y_axis': 'Value', 'scale_label': 'Linear Scale'},
                            {**PANEL, 'panel_index': 1, 'position': 'bottom', 'x_axis': 'Week', 'y_axis': 'Concentration'}]
        answer['legend'] = ['Group A', 'Group B']
        answer['printed_labels'] = []
        parent['enrichment'] = {'validation': {'answer': answer, 'issues': ['model_reported_uncertainty']}}
        return rows, groups[0], parent

    def test_partial_labels_keep_panel_support_and_never_index_prose(self):
        rows, group, parent = self.fixture()
        result = project(parent, group, {a['asset_id']: a for a in rows})
        self.assertEqual(result['accepted_fields'], 7)
        self.assertIn('Panel 1 x_axis: Time', result['text'])
        self.assertNotIn(FIGURE['summary'], result['text'])
        self.assertTrue(result['description_review_required'])
        self.assertFalse(result['numeric_evidence_eligible'])
        f = next(f for f in result['fields'] if f['role'] == 'y_axis' and f['panel_index'] == 1)
        self.assertEqual(f['evidence']['asset_id'], 'bottom')

    def test_vlm_role_assignment_preserves_ordinary_labels_omitted_by_the_model(self):
        rows,group,parent=self.fixture()
        rows[1]['light_region_ocr']={'provenance':{'ocr':{'tokens':[
            {'text':'Printed additional label','confidence':.95,'bbox':[20,30,180,50]}]}}}
        lookup={a['asset_id']:a for a in rows}
        old=project(parent,group,lookup,version='figure-labels-3')
        result=project(parent,group,lookup,version='figure-labels-4')
        self.assertNotIn('Printed additional label',old['text'])
        self.assertIn('Printed additional label',result['text'])
        field=next(f for f in result['fields'] if f['text']=='Printed additional label')
        self.assertEqual(field['role_status'],'unassigned_ocr_literal')
        self.assertEqual(field['evidence']['token_index'],0)
        self.assertFalse(field['numeric_evidence_eligible'])

    def test_axis_cannot_borrow_other_panel_or_caption(self):
        rows, group, parent = self.fixture()
        parent['enrichment']['validation']['answer']['panels'][0]['y_axis'] = 'Concentration'
        parent['enrichment']['validation']['answer']['panels'][1]['x_axis'] = 'Secret axis title'
        result = project(parent, group, {a['asset_id']: a for a in rows})
        rejected = [f for f in result['fields'] if f['status'] == 'needs_review']
        self.assertEqual(len(rejected), 2)
        self.assertNotIn('Panel 1 y_axis: Concentration', result['text'])

    def test_contiguous_phrase_and_units_must_agree(self):
        refs = [{'text': 'Value Mean\nTime (day)', 'asset_id': 'a'}]
        self.assertIsNone(support('Mean Value', refs))
        self.assertIsNone(support('Time (hour)', refs))
        self.assertIsNotNone(support('Time (day)', refs))
        self.assertIsNone(support('A', [{'text': 'ABC'}]))
        self.assertIsNotNone(support('血清中濃度 (μg/mL)', [{'text': '血清中濃度（μg/mL）'}]))
        self.assertIsNone(support('Time', [{'text': 'Time', 'allowed_roles': ['y_axis']}], 'x_axis'))

    def test_numeric_only_and_digitized_table_are_rejected(self):
        rows, group, parent = self.fixture()
        parent['enrichment']['validation']['answer']['printed_labels'] = ['243\n251', 'Invented Value 999']
        rows[1]['base']['printed_text_reference'] += '\n<table><tr><td>Invented Value 999</td></tr></table>\n243\n251'
        result = project(parent, group, {a['asset_id']: a for a in rows})
        self.assertEqual(result['rejected_fields'], 2)
        self.assertNotIn('999', result['text'])

    def test_wrong_panel_count_and_truncated_candidate_fall_back(self):
        rows, group, parent = self.fixture()
        for issue in ['generation_incomplete', 'invalid_panel_order_or_identity']:
            parent['enrichment']['validation']['issues'] = [issue]
            result = project(parent, group, {a['asset_id']: a for a in rows})
            self.assertEqual(result['text'], group['caption_text'])
            self.assertEqual(result['accepted_fields'], 0)

    def test_title_prefix_removed_only_with_literal_support(self):
        rows, group, parent = self.fixture()
        parent['enrichment']['validation']['answer']['panels'][0]['x_axis'] = 'title:Time'
        result = project(parent, group, {a['asset_id']: a for a in rows})
        self.assertIn('Panel 1 x_axis: Time', result['text'])
        self.assertNotIn('title:Time', result['text'])


if __name__ == '__main__':
    unittest.main()
