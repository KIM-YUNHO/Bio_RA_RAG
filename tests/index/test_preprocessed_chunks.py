"""The upload adapter must retain source ranges and exclusion/group boundaries."""
import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz

from tests.preprocessing.test_preprocessing_hybrid import Parser, Qwen, GOOD, BAD, reply, FIGURE
from tests.index.test_chunking import CharacterCounter
from preprocess.extraction.hybrid import run_hybrid
from preprocess.files import read_jsonl
from preprocess.hybrid_config import HybridConfig
from preprocess.ingest import from_inputs, sha256
from rag.index import preprocessed_chunks as pc
from rag.index.chunk_schema import validate_dataset
from rag.index import context_units as cu


class PreprocessedChunkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf, self.run, self.out = self.root/'source.pdf', self.root/'preprocessing', self.root/'candidate'
        with fitz.open() as pdf:
            for i in range(2):
                page = pdf.new_page(width=400, height=400)
                for x, y, text in ((40,70,'Group'), (150,70,'N'), (40,100,'A'), (150,100,'10'),
                    (40,130,'B'), (150,130,'20'), (40,190,'Time Value A B Week 12')):
                    page.insert_text((x, y), text)
            pdf.save(self.pdf)
        self.docs = from_inputs([self.pdf], self.run)
        self.counter = CharacterCounter()

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, parser=None, qwen=None, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_hybrid(self.docs, self.run, HybridConfig(), paddle=parser or Parser(),
                qwen=qwen, use_qwen=qwen is not None, **kw)

    def test_plain_pdf_has_unknown_metadata_and_no_registry_or_agency_requirement(self):
        self.prepare(pages=[1])
        chunks, summary, excluded = pc.build(self.run, self.counter)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]['product_id'], 'unknown')
        self.assertEqual(chunks[0]['language'], 'unknown')
        self.assertEqual(chunks[0]['text'], GOOD)
        self.assertEqual(chunks[0]['schema_version'], 'rag-chunk-3')
        self.assertFalse(chunks[0]['representation']['numeric_evidence_eligible'])
        self.assertEqual(summary['source_verification']['offset_basis'], 'preprocessing_representation_unit')

    def test_dataset_metadata_and_false_search_default_survive_into_chunks(self):
        self.docs[0].metadata.update(ingredient='synthetic', product_kind='biosimilar', family='label',
            author='regulator', tier='label', default_search=False, dataset_version='synthetic-v2',
            evaluation_cohort='development', evaluation_family_id='synthetic-family',
            exposure_status='history_unknown', model_execution_allowed=True, baseline_status='added_document_id')
        self.prepare(pages=[1])
        chunks, _, _ = pc.build(self.run, self.counter)
        self.assertTrue(chunks)
        for c in chunks:
            meta = c['document_metadata']
            self.assertIs(meta['default_search'], False)
            self.assertEqual(meta['evaluation_family_id'], 'synthetic-family')
            self.assertEqual(meta['dataset_version'], 'synthetic-v2')
            self.assertFalse(c['representation']['numeric_evidence_eligible'])

    def test_locked_metadata_blocks_enrichment_resume_before_writes(self):
        from unittest.mock import Mock
        from preprocess.extraction.hybrid import resume_enrichment
        self.prepare(pages=[1])
        path = self.run/'extraction_manifest.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        manifest['documents'][0]['metadata']['model_execution_allowed'] = False
        path.write_text(json.dumps(manifest), encoding='utf-8')
        backend = Mock()
        with self.assertRaisesRegex(ValueError, 'execution denied'):
            resume_enrichment(self.run, HybridConfig(), adapter=backend)
        self.assertEqual(backend.mock_calls, [])

    def test_waiting_table_does_not_enter_chunks_or_semantic_contexts(self):
        self.prepare(Parser(BAD, figure=True), Qwen(), pages=[1], max_qwen_calls=1)
        # The table is repaired; the remaining figure is pending and excluded.
        chunks, summary, excluded = pc.build(self.run, self.counter)
        pending = next(u for u in read_jsonl(self.run/'source_units.jsonl') if u['kind'] == 'figure')
        self.assertNotIn(pending['unit_id'], [s['unit_id'] for c in chunks for s in c['provenance']['source_spans']])
        self.assertEqual(len(excluded), 1)
        units, _, _ = pc.materialize(chunks)
        self.assertNotIn(pending['unit_id'], units)
        self.assertEqual(summary['included_units'], 1)

    def test_source_tampering_and_running_checkpoints_cannot_be_chunked(self):
        self.prepare(pages=[1])
        status = json.loads((self.run/'processing_status.json').read_text(encoding='utf-8'))
        status['lifecycle'] = 'running'
        (self.run/'processing_status.json').write_text(json.dumps(status), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'running'):
            pc.build(self.run, self.counter)
        status['lifecycle'] = 'finished'
        (self.run/'processing_status.json').write_text(json.dumps(status), encoding='utf-8')
        (self.run/'source_units.jsonl').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            pc.build(self.run, self.counter)

    def test_long_tables_repeat_source_headers_and_keep_complete_rows(self):
        reference = self.root/'many_rows.pdf'
        with fitz.open(self.pdf) as pdf:
            pdf[0].insert_text((40, 90), '10 10 10 10 10')
            pdf.save(reference)
        self.docs = from_inputs([reference], self.run)
        text = '<table><tr><th>Group</th><th>N</th></tr>' + ''.join(
            f'<tr><td>Arm {label}</td><td>{20 if label == "G" else 10}</td></tr>' for label in 'ABCDEFG') + '</table>'
        self.prepare(Parser(text), pages=[1])
        chunks, summary, _ = pc.build(self.run, self.counter, {'table_max_tokens': 130})
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(c['n_tokens'] <= 130 and c['text'].startswith('<table><tr><th>') and c['text'].endswith('</table>') for c in chunks))
        self.assertEqual(sum(c['text'].count('<tr><td>') for c in chunks), 7)
        self.assertTrue(all(c['structure']['table_id'] == chunks[0]['structure']['table_id'] for c in chunks))
        self.assertEqual(summary['source_verification']['units_covered'], 1)
        self.assertTrue(all(c['provenance']['source_spans'][0]['role'] == 'repeated_context' for c in chunks))

    def test_oversized_row_is_deferred_whole_without_cutting_cells(self):
        text = '<table><tr><th>Group</th><th>N</th></tr><tr><td>'+'A'*150+'</td><td>10 20</td></tr></table>'
        self.prepare(Parser(text), pages=[1])
        chunks, summary, excluded = pc.build(self.run, self.counter, {'table_max_tokens': 130})
        self.assertEqual(chunks, [])
        self.assertEqual(excluded[0]['reasons'], ['oversized_table_row_or_header'])

    def test_oversized_merged_table_is_deferred_but_fitting_table_is_unchanged(self):
        html = '<table><tr><th>Group</th><th>N</th></tr><tr><td rowspan="2">A</td><td>10</td></tr><tr><td>20</td></tr></table>'
        parts = pc.table_parts(html, self.counter, 1000, '', 8192)
        self.assertEqual(parts[0].fragments[0].text, html)
        with self.assertRaisesRegex(pc.DeferredTable, 'merged'):
            pc.table_parts(html, self.counter, 80, '', 8192)

    def test_prose_sections_split_without_study_boundary_overlap_and_preserve_ranges(self):
        parser = Parser()
        texts = ['2.3. Clinical aspects', 'Study CT-P43 1.1', 'Methods', 'old trial body '*8,
                 'Study CT-P43 1.2', 'Methods', 'new trial body '*8]
        def parse(image):
            from PIL import Image
            with Image.open(image) as img:
                width, height = img.size
            return {'res': {'width': width, 'height': height, 'parsing_res_list': [
                {'block_label': 'text', 'block_order': i, 'block_content': text,
                 'block_bbox': [20, 20+i*65, width-20, 70+i*65]} for i, text in enumerate(texts)]}}
        parser.parse = parse
        self.docs[0].metadata['product_id'] = 'CTP43'
        self.prepare(parser, pages=[1])
        chunks, _, _ = pc.build(self.run, self.counter, {'max_tokens': 100, 'target_tokens': 75, 'overlap_tokens': 15})
        self.assertFalse(any('old trial' in c['text'] and 'Study CT-P43 1.2' in c['text'] for c in chunks))
        old = next(c for c in chunks if 'old trial' in c['text'])
        new = next(c for c in chunks if 'new trial' in c['text'])
        self.assertNotEqual(old['structure']['section_id'], new['structure']['section_id'])
        self.assertEqual(new['structure']['heading_studies'], ['ST_CTP43_1_2'])
        self.assertEqual(new['studies'], ['ST_CTP43_1_2'])
        self.assertEqual(new['study_attribution_status'],'heuristic_unverified')
        self.assertNotIn('ST_CTP43_1_1',new['studies'])
        self.assertTrue(any(a['quote']=='CT-P43 1.2' and a['basis']=='section_heading'
            for a in new['study_candidates']['anchors']))
        self.assertTrue(all(c['provenance']['offset_basis'] == 'preprocessing_representation_unit' for c in chunks))
        validate_dataset(chunks, self.counter)

    def test_build_is_deterministic_and_does_not_modify_preprocessing(self):
        self.prepare(pages=[1])
        before = sha256(self.run/'source_units.jsonl')
        first, _, _ = pc.build(self.run, self.counter)
        second, _, _ = pc.build(self.run, self.counter)
        self.assertEqual(first, second)
        self.assertEqual(sha256(self.run/'source_units.jsonl'), before)
        altered = deepcopy(first)
        altered[0]['text'] += ' fabricated'
        run, _, _, units, assets, pages, _ = pc.load_input(self.run)
        with self.assertRaisesRegex(ValueError, 'reconstruct'):
            pc.verify_ranges(altered, units, assets, pages)

    def test_headings_inside_one_source_block_keep_exact_offsets_and_analysis_boundaries(self):
        text=('Study CT-P43 1.1\nExclusion criteria\nParticipants were excluded for active infection.\n'
              'Treatments\nA single 45 mg dose was administered.\nPrimary analysis\n'
              'The primary comparison was reported.\nPost-hoc analysis\nThe adjusted comparison was reported.')
        parser=Parser()
        def parse(image):
            from PIL import Image
            with Image.open(image) as img:width,height=img.size
            return {'res':{'width':width,'height':height,'parsing_res_list':[
                {'block_label':'text','block_order':0,'block_content':text,
                 'block_bbox':[20,20,width-20,height-20]}]}}
        parser.parse=parse
        self.docs[0].metadata['product_id']='CTP43'
        self.prepare(parser,pages=[1])
        before=sha256(self.run/'source_units.jsonl')
        chunks,summary,_=pc.build(self.run,self.counter,{'max_tokens':200,'target_tokens':150,'overlap_tokens':20})
        dosing=next(c for c in chunks if '45 mg' in c['text'])
        primary=next(c for c in chunks if 'primary comparison' in c['text'])
        posthoc=next(c for c in chunks if 'adjusted comparison' in c['text'])
        self.assertNotEqual(primary['structure']['section_id'],posthoc['structure']['section_id'])
        self.assertNotIn('active infection',dosing['text'])
        self.assertNotIn('adjusted comparison',primary['text'])
        anchor=next(h for h in dosing['structure']['heading_anchors'] if h['title']=='Treatments')
        location=anchor['heading_location']
        self.assertEqual(text[location['representation_start']:location['representation_end']],'Treatments\n')
        self.assertEqual(summary['source_verification']['status'],'passed')
        self.assertEqual(before,sha256(self.run/'source_units.jsonl'))

    def test_nested_heading_prefixes_share_body_but_sibling_sections_stay_separate(self):
        parser = Parser()
        texts = ['2.6. Clinical aspects', '2.6.1. Pharmacokinetics', 'Methods',
                 'Participants received a single dose.', 'Results', 'Serum concentrations were measured.']
        def parse(image):
            from PIL import Image
            with Image.open(image) as img: width, height = img.size
            return {'res': {'width': width, 'height': height, 'parsing_res_list': [
                {'block_label': 'text', 'block_order': i, 'block_content': text,
                 'block_bbox': [20, 20+i*65, width-20, 70+i*65]} for i, text in enumerate(texts)]}}
        parser.parse = parse
        self.prepare(parser, pages=[1])
        chunks, _, _ = pc.build(self.run, self.counter)
        self.assertEqual(len(chunks), 2)
        self.assertIn('2.6. Clinical aspects', chunks[0]['text'])
        self.assertIn('Participants received', chunks[0]['text'])
        self.assertNotIn('Results', chunks[0]['text'])
        self.assertTrue(chunks[0]['section'].endswith('Methods'))
        self.assertTrue(chunks[1]['section'].endswith('Results'))

    def test_catalog_date_and_final_source_url_reach_chunks(self):
        self.docs[0].metadata.update(source_metadata={'date': '2024-01-02'},
                                     url='https://example.org/old', final_url='https://example.org/final')
        self.prepare(pages=[1])
        chunks, _, _ = pc.build(self.run, self.counter)
        self.assertEqual(chunks[0]['doc_date'], '2024-01-02')
        self.assertEqual(chunks[0]['source_url'], 'https://example.org/final')

    def test_same_page_table_regions_share_parent_without_chunk_id_collision(self):
        path=self.root/'two_regions.pdf'
        with fitz.open() as pdf:
            page=pdf.new_page(width=400,height=400)
            page.insert_text((30,35),'Table 1. Groups')
            for offset in (0,120):
                for x,y,text in ((40,70,'Group'),(150,70,'N'),(40,100,'A'),(150,100,'10'),(40,130,'B'),(150,130,'20')):
                    page.insert_text((x,y+offset),text)
            pdf.save(path)
        self.docs=from_inputs([path],self.run)
        parser=Parser()
        def parse(image):
            from PIL import Image
            with Image.open(image) as img:w,h=img.size
            source=[('table_title',[30,20,300,40],'Table 1. Groups'),
                    ('table',[30,50,250,140],GOOD),('table',[30,170,250,260],GOOD)]
            return {'res':{'width':w,'height':h,'parsing_res_list':[
                {'block_label':label,'block_bbox':[v*(w if j%2==0 else h)/400 for j,v in enumerate(box)],
                 'block_order':i,'block_content':text} for i,(label,box,text) in enumerate(source)]}}
        parser.parse=parse
        self.prepare(parser)
        first,_,_=pc.build(self.run,self.counter)
        tables=[c for c in first if c['structure']['kind']=='table_candidate']
        self.assertEqual(len(tables),2)
        self.assertEqual(tables[0]['structure']['table_id'],tables[1]['structure']['table_id'])
        self.assertNotEqual(tables[0]['chunk_id'],tables[1]['chunk_id'])
        self.assertEqual({s['unit_id'] for c in tables for s in c['provenance']['source_spans'] if s['role']=='content'},
            {u['unit_id'] for u in read_jsonl(self.run/'source_units.jsonl') if u['kind']=='table'})
        second,_,_=pc.build(self.run,self.counter)
        self.assertEqual([c['chunk_id'] for c in first],[c['chunk_id'] for c in second])
        validate_dataset(first,self.counter)

    def test_two_panel_caption_only_parent_restores_panels_and_retains_review(self):
        parser = Parser(figure=True)
        original = parser.parse
        def parse(image):
            raw = original(image)
            w, h = raw['res']['width'], raw['res']['height']
            raw['res']['parsing_res_list'][1]['block_bbox'] = [30*w/400, 160*h/400, 300*w/400, 220*h/400]
            raw['res']['parsing_res_list'] += [
                {'block_label': 'chart', 'block_order': 3, 'block_content': 'Time Value A B Week 12',
                    'block_bbox': [30*w/400, 225*h/400, 300*w/400, 280*h/400]},
                {'block_label': 'figure_title', 'block_order': 4, 'block_content': 'Figure 3. Comparison.',
                    'block_bbox': [30*w/400, 285*h/400, 300*w/400, 300*h/400]}]
            return raw
        parser.parse = parse
        self.prepare(parser, Qwen(truncated=True), pages=[1])
        pc.write(self.run, self.out, self.counter)
        chunks = read_jsonl(self.out/'chunks.jsonl')
        figure = next(c for c in chunks if c['structure']['kind'] == 'figure_candidate')
        restored = pc.visual_context(self.out, figure['chunk_id'])
        self.assertEqual(len(restored['regions']), 2)
        self.assertTrue(restored['description_review_required'])
        self.assertFalse(restored['numeric_evidence_eligible'])
        self.assertEqual([m['panel_index'] for m in restored['regions']], [0, 1])
        self.assertTrue(all(not m['retrieval_eligible'] for m in restored['regions']))
        units, parents, membership = cu.load(self.out)
        visual_windows = [w for w in cu.windows(units, parents, self.counter) if w['context_id'] == figure['structure']['figure_id']]
        self.assertEqual(len(visual_windows), 1)
        self.assertEqual(membership[figure['chunk_id']]['source_ranges'][0]['unit_id'], figure['representation']['unit_ids'][0])

    def test_context_first_export_restores_prose_target_and_enforces_policy_and_hashes(self):
        parser = Parser(figure=True)
        original = parser.parse
        def parse(image):
            raw = original(image)
            w, h = raw['res']['width'], raw['res']['height']
            raw['res']['parsing_res_list'] += [
                {'block_label':'figure_title', 'block_order':3, 'block_content':'Figure 3. Trial chart',
                    'block_bbox':[30*w/400,270*h/400,300*w/400,285*h/400]},
                {'block_label':'text', 'block_order':4, 'block_content':'Concentrations by visit are shown in Figure 3.',
                    'block_bbox':[30*w/400,310*h/400,300*w/400,340*h/400]}]
            return raw
        parser.parse = parse
        self.docs[0].metadata.update(default_search=False, evaluation_cohort='development')
        self.prepare(parser, Qwen(truncated=True), pages=[1])
        pc.write(self.run, self.out, self.counter)
        chunks = read_jsonl(self.out/'chunks.jsonl')
        figure = next(c for c in chunks if c['structure']['figure_id'])
        prose = next(c for c in chunks if c['structure'].get('related_visual_link_ids'))
        self.assertIn('Concentrations by visit', figure['header'])
        self.assertNotIn('Concentrations by visit', figure['text'])
        with self.assertRaisesRegex(ValueError, 'search-excluded'):
            pc.related_visual_contexts(self.out, prose['chunk_id'])
        restored = pc.related_visual_contexts(self.out, prose['chunk_id'], include_nondefault=True)
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]['logical_group_id'], figure['structure']['figure_id'])
        self.assertTrue(any(c['context_role'] == 'referring_sentence' for c in restored[0]['source_context']))
        self.assertFalse(restored[0]['numeric_evidence_eligible'])
        import os
        import subprocess
        import sys
        process = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-m', 'rag.retrieval.search',
            '--mode', 'bm25', '--rag-dir', str(self.out), '--include-nondefault', '--visual-context',
            '--query', 'Concentrations by visit', '--top-k', '1'], cwd=self.root, capture_output=True, text=True, encoding='utf-8',
            env={**os.environ, 'PYTHONPATH': str(pc.bc.ROOT)})
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(json.loads(process.stdout)['results'][0]['visual_contexts'])
        path = self.out/'visual_context_units.jsonl'
        path.write_text(path.read_text(encoding='utf-8')+'\n{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Changed candidate artifact'):
            pc.related_visual_contexts(self.out, prose['chunk_id'], include_nondefault=True)

    def test_deferred_table_cannot_be_restored_via_its_searchable_caption_or_prose(self):
        parser=Parser();original=parser.parse
        def parse(image):
            raw=original(image);w,h=raw['res']['width'],raw['res']['height']
            raw['res']['parsing_res_list'] += [
                {'block_label':'table_title','block_order':3,'block_content':'Table 3. Trial results',
                    'block_bbox':[30*w/400,20*h/400,300*w/400,45*h/400]},
                {'block_label':'text','block_order':4,'block_content':'The results are shown in Table 3.',
                    'block_bbox':[30*w/400,300*h/400,300*w/400,330*h/400]}]
            return raw
        parser.parse=parse;self.prepare(parser,pages=[1])
        with patch.object(pc,'table_bundle',side_effect=pc.DeferredTable('test_table_requires_review')):
            pc.write(self.run,self.out,self.counter)
        chunks=read_jsonl(self.out/'chunks.jsonl')
        self.assertFalse(any(c['structure']['kind']=='table_candidate' for c in chunks))
        caption=next(c for c in chunks if c['structure'].get('table_id'))
        prose=next(c for c in chunks if c['structure'].get('related_visual_link_ids'))
        self.assertIn('Table 3',caption['text'])
        self.assertEqual(pc.related_visual_contexts(self.out,caption['chunk_id']),[])
        self.assertEqual(pc.related_visual_contexts(self.out,prose['chunk_id']),[])

    def test_supported_figure_fields_are_indexed_and_context_keeps_field_restrictions(self):
        parser = Parser(figure=True)
        original = parser.parse
        def parse(image):
            raw = original(image)
            w, h = raw['res']['width'], raw['res']['height']
            raw['res']['parsing_res_list'].append({'block_label': 'figure_title', 'block_order': 3,
                'block_content': 'Figure 3. Trial chart', 'block_bbox': [30*w/400,270*h/400,300*w/400,285*h/400]})
            return raw
        parser.parse = parse
        self.prepare(parser, Qwen(), pages=[1])
        pc.write(self.run, self.out, self.counter)
        figure = next(c for c in read_jsonl(self.out/'chunks.jsonl') if c['structure']['kind'] == 'figure_candidate')
        self.assertIn('Panel 1 x_axis: Time', figure['text'])
        self.assertNotIn(FIGURE['summary'], figure['text'])
        self.assertIn('literal_figure_labels_roles_unverified', figure['quality']['warnings'])
        restored = pc.visual_context(self.out, figure['chunk_id'])
        self.assertEqual(restored['figure_labels']['accepted_fields'], 5)
        self.assertTrue(restored['description_review_required'])
        self.assertFalse(restored['numeric_evidence_eligible'])
        # Tampered field support must fail even after an attacker rehashes artifacts.
        from preprocess.extraction.hybrid_validate import validate_extraction
        from preprocess.files import save_rows, write_json
        rows = read_jsonl(self.run/'extracted_assets.jsonl')
        parent = next(a for a in rows if a.get('is_logical_group'))
        parent['figure_labels']['fields'][0]['evidence']['asset_id'] = 'foreign-panel'
        save_rows(self.run/'extracted_assets.jsonl', rows)
        integrity = json.loads((self.run/'extraction_integrity.json').read_text(encoding='utf-8'))
        integrity['files']['extracted_assets.jsonl'] = sha256(self.run/'extracted_assets.jsonl')
        write_json(self.run/'extraction_integrity.json', integrity)
        with self.assertRaisesRegex(ValueError, 'Figure label projection'):
            validate_extraction(self.run)

    def test_table_bundle_keeps_caption_source_and_physical_cross_page_ranges(self):
        path=self.root/'context.pdf'
        with fitz.open(self.pdf) as pdf:
            pdf[0].insert_text((40,375),'Table 14. Single 45 mg',fontsize=8)
            pdf[1].insert_text((40,35),'or 90 mg administration',fontsize=8)
            pdf[1].insert_text((40,155),'Source data: Trial A',fontsize=8)
            pdf.save(path)
        self.docs=from_inputs([path], self.run)
        parser=Parser()
        def parse(image):
            from PIL import Image
            with Image.open(image) as img:
                w,h=img.size
            parser.calls+=1
            source=[('table_title',[30,360,300,382],'Table 14. Single 45 mg')] if parser.calls==1 else [
                ('figure_title',[30,22,300,40],'or 90 mg administration'),
                ('table',[30,50,250,140],GOOD),
                ('vision_footnote',[30,142,300,162],'Source data: Trial A')]
            return {'res':{'width':w,'height':h,'parsing_res_list':[
                {'block_label':label,'block_bbox':[v*(w if j%2==0 else h)/400 for j,v in enumerate(box)],
                 'block_order':i,'block_content':text} for i,(label,box,text) in enumerate(source)]}}
        parser.parse=parse
        self.prepare(parser)
        original=sha256(self.run/'source_units.jsonl')
        from preprocess.extraction.regroup import regroup
        revised=self.root/'regrouped'
        with patch('urllib.request.urlopen', side_effect=AssertionError('No model calls')):
            updated=regroup(self.run,revised)
        self.assertEqual(updated['new_ocr_calls']+updated['new_vlm_calls'],0)
        self.assertEqual(sha256(self.run/'source_units.jsonl'),original)
        pc.write(revised,self.out,self.counter)
        chunks=read_jsonl(self.out/'chunks.jsonl')
        table=next(c for c in chunks if c['structure']['kind']=='table_candidate')
        self.assertIn('or 90 mg administration',table['structure']['caption'])
        self.assertIn('Source data: Trial A',table['text'])
        self.assertEqual({r['pdf_page'] for r in table['provenance']['source_refs']},{1,2})
        units,parents,_=cu.load(self.out)
        caption=next(uid for uid,role in table['structure']['context_roles'].items() if role=='caption')
        self.assertEqual(units[caption]['pdf_page'],1)
        parent=parents[table['structure']['table_id']]
        self.assertEqual((parent['page_from'],parent['page_to']),(1,2))
        source=next(uid for uid,role in table['structure']['context_roles'].items() if role=='source')
        self.assertEqual(parent['unit_ids'][-1],source)
        restored=pc.visual_context(self.out,table['chunk_id'])
        self.assertEqual([item['role'] for item in restored['printed_context']],['caption','caption_continuation','source'])
        altered=deepcopy(table)
        altered['provenance']['source_spans'][0]['role']='content'
        _,_,_,input_units,assets,pages,_=pc.load_input(revised)
        with self.assertRaisesRegex(ValueError,'boundary|printed context'):
            pc.verify_ranges([altered],input_units,assets,pages,False)
        self.assertFalse(any(c['structure']['kind']=='text' and source in c['representation']['unit_ids'] for c in chunks))
        altered=deepcopy(table)
        altered['provenance']['source_spans']=[s for s in altered['provenance']['source_spans'] if s['unit_id'] != source]
        with self.assertRaisesRegex(ValueError,'printed context'):
            pc.verify_ranges([altered],input_units,assets,pages,False)

    def test_split_table_repeats_source_note_and_defers_missing_context(self):
        first={'unit_id':'table','document_id':'doc','version_id':'v','kind':'table',
            'text':'<table><tr><th>N</th></tr>'+''.join('<tr><td>row '+str(i)+'</td></tr>' for i in range(8))+'</table>',
            'structure':{'table_id':'g','context_unit_ids':['note'],'context_roles':{'note':'source'}}}
        note={'unit_id':'note','document_id':'doc','version_id':'v','kind':'text','text':'Source: Trial A',
              'retrieval_eligible':True,'structure':{'table_id':'g'}}
        args=({'note':note},{'note':{'pdf_page':1}}, {('doc','v',1):{'status':'parsed'}},self.counter,110,'',8192)
        selected,parts=pc.table_bundle(first,*args)
        self.assertGreater(len(parts),1)
        self.assertEqual(sum('row ' in f.text for p in parts for f in p.fragments),8)
        self.assertTrue(all(p.fragments[-1].text=='Source: Trial A' for p in parts))
        self.assertTrue(all(len(''.join(f.text for f in p.fragments))+1<=110 for p in parts))
        note['review_required']=True
        with self.assertRaisesRegex(pc.DeferredTable,'context_unavailable'):
            pc.table_bundle(first,*args)


if __name__ == '__main__':
    unittest.main()
