import copy
import unittest

from preprocess.visual_groups import build_visual_groups
from preprocess.table_quality import inspect_table, numbers


def asset(identity, label, box, text, page=1, doc='upload:1', html=None):
    kind = 'table' if label=='table' else 'figure' if label=='chart' else 'text'
    return {'asset_id':identity,'document_id':doc,'version_id':'source-hash','pdf_page':page,
        'block_label':label,'asset_type':kind,'bbox_pdf':box,'bbox_input_px':box,'input_image_size':[1000,1000],
        'source_refs':[{'path':'original.pdf','sha256':'source-hash','pdf_page':page,'bbox_pdf':box}],
        'base':{'content':html if html else text,'quality':inspect_table(html) if html else None,
                'printed_text_reference':text},'native_reference':{'text':text,'reliable':bool(text)},
        'route':{'task':'describe_figure' if kind=='figure' else None},
        'selected':{'origin':'paddle','content':text},'enrichment':None}


class GroupTests(unittest.TestCase):
    def long_table(self):
        title='Important risk: serious infections (including bacterial infections)'
        html=f'<table><tr><td colspan="2">{title}</td></tr><tr><td>Evidence</td><td>Observed risks (including</td></tr></table>'
        next_html='<table><tr><td></td><td>bacterial infections) remain under review.</td></tr><tr><td>Risk factors</td><td>Other factors</td></tr></table>'
        return [asset('cap','text',[100,790,800,825],'Table 12: '+title),
            asset('body','table',[100,830,800,930],title+' Evidence Observed risks (including',html=html),
            asset('next','table',[100,60,800,850],'bacterial infections) remain under review. Risk factors Other factors',page=2,html=next_html)]

    def test_native_repeated_header_recovers_long_caption_and_cell_boundary(self):
        rows=self.long_table();before=copy.deepcopy(rows)
        groups,_=build_visual_groups(rows)
        self.assertEqual(len(groups),1)
        g=groups[0]
        self.assertEqual((g['label'],g['member_asset_ids'],g['pdf_pages']),('Table 12',['body','next'],[1,2]))
        self.assertEqual(g['relations'][-1]['basis'],'native_open_delimiter_cell_boundary')
        self.assertEqual(g['continuation_status'],'data_fragment_continuation_candidate')
        self.assertEqual(rows[0]['block_label'],'text')
        self.assertEqual(rows[0]['caption_recovery']['table_asset_id'],'body')
        for old,new in zip(before,rows):
            self.assertEqual(old['base'],new['base'])
        self.assertEqual(rows[2]['base']['quality']['cells'][0]['text'],'')
        self.assertEqual([r['pdf_page'] for r in g['source_refs']],[1,1,2])

    def test_boundary_checks_ignore_unrelated_math_and_printed_page_number(self):
        rows=self.long_table()
        cell=rows[1]['base']['quality']['cells'][-1]
        cell['text']='Prior (IL-12R $ \\beta $1). However, observed risks (including'
        rows[1]['native_reference']['text']='Prior (IL-12Rβ1). However, observed risks (including'
        rows[2]['base']['quality']['cells'][1]['text']+=' Later anti-IL-12/23p40.'
        rows[2]['native_reference']['text']+=' Later anti-\nIL-12/23ρ40.'
        rows.append(asset('page','number',[650,950,800,970],'Page 1 of 3'))
        groups,_=build_visual_groups(rows)
        self.assertEqual(groups[0]['member_asset_ids'],['body','next'])

    def test_number_label_is_not_enough_to_ignore_intervening_content(self):
        rows=self.long_table()
        rows.append(asset('number','number',[100,940,800,960],'2 participants withdrew'))
        groups,_=build_visual_groups(rows)
        self.assertEqual(groups[0]['member_asset_ids'],['body'])

    def test_long_caption_requires_native_agreement_header_match_and_clear_owner(self):
        for condition in ('native_missing','native_disagrees','different_header','prose','blocker','duplicate_body','duplicate_caption'):
            with self.subTest(condition=condition):
                rows=self.long_table()[:2]
                if condition=='native_missing':rows[0]['native_reference']['reliable']=False
                if condition=='native_disagrees':rows[0]['native_reference']['text']='Table 99: Other title'
                if condition=='different_header':rows[1]['base']['quality']['cells'][0]['text']='Other risk'
                if condition=='prose':
                    rows[0]['base']['content']=rows[0]['base']['content'].replace('12:', '12 shows')
                    rows[0]['native_reference']['text']=rows[0]['base']['content']
                if condition=='blocker':rows.append(asset('between','text',[100,826,800,829],'Other content'))
                if condition=='duplicate_body':
                    duplicate=copy.deepcopy(rows[1]);duplicate['asset_id']='duplicate';rows.append(duplicate)
                if condition=='duplicate_caption':
                    duplicate=copy.deepcopy(rows[0]);duplicate['asset_id']='duplicate';rows.append(duplicate)
                groups,_=build_visual_groups(rows)
                self.assertNotIn('caption_recovery',rows[0])
                self.assertFalse(any(g['label']=='Table 12' for g in groups))

    def test_cell_boundary_rejects_unsupported_or_interrupted_continuations(self):
        for condition in ('closed_sentence','no_native','native_disagrees','capitalized','filled_stub','heading','previous_heading','other_version','page_gap','columns'):
            with self.subTest(condition=condition):
                rows=self.long_table()
                if condition=='closed_sentence':rows[1]['base']['quality']['cells'][-1]['text']='Observed risks are complete.'
                if condition=='no_native':rows[2]['native_reference']['reliable']=False
                if condition=='native_disagrees':rows[2]['native_reference']['text']='A different native fragment.'
                if condition=='capitalized':rows[2]['base']['quality']['cells'][1]['text']='Bacterial infections) remain under review.'
                if condition=='filled_stub':rows[2]['base']['quality']['cells'][0]['text']='New evidence'
                if condition=='heading':rows.append(asset('heading','paragraph_title',[100,20,800,40],'New section',page=2))
                if condition=='previous_heading':rows.append(asset('heading','paragraph_title',[100,940,800,960],'New section'))
                if condition=='other_version':rows[2]['version_id']='different-hash'
                if condition=='page_gap':rows[2]['pdf_page']=3
                if condition=='columns':rows[2]['base']['quality']['columns']=3
                groups,_=build_visual_groups(rows)
                g=next(g for g in groups if g['label']=='Table 12')
                self.assertEqual(g['member_asset_ids'],['body'])
                self.assertNotEqual(rows[2]['logical_group_id'],g['group_id'])

    def test_cell_boundary_with_two_possible_parents_abstains(self):
        rows=self.long_table()
        cap=copy.deepcopy(rows[0]);cap.update(asset_id='othercap',block_label='table_title')
        cap['base']['content']='Table 13: Other title';cap['native_reference']['text']=cap['base']['content']
        body=copy.deepcopy(rows[1]);body['asset_id']='otherbody'
        for row,box in ((rows[0],[100,790,450,825]),(rows[1],[100,830,450,930]),
                (cap,[550,790,900,825]),(body,[550,830,900,930]),(rows[2],[100,60,900,850])):
            row['bbox_input_px']=box;row['bbox_pdf']=box
        rows.extend([cap,body])
        groups,_=build_visual_groups(rows)
        self.assertTrue(all('next' not in g['member_asset_ids'] for g in groups if g['label']))

    def test_recovered_caption_is_recomputed_on_regroup(self):
        rows=self.long_table()[:2]
        build_visual_groups(rows)
        self.assertIn('caption_recovery',rows[0])
        rows[0]['native_reference']['reliable']=False
        groups,_=build_visual_groups(rows)
        self.assertNotIn('caption_recovery',rows[0])
        self.assertFalse(any(g['label'] for g in groups))

    def test_left_margin_caption_does_not_jump_to_the_next_caption_below(self):
        rows=[asset('a','paragraph_title',[60,250,145,270],'Figure A'),
            asset('plot','chart',[70,300,930,410],'Supplies'),
            asset('b','paragraph_title',[60,435,145,455],'Figure B'),
            asset('device','chart',[70,520,930,700],'Device')]
        groups,_=build_visual_groups(rows)
        self.assertEqual({g['label']:g['member_asset_ids'] for g in groups},
            {'Figure A':['plot'],'Figure B':['device']})

    def test_left_caption_can_own_a_centered_diagram_without_horizontal_overlap(self):
        rows=[asset('cap','paragraph_title',[60,100,145,120],'Figure D'),
            asset('plot','chart',[300,150,650,330],'Needle cover')]
        groups,_=build_visual_groups(rows)
        self.assertEqual(groups[0]['member_asset_ids'],['plot'])
        self.assertEqual(groups[0]['relations'][0]['relation'],'left_margin_caption_above_candidate')

    def test_left_margin_fallback_does_not_cross_another_column_or_heading(self):
        for blockers in [[asset('left','chart',[65,160,280,300],'Left diagram'),
                          asset('right-heading','paragraph_title',[300,130,700,145],'Other column')],
                         [asset('heading','paragraph_title',[50,130,700,145],'New section')]]:
            with self.subTest(blocker=blockers[-1]['asset_id']):
                rows=[asset('cap','paragraph_title',[60,100,145,120],'Figure D'),
                    *blockers,asset('right','chart',[350,160,650,300],'Right diagram')]
                groups,_=build_visual_groups(rows)
                self.assertFalse(any('right' in g['member_asset_ids'] for g in groups))

    def test_same_row_literal_reference_disambiguates_tiled_instruction_figures(self):
        rows=[asset('upper','figure_title',[760,260,850,280],'Figure G'),
            asset('body','chart',[700,320,940,520],'Device'),
            asset('lower','figure_title',[760,560,850,580],'Figure H'),
            asset('step','text',[50,330,680,360],'Place the device at a right angle (Figure H).')]
        groups,_=build_visual_groups(rows)
        self.assertEqual([g['label'] for g in groups],['Figure H'])
        self.assertEqual(groups[0]['relations'][0]['relation'],'same_row_explicit_reference_candidate')

    def test_conflicting_row_references_and_cross_page_figures_stay_unassigned(self):
        rows=[asset('upper','figure_title',[760,260,850,280],'Figure G'),
            asset('body','chart',[700,320,940,520],'Device'),
            asset('lower','figure_title',[760,560,850,580],'Figure H'),
            asset('step','text',[50,330,680,360],'Compare Figure G and Figure H.'),
            asset('edge','paragraph_title',[60,940,145,960],'Figure E'),
            asset('next','chart',[300,20,650,150],'Continued drawing',page=2)]
        groups,_=build_visual_groups(rows)
        self.assertFalse(groups)
        self.assertEqual(rows[1]['grouping_issue'],'ambiguous_caption_association')

    def test_external_row_reference_cannot_disambiguate_a_local_caption(self):
        rows=[asset('upper','figure_title',[760,260,850,280],'Figure G'),
            asset('body','chart',[700,320,940,520],'Device'),
            asset('lower','figure_title',[760,560,850,580],'Figure H'),
            asset('source','text',[50,330,680,360],'Source: Clinical Study Report, Figure H.')]
        groups,_=build_visual_groups(rows)
        self.assertFalse(groups)
        self.assertEqual(rows[1]['grouping_issue'],'ambiguous_caption_association')

    def test_closing_parenthesis_reference_is_not_a_caption(self):
        from preprocess.visual_groups import caption_identity
        self.assertIsNone(caption_identity(asset('cap','paragraph_title',[60,20,145,40],'Figure C)')))

    def test_plain_text_standalone_label_can_be_a_caption_but_prose_cannot(self):
        from preprocess.visual_groups import caption_identity
        self.assertEqual(caption_identity(asset('cap','text',[60,20,145,40],'Figure F')),('figure','F'))
        self.assertIsNone(caption_identity(asset('prose','text',[60,20,145,40],'Figure F shows a needle.')))
        self.assertEqual(caption_identity(asset('head','header',[60,20,145,40],'Figure G')),('figure','G'))
        self.assertIsNone(caption_identity(asset('head','header',[60,20,145,40],'Figure G Results')))
        rows=[asset('cap','text',[60,100,145,120],'Figure F'),
            asset('plot','chart',[300,150,650,330],'Needle')]
        groups,_=build_visual_groups(rows)
        self.assertEqual(groups[0]['member_asset_ids'],['plot'])

    def test_letter_captions_separate_adjacent_instruction_diagrams(self):
        rows = [asset('ci','figure_title',[100,80,700,110],'Figure I'),
            asset('i','chart',[100,130,700,350],'Device I'),
            asset('cj','figure_title',[100,450,700,480],'Figure J'),
            asset('j','chart',[100,500,700,700],'Device J')]
        groups, jobs = build_visual_groups(rows)
        self.assertEqual([g['label'] for g in groups], ['Figure I', 'Figure J'])
        self.assertEqual([g['member_asset_ids'] for g in groups], [['i'], ['j']])
        self.assertEqual(len(jobs), 2)

    def test_supplement_and_roman_labels_do_not_consume_prose_words(self):
        from preprocess.visual_groups import caption_identity
        for text, expected in [('Fig. S1: Supplement', ('figure', 'S1')), ('Table IV. Summary', ('table', 'IV')),
            ('Figure 3A. Panel', ('figure', '3A')), ('Figure 3(a). Panel', ('figure', '3')), ('Figure In this study', None), ('Figure Mix', None),
            ('Figure summary', None), ('Figure 1Aspirin', None)]:
            with self.subTest(text=text):
                self.assertEqual(caption_identity(asset('cap', 'figure_title', [0,0,500,30], text)), expected)

    def test_two_panels_preserve_one_caption_parent_and_order(self):
        rows = [asset('cap','figure_title',[100,80,800,110],'Figure 3. Linear (Top) and Semi-logarithmic (Bottom)'),
            asset('a','chart',[100,150,700,400],'Linear'),asset('b','chart',[100,460,700,680],'Semi-log'),
            asset('next','paragraph_title',[100,710,700,730],'Statistical Analysis')]
        before = copy.deepcopy(rows)
        groups,jobs=build_visual_groups(rows)
        self.assertEqual(len(groups),1)
        self.assertEqual(groups[0]['member_asset_ids'],['a','b'])
        self.assertEqual(len(jobs),1)
        self.assertEqual(rows[1]['panel_index'],0)
        self.assertEqual(rows[2]['panel_index'],1)
        self.assertEqual(jobs[0]['selected']['origin'],'native_figure_caption')
        self.assertEqual(rows[1]['base'],before[1]['base'])
        self.assertEqual(jobs[0]['bbox_pdf'],[100,80,800,680])

    def test_two_numbered_captions_are_never_merged(self):
        rows=[asset('c1','figure_title',[100,80,700,110],'Figure 1. First'),
            asset('a','chart',[100,130,700,300],'A'),
            asset('c2','figure_title',[100,400,700,430],'Figure 2. Second'),
            asset('b','chart',[100,450,700,650],'B')]
        groups,_=build_visual_groups(rows)
        self.assertEqual([g['member_asset_ids'] for g in groups],[['a'],['b']])

    def test_caption_below_diagram_and_other_document_stay_separate(self):
        rows=[asset('a','chart',[100,100,700,400],'Design'),
            asset('cap','paragraph_title',[100,420,700,440],'Figure 1. Study design'),
            asset('other','chart',[100,150,700,400],'Other',doc='upload:2')]
        groups,jobs=build_visual_groups(rows)
        self.assertEqual(groups[0]['member_asset_ids'],['a'])
        self.assertNotIn('logical_group_id',rows[-1])

    def test_heading_closes_figure_group(self):
        rows=[asset('cap','figure_title',[100,80,700,110],'Figure 1. First'),
            asset('a','chart',[100,130,700,300],'A'),
            asset('heading','paragraph_title',[100,400,700,430],'Safety Assessment'),
            asset('b','chart',[100,500,700,650],'Uncaptioned')]
        groups,_=build_visual_groups(rows)
        self.assertEqual(groups[0]['member_asset_ids'],['a'])
        self.assertNotIn('logical_group_id',rows[-1])

    def test_resume_rebuild_discards_stale_association_flags(self):
        rows=[asset('cap','figure_title',[100,80,700,110],'Figure 1. First'),
            asset('a','chart',[100,130,700,300],'A')]
        rows[1].update(grouping_issue='ambiguous_caption_association',continuation_status='data_fragment_continuation_candidate',
                       context_association_issues=['old_issue'])
        groups,jobs=build_visual_groups(rows)
        self.assertEqual(groups[0]['member_asset_ids'],['a'])
        self.assertNotIn('grouping_issue',rows[1])
        self.assertNotIn('continuation_status',rows[1])
        self.assertNotIn('context_association_issues',rows[1])
        self.assertIsNone(jobs[0]['panel_index'])

    def test_table_caption_on_previous_page_keeps_both_source_pages(self):
        html='<table><tr><td>Group</td><td>N</td></tr><tr><td>A</td><td>10</td></tr></table>'
        rows=[asset('cap','table_title',[100,920,700,940],'Table 14. Single 45 mg'),
            asset('tail','paragraph_title',[100,50,700,75],'or 90 mg administration',page=2),
            asset('table','table',[100,90,700,300],'',page=2,html=html)]
        groups,_=build_visual_groups(rows)
        self.assertEqual(groups[0]['continuation_status'],'caption_on_previous_page')
        self.assertEqual(groups[0]['pdf_pages'],[1,2])
        self.assertIn('90 mg',groups[0]['caption_text'])

    def test_latex_header_fraction_does_not_create_false_numeric_mismatch(self):
        self.assertEqual(numbers('t1/2 (h)'),numbers('t $ _{{1/2}} $ (h)'))
        self.assertEqual(numbers('Cmax 5.095 ±1.498'),numbers('C $ _{{max}} $ 5.095\\n $ \\pm $1.498'))

    def test_misclassified_caption_tail_and_native_source_are_attached(self):
        html='<table><tr><td>Group</td><td>N</td></tr><tr><td>A</td><td>10</td></tr></table>'
        rows=[asset('cap','table_title',[100,920,700,940],'Table 14. Single 45 mg'),
            asset('tail','figure_title',[100,50,700,75],'or 90 mg administration',page=2),
            asset('table','table',[100,90,700,300],'',page=2,html=html),
            asset('note','vision_footnote',[100,310,700,330],'Source data: Trial A',page=2)]
        original=copy.deepcopy(rows)
        groups,_=build_visual_groups(rows)
        g=groups[0]
        self.assertEqual(g['context_asset_ids'], ['cap','tail','note'])
        self.assertEqual(g['context_roles'], {'cap':'caption','tail':'caption_continuation','note':'source'})
        self.assertEqual(rows[1]['block_label'], 'figure_title')
        self.assertEqual(rows[2]['base'], original[2]['base'])
        self.assertEqual(g['pdf_pages'], [1,2])
        self.assertEqual(rows[3]['selected']['origin'], 'native_table_context')

    def test_note_owner_stops_at_heading_and_nearby_figure(self):
        html='<table><tr><td>N</td></tr><tr><td>10</td></tr></table>'
        rows=[asset('t','table',[100,100,700,300],'',html=html),
            asset('n1','vision_footnote',[100,302,600,320],'Units: ng/mL'),
            asset('heading','paragraph_title',[100,325,700,340],'Clinical Results'),
            asset('n2','vision_footnote',[100,345,600,360],'Source: Other study'),
            asset('fig','chart',[100,365,700,420],'Figure'),
            asset('n3','vision_footnote',[100,425,600,440],'Source: Figure study')]
        groups,_=build_visual_groups(rows)
        g=next(g for g in groups if g['kind']=='table')
        self.assertEqual(g['context_asset_ids'], ['n1'])
        self.assertEqual(g['context_roles']['n1'], 'unit')
        self.assertNotIn('logical_group_id',rows[3])
        self.assertNotIn('logical_group_id',rows[5])

    def test_side_by_side_table_footnote_is_ambiguous(self):
        html='<table><tr><td>N</td></tr><tr><td>10</td></tr></table>'
        rows=[asset('a','table',[100,100,440,300],'',html=html),
            asset('b','table',[460,100,800,300],'',html=html),
            asset('note','vision_footnote',[100,305,800,330],'Source: Shared or unclear')]
        groups,_=build_visual_groups(rows)
        self.assertNotIn('logical_group_id',rows[2])
        self.assertEqual(rows[2]['context_association_issues'], ['ambiguous_table_context_owner'])
        self.assertTrue(all(g['context_issues'][0]['reason']=='ambiguous_note_owner' for g in groups))

    def test_intervening_content_prevents_cross_page_caption_ownership(self):
        html='<table><tr><td>N</td></tr><tr><td>10</td></tr></table>'
        for label,value in [('figure_title','Figure 2. Other figure'), ('text','and clinical response improved')]:
            with self.subTest(label=label):
                rows=[asset('cap','table_title',[100,920,700,940],'Table 14. Single 45 mg'),
                    asset('tail',label,[100,30,700,50],value,page=2),
                    asset('body','text',[100,55,700,75],'Independent paragraph',page=2),
                    asset('table','table',[100,90,700,300],'',page=2,html=html)]
                groups,_=build_visual_groups(rows)
                g=next(g for g in groups if g['kind']=='table')
                self.assertEqual(g['caption_part_asset_ids'], [])
                self.assertEqual(g['association_status'], 'standalone_region')
                self.assertNotIn('logical_group_id',rows[0])
                self.assertNotIn('logical_group_id',rows[1])

    def test_redacted_span_and_japanese_row_name_changes_require_review(self):
        html='<table><tr><td>Group</td><td>Function</td></tr><tr><td>A</td><td rowspan="2">Active substance (b) (4)</td></tr><tr><td>B</td></tr></table>'
        checked=inspect_table(html)
        self.assertIn('redacted_cells_require_review',checked['issues'])
        self.assertIn('visible_text_spans_redacted_rows',checked['issues'])
        html='<table><tr><td>群</td><td>N</td></tr><tr><td>本剂</td><td>62</td></tr></table>'
        self.assertIn('native_row_labels_not_preserved',inspect_table(html,'群 N 本剤 62',True)['issues'])


if __name__=='__main__':
    unittest.main()
