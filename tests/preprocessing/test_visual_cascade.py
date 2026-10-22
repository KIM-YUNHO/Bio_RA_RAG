import unittest
from PIL import Image,ImageDraw
from preprocess.extraction.visual_router import decide,reapply,usable_native,figure_group_route
from preprocess.tables.simple_grid import reconstruct


class CascadeTests(unittest.TestCase):
    def test_light_stage_is_required_before_any_visual_model(self):
        for kind in ('table','figure','text'):
            route=decide(kind,{'issues':['inconsistent_row_width']},complexity={'irregular_grid':True})
            self.assertIsNone(route['task']);self.assertEqual(route['next_stage'],'light_ocr')

    def test_review_flags_alone_do_not_call_model(self):
        route=decide('table',{'issues':['source_redactions_require_review','continuation_needs_context']})
        self.assertEqual(route['next_stage'],'review');self.assertIsNone(route['task'])

    def test_native_and_readable_single_figure_skip_vlm(self):
        self.assertEqual(decide('table',native_complete=True)['next_stage'],'native')
        light={'tokens':[{'text':'Week','confidence':.98}]}
        self.assertIsNone(decide('figure',light=light)['task'])
        self.assertIsNone(decide('figure',light=light,complexity={'multiple_panels':True})['task'])
        self.assertEqual(decide('figure',light=light,complexity={'chart_axes':True})['task'],'describe_figure')

    def test_native_selection_is_not_degraded_by_an_incomplete_light_retry(self):
        asset={'asset_type':'table','base':{'origin':'native_table_grid','content':'<table>literal</table>',
            'quality':{'issues':[]},'native_text_provenance':{'origin':'native_table_grid'}},
            'selected':{'origin':'native_table_grid','content':'<table>literal</table>'},
            'light_region_ocr':{'provenance':{'ocr':{'tokens':[]},
                'grid':{'complexity':{'borderless_unresolved':True}}},
                'quality':{'issues':['expected_one_html_table']}}}
        reapply(asset)
        self.assertTrue(usable_native(asset));self.assertEqual(asset['route']['next_stage'],'native')
        asset['selected']['content']='different';self.assertFalse(usable_native(asset))

    def test_reapply_uses_region_sidecar_quality_and_complexity(self):
        asset={'asset_type':'table','base':{'quality':{'issues':[]}},
            'light_region_ocr':{'provenance':{'ocr':{'tokens':[{'confidence':.99}]},
                'grid':{'complexity':{'irregular_grid':True}}},
                'quality':{'issues':['inconsistent_row_width']}}}
        reapply(asset);self.assertEqual(asset['route']['task'],'repair_table')

    def test_group_rebuild_keeps_sidecars_without_escalating_instruction_panels(self):
        member={'base':{},'light_region_ocr':{'provenance':{
            'ocr':{'tokens':[{'text':'Needle','confidence':.99}]}}}}
        route=figure_group_route([member,member,member])
        self.assertEqual(route['next_stage'],'light_ocr_complete');self.assertIsNone(route['task'])
        self.assertEqual(route['visual_structure_status'],'unassessed')
        self.assertEqual(figure_group_route([member,{'base':{}}])['next_stage'],'light_ocr')
        member['light_region_ocr']['provenance']['visual_complexity']={'chart_axes':True}
        self.assertEqual(figure_group_route([member])['task'],'describe_figure')

    def test_complex_table_after_light_can_escalate(self):
        light={'tokens':[{'text':'low','confidence':.3}]}
        self.assertEqual(decide('table',light=light,complexity={'irregular_grid':True})['task'],'repair_table')
        self.assertIsNone(decide('text',light=light)['task'])

    def test_unruled_low_confidence_table_does_not_become_complex_by_size(self):
        image=Image.new('RGB',(400,300),'white')
        tokens=[{'text':'label','confidence':.4,'bbox':[x,y,x+40,y+15]} for y in (10,70,130) for x in (10,210)]
        result=reconstruct(image,{'tokens':tokens})
        self.assertFalse(result['complexity']['borderless_unresolved'])
        self.assertIsNone(decide('table',light={'tokens':tokens},complexity=result['complexity'])['task'])

    def test_flat_grid_preserves_literal_numbers_and_cell_positions(self):
        image=Image.new('RGB',(303,153),'white');d=ImageDraw.Draw(image)
        for x in (1,151,301):d.line((x,1,x,151),fill='black',width=2)
        for y in (1,51,101,151):d.line((1,y,301,y),fill='black',width=2)
        tokens=[{'text':t,'confidence':.99,'bbox':[x,y,x+50,y+16]} for t,x,y in
            [('Group',20,20),('N',170,20),('A',20,70),('220',170,70),('B',20,120),('225',170,120)]]
        result=reconstruct(image,{'tokens':tokens})
        self.assertTrue(result['complexity']['simple_grid_complete'])
        self.assertIn('<td>220</td>',result['html']);self.assertIn('<td>225</td>',result['html'])
        self.assertEqual(result['cells'][3]['bbox_input_px'],[152,52,302,102])
        tokens[3]['confidence']=.3
        uncertain=reconstruct(image,{'tokens':tokens})
        self.assertFalse(uncertain['complexity']['simple_grid_complete']);self.assertEqual(uncertain['html'],'')


if __name__=='__main__':unittest.main()
