"""Ownership and source-range tests for context-first visual retrieval."""
from copy import deepcopy
import unittest

from preprocess import visual_context as vc
from test_chunking import CharacterCounter


class VisualContextTests(unittest.TestCase):
    def setUp(self):
        self.assets, self.units, self.groups = [], [], []

    def add(self, uid, text, page=1, y=50, label='text', kind='text', native=True):
        ref = {'path': 'source.pdf', 'sha256': 'v1', 'pdf_page': page, 'bbox_pdf': [20,y,500,y+20]}
        asset = {'asset_id': uid, 'document_id': 'doc', 'version_id': 'v1', 'pdf_page': page,
            'bbox_pdf': ref['bbox_pdf'], 'asset_type': kind, 'block_label': label,
            'source_refs': [ref], 'native_reference': {'reliable': native, 'text': text},
            'base': {'content': text}, 'selected': {'content': text, 'origin': 'paddle'}}
        unit = {'unit_id': uid, 'document_id': 'doc', 'version_id': 'v1', 'kind': kind,
            'text': text, 'content_origin': 'paddle', 'source_refs': [ref], 'review_required': False}
        self.assets.append(asset)
        self.units.append(unit)
        return asset

    def group(self, uid, label, page=1, y=200, caption=None, kind='figure'):
        title = self.add(uid+'cap', caption or label, page, y, 'caption')
        body = self.add(uid+'body', '', page, y+30, 'chart', kind)
        title['logical_group_id'] = body['logical_group_id'] = uid
        group = {'group_id': uid, 'document_id': 'doc', 'version_id': 'v1', 'kind': kind,
            'label': label, 'caption_text': caption or label, 'caption_asset_id': title['asset_id'],
            'caption_part_asset_ids': [title['asset_id']], 'member_asset_ids': [body['asset_id']],
            'source_refs': [*title['source_refs'], *body['source_refs']]}
        self.groups.append(group)
        return group

    def build(self):
        return vc.assemble(self.assets, self.units, self.groups)

    def test_repeated_letters_stay_in_instruction_scope_even_for_identical_titles(self):
        for index in (1, 2):
            self.add(f'ifu{index}', 'INSTRUCTIONS FOR USE\nMedicine\nInjection', index, 10, 'header')
            self.add(f'p{index}', f'Prepare device {index}. See Figure B.', index, 80)
            self.group(f'g{index}', 'Figure B', index)
        bundle = self.build()
        refs = [l for l in bundle['links'] if l['context_role'] == 'referring_sentence']
        self.assertEqual({(l['source_unit_id'], l['visual_id']) for l in refs}, {('p1','g1'),('p2','g2')})
        self.assertNotEqual(bundle['targets'][0]['scope_id'], bundle['targets'][1]['scope_id'])

    def test_repeated_number_without_scope_is_never_resolved_by_nearest_page(self):
        self.add('p', 'See Figure B.', 1, 80)
        self.group('g1', 'Figure B', 1)
        self.group('g2', 'Figure B', 2)
        refs = [l for l in self.build()['links'] if l['context_role'] == 'referring_sentence']
        self.assertTrue(refs)
        self.assertTrue(all(l['visual_id'] is None for l in refs))

    def test_multivisual_paragraph_attaches_only_the_explicit_sentence(self):
        self.group('f4', 'Figure 4', 1)
        self.group('t5', 'Table 5', 2, kind='table')
        text = 'Study design is in Figure 3. Mean concentrations are shown in Figure 4 and Table 5. Safety was comparable.'
        self.add('p', text, 1, 100)
        bundle = self.build()
        refs = [l for l in bundle['links'] if l['context_role'] == 'referring_sentence' and l['visual_id']]
        self.assertEqual({l['visual_id'] for l in refs}, {'f4', 't5'})
        self.assertTrue(all(vc.link_text(l, bundle) == 'Mean concentrations are shown in Figure 4 and Table 5.' for l in refs))
        self.assertTrue(all(not l['numeric_evidence_eligible'] for l in refs))

    def test_external_source_number_cannot_link_to_a_local_figure(self):
        self.group('f', 'Figure 11-2')
        self.add('source', 'Source: Clinical Study Report ABC, Figure 11-2.', y=350)
        links = [l for l in self.build()['links'] if l['source_unit_id'] == 'source']
        self.assertEqual(links[0]['link_status'], 'external_reference')
        self.assertIsNone(links[0]['visual_id'])

    def test_orphan_caption_is_recorded_separately_from_prose(self):
        self.add('orphan', 'Figure E', 1, 740, 'paragraph_title')
        self.group('other','Figure E',2)
        bundle=self.build()
        self.assertIn('orphan',bundle['summary']['orphan_caption_source_unit_ids'])
        self.assertFalse(any(l['source_unit_id']=='orphan' for l in bundle['links']))

    def test_trailing_parenthetical_reference_keeps_its_description_and_stops_at_safety(self):
        self.group('f','Figure D')
        text='Pull the cover straight off. (See Figure D) Safety is described elsewhere.'
        self.add('p',text,y=100)
        refs=[l for l in self.build()['links'] if l['context_role']=='referring_sentence']
        self.assertEqual(len(refs),1)
        self.assertEqual(vc.link_text(refs[0],self.build()),
            'Pull the cover straight off. (See Figure D)')

    def test_legacy_v1_source_bundle_can_still_be_reconstructed(self):
        self.group('f','Figure D')
        self.add('p','Pull the cover straight off. (See Figure D)',y=100)
        bundle=vc.assemble(self.assets,self.units,self.groups,version='visual-context-1')
        self.assertEqual(bundle['version'],'visual-context-1')
        vc.validate_bundle(bundle,self.assets,self.units,self.groups)
        refs=[l for l in bundle['links'] if l['context_role']=='referring_sentence']
        self.assertEqual(vc.link_text(refs[0],bundle),'(See Figure D)')

    def test_native_bullet_prefix_is_excluded_by_source_offsets_for_reverse_matching(self):
        self.group('f','Figure F')
        self.add('p','•\nInsert at 45 degrees. (See Figure F)',y=100)
        bundle=self.build()
        link=next(l for l in bundle['links'] if l['source_unit_id']=='p')
        self.assertEqual(vc.link_text(link,bundle),'Insert at 45 degrees. (See Figure F)')
        self.assertEqual(link['context_range'][0],2)
        text='- Insert at 45 degrees. (See Figure F)'
        chunk={'document_id':'doc','version_id':'v1','text':text,'structure':{},
            'provenance':{'source_spans':[{'unit_id':'p','separator_before':'',
                'representation_start':0,'representation_end':len(text)}]}}
        self.assertTrue(vc.reverse_link_present(chunk,link,bundle))
        chunk['text']=text.replace('45','90')
        self.assertFalse(vc.reverse_link_present(chunk,link,bundle))

    def test_plural_ranges_japanese_and_fullwidth_offsets(self):
        text = 'Tables 5–7 and 9; Ｆｉｇｕｒｅ Ａ; 図２及び３; 表4.'
        refs = vc.references(text)
        self.assertEqual([(r['kind'],r['identifier']) for r in refs],
            [('table','5'),('table','6'),('table','7'),('table','9'),('figure','A'),('figure','2'),('figure','3'),('table','4')])
        self.assertTrue(all(text[r['range'][0]:r['range'][1]] == r['literal'] for r in refs))

    def test_missing_pages_block_inferred_scope_and_old_study_headings_do_not_leak(self):
        self.add('p', 'See Table 1.', 1)
        self.group('t', 'Table 1', 3, kind='table')
        links = [l for l in self.build()['links'] if l['context_role'] == 'referring_sentence']
        self.assertIsNone(links[0]['visual_id'])

    def test_footer_and_table_cells_never_become_prose_references(self):
        self.group('f', 'Figure 2')
        self.add('footer', 'See Figure 2.', y=400, label='footer')
        self.add('cell', 'See Figure 2.', y=500, label='table', kind='table')
        self.assertFalse(any(l['context_role'] == 'referring_sentence' for l in self.build()['links']))

    def test_native_bullet_instruction_overrides_vision_footnote_without_reclassifying_real_note(self):
        self.group('f', 'Figure D')
        self.add('instruction', '•\nClean the rubber stopper with an antiseptic swab. (See Figure D)', y=100, label='vision_footnote')
        self.add('real_note', '* Values are based on study results. (See Figure D)', y=400, label='vision_footnote')
        bundle=self.build()
        refs=[l for l in bundle['links'] if l['context_role']=='referring_sentence']
        self.assertEqual([(l['source_unit_id'],l['visual_id']) for l in refs],[('instruction','f')])
        self.assertEqual(refs[0]['link_basis'],'explicit_reference_layout_role_conflict')
        self.assertTrue(vc.link_text(refs[0],bundle).startswith('Clean'))

    def test_layout_role_override_requires_native_support_and_unowned_bullet(self):
        self.group('f','Figure D')
        value='• Remove the cover from the syringe. (See Figure D)'
        self.add('unreliable',value,y=100,label='vision_footnote',native=False)
        self.add('unmarked',value[2:],y=120,label='vision_footnote')
        self.add('true_footnote',value,y=140,label='footnote')
        owned=self.add('owned',value,y=160,label='vision_footnote')
        owned['logical_group_id']='some_table'
        self.assertFalse(any(l['context_role']=='referring_sentence' for l in self.build()['links']))

    def test_dot_leader_toc_is_not_a_reference_even_when_target_exists(self):
        self.group('f','Figure 1')
        self.add('toc','Figure 1. Study design ...........................33',y=100)
        bundle=self.build()
        self.assertFalse(any(l['source_unit_id']=='toc' for l in bundle['links']))
        self.assertEqual(next(c for c in bundle['units'] if c['source_unit_id']=='toc')['context_role_hint'],'table_of_contents')
        self.assertFalse(vc.toc_entry('The response ... is shown in Figure 1.'))

    def test_legacy_v2_preserves_the_original_role_and_toc_rules(self):
        self.group('f','Figure 1')
        self.add('toc','Figure 1. Study design ...........................33',y=100)
        self.add('p','• Read the study design first. (See Figure 1)',y=120,label='vision_footnote')
        bundle=vc.assemble(self.assets,self.units,self.groups,version='visual-context-2')
        vc.validate_bundle(bundle,self.assets,self.units,self.groups)
        self.assertTrue(any(l['source_unit_id']=='toc' for l in bundle['links']))
        self.assertFalse(any(l['source_unit_id']=='p' for l in bundle['links']))

    def test_embedded_note_retains_exact_native_span_and_owning_table(self):
        group=self.group('t','Table 3',kind='table')
        asset=next(a for a in self.assets if a['asset_id']=='tbody')
        original='<table><tr><td>Weight</td><td>Dose</td></tr><tr><td>60 kg</td><td>45 mg</td></tr><tr><td colspan="2">$ ^{{*}} $ For lower weights see Table 2 for injection volumes.</td></tr></table>'
        native='Weight\nDose\n60 kg\n45 mg\n* \nFor lower weights see Table 2 for injection volumes.\n'
        asset['selected']['content']=original
        asset['native_reference']['text']=native
        next(u for u in self.units if u['unit_id']=='tbody')['text']=original
        bundle=self.build()
        notes=[l for l in bundle['links'] if l['link_basis']=='embedded_table_row_native_span']
        self.assertEqual(len(notes),1)
        self.assertEqual((notes[0]['context_role'],notes[0]['visual_id']),('footnote','t'))
        self.assertEqual(vc.link_text(notes[0],bundle),'* \nFor lower weights see Table 2 for injection volumes.')
        self.assertEqual(notes[0]['embedded_html_row'],2)
        self.assertEqual(asset['selected']['content'],original)
        self.assertFalse(any(l['context_role']=='referring_sentence' and l['source_unit_id']=='tbody' for l in bundle['links']))
        tampered=deepcopy(bundle);tampered['links'][-1]['context_range'][0]+=1
        with self.assertRaises(ValueError):vc.validate_bundle(tampered,self.assets,self.units,self.groups)

    def test_spanning_data_rows_and_unconfirmed_note_numbers_stay_intact(self):
        asset=self.add('table','',kind='table')
        asset['selected']['content']='<table><tr><td>A</td><td>B</td></tr><tr><td colspan="2">Combined response at Week 12</td></tr></table>'
        asset['native_reference']['text']='Combined response at Week 12'
        self.assertEqual(vc.embedded_table_notes(asset),[])
        asset['selected']['content']='<table><tr><td>A</td><td>B</td></tr><tr><td colspan="2">* Response was measured at Week 90.</td></tr></table>'
        asset['native_reference']['text']='* Response was measured at Week 12.'
        self.assertEqual(vc.embedded_table_notes(asset),[])
        asset['native_reference']['text']='Response was measured at Week 90.'
        self.assertEqual(vc.embedded_table_notes(asset),[])

    def test_previous_page_caption_retains_separate_caption_and_body_locators(self):
        group = self.group('t', 'Table 14', 2, kind='table')
        title = next(a for a in self.assets if a['asset_id'] == group['caption_asset_id'])
        title['pdf_page'] = title['source_refs'][0]['pdf_page'] = 1
        self.add('p', 'Parameters are shown in Table 14.', 1, 80)
        target = self.build()['targets'][0]
        self.assertEqual(target['caption_page'], 1)
        self.assertEqual(target['pdf_page'], 2)
        self.assertEqual(target['pdf_pages'], [2])

    def test_header_budget_preserves_whole_sentence_and_detects_fabrication(self):
        self.group('f', 'Figure 2')
        self.add('p', 'Long description is shown in Figure 2.', y=100)
        bundle = self.build()
        chunk = {'document_id':'doc','version_id':'v1','header':'Base','text':'Figure 2',
            'structure':{'figure_id':'f','table_id':None}, 'chunking':{'embedding_limit':1000},
            'provenance':{'source_spans':[]}}
        blocked = deepcopy(chunk)
        vc.apply_to_chunks([blocked], bundle, CharacterCounter(), max_tokens=10)
        self.assertEqual(blocked['header'], 'Base')
        self.assertTrue(blocked['structure']['visual_context']['link_ids'])
        vc.apply_to_chunks([chunk], bundle, CharacterCounter(), max_tokens=500)
        self.assertIn('Long description is shown in Figure 2.', chunk['header'])
        vc.verify_chunk_links(chunk, bundle)
        chunk['header'] += ' invented'
        with self.assertRaisesRegex(ValueError, 'reconstruct'):
            vc.verify_chunk_links(chunk, bundle)

    def test_source_and_scope_tampering_is_rejected(self):
        self.group('f', 'Figure 2')
        self.add('p', 'See Figure 2.', y=100)
        bundle = self.build()
        bundle['links'][0]['version_id'] = 'another-version'
        with self.assertRaisesRegex(ValueError, 'differs'):
            vc.validate_bundle(bundle, self.assets, self.units, self.groups)

    def test_partial_page_prose_cannot_supply_context_to_a_complete_visual(self):
        self.add('p', 'See Figure 2.', 1)
        self.group('f', 'Figure 2', 2)
        pages = [{'document_id':'doc','version_id':'v1','pdf_page':1,'status':'partial'},
                 {'document_id':'doc','version_id':'v1','pdf_page':2,'status':'parsed'}]
        bundle = vc.assemble(self.assets, self.units, self.groups, pages=pages)
        self.assertFalse(any(l['source_unit_id']=='p' for l in bundle['links']))

    def test_reverse_native_quote_maps_whitespace_without_reusing_native_offsets(self):
        self.group('t', 'Table 14', kind='table')
        self.add('p', 'Median parameters\n are shown in Table 14.', y=100)
        bundle = self.build()
        text = 'Median parameters are shown in Table 14.'
        chunk = {'document_id':'doc','version_id':'v1','header':'Base','text':text,
            'structure':{'figure_id':None,'table_id':None}, 'chunking':{'embedding_limit':1000},
            'provenance':{'source_spans':[{'unit_id':'p','separator_before':'',
                'representation_start':0,'representation_end':len(text)}]}}
        vc.apply_to_chunks([chunk], bundle, CharacterCounter())
        self.assertTrue(chunk['structure']['related_visual_link_ids'])
        vc.verify_chunk_links(chunk, bundle)
        self.assertIsNone(vc.literal_window(text.replace('14','15'), 'Median parameters\n are shown in Table 14.'))


if __name__ == '__main__':
    unittest.main()
