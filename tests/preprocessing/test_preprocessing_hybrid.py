"""Routing, model failure, source integrity and candidate acceptance boundaries."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz
from PIL import Image

from preprocess.hybrid import pdf_box, read_jsonl, resume_enrichment, run_hybrid
from preprocess.hybrid_config import HybridConfig, local_endpoint, parse_pages
from preprocess.ingest import from_inputs, sha256
from preprocess.qwen_adapter import validate_answer
from preprocess.table_quality import inspect_table
from preprocess.qwen_contracts import PROMPT_VERSIONS

GOOD = '<table><tr><th>Group</th><th>N</th></tr><tr><td>A</td><td>10</td></tr><tr><td>B</td><td>20</td></tr></table>'
BAD = GOOD.replace('>10<', '>1<')
PANEL = {'panel_index':0,'position':'single','x_axis':'Time','y_axis':'Value',
         'x_scale':None,'y_scale':None,'scale_label':None}
FIGURE = {"figure_type": "chart", "panels": [PANEL], "x_axis": "Time", "y_axis": "Value",
          "legend": ["A", "B"], "printed_labels": ["Week 12"], "what_is_compared": "Groups A and B.",
          "summary": "A comparison at Week 12.", "uncertainties": []}


def reply(answer, done_reason="stop"):
    return {"message": {"content": json.dumps(answer)}, "done": True, "done_reason": done_reason,
            "prompt_eval_count": 100, "eval_count": 50}


def table_answer(html):
    quality=inspect_table(html)
    return {'column_count':quality['columns'],'rows':[{'row_index':r,
        'section':'header' if all(c['is_header'] for c in quality['cells'] if c['row']==r) else 'body',
        'cells':[{'column_index':c['column'],'row_span':c['row_span'],'col_span':c['col_span'],
                  'text':c['text'],'state':'visible' if c['text'] else 'blank'}
                 for c in quality['cells'] if c['row']==r]} for r in range(quality['rows'])],
        'caption':None,'footnotes':[],'uncertainties':[]}


class Parser:
    def __init__(self, table=GOOD, figure=False, fail_once=False, identity="parser-1"):
        self.table, self.figure, self.fail_once, self.identity = table, figure, fail_once, identity
        self.calls, self.closed = 0, False

    def fingerprint(self):
        return {"name": self.identity}

    def parse(self, image):
        self.calls += 1
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("Simulated parser failure")
        with Image.open(image) as img:
            width, height = img.size
        sx, sy = width / 400, height / 400
        blocks = [{"block_label": "table", "block_content": self.table,
                   "block_bbox": [30 * sx, 50 * sy, 250 * sx, 140 * sy], "block_order": 1}]
        if self.figure:
            blocks.append({"block_label": "chart", "block_content": "Time Value A B Week 12",
                "block_bbox": [30 * sx, 160 * sy, 300 * sx, 260 * sy], "block_order": 2})
        return {"res": {"width": width, "height": height, "model_settings": {"use_doc_preprocessor": False,
            "use_layout_detection": True}, "parsing_res_list": blocks}}

    def close(self):
        self.closed = True


class Qwen:
    def __init__(self, parser=None, digest="qwen-1", unavailable=False, truncated=False, hallucinated=False):
        self.parser, self.digest, self.unavailable = parser, digest, unavailable
        self.truncated, self.hallucinated = truncated, hallucinated
        self.calls, self.unloaded = [], False

    def fingerprint(self):
        if self.unavailable:
            raise RuntimeError("Model not installed")
        return {"name": "local-qwen", "digest": self.digest}

    def generate(self, task, images, context):
        if self.parser and not self.parser.closed:
            raise AssertionError("Paddle must exit before Qwen generation")
        self.calls.append(task)
        if task == "repair_table":
            answer = table_answer(GOOD.replace('>10<', '>999<') if self.hallucinated else GOOD)
        else:
            answer = dict(FIGURE)
        return reply(answer, "length" if self.truncated else "stop")

    def unload(self):
        self.unloaded = True


class HybridTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path, self.out = self.root / "upload.pdf", self.root / "result"
        with fitz.open() as pdf:
            for _ in range(2):
                page = pdf.new_page(width=400, height=400)
                for x, y, text in ((40, 70, 'Group'), (150, 70, 'N'), (40, 100, 'A'),
                    (150, 100, '10'), (40, 130, 'B'), (150, 130, '20'), (40, 190, 'Time Value A B Week 12')):
                    page.insert_text((x, y), text)
            pdf.save(self.path)
        self.docs = from_inputs([self.path], self.out)
        self.config = HybridConfig()

    def tearDown(self):
        self.temp.cleanup()

    def run_pipeline(self, parser, qwen=None, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_hybrid(self.docs, self.out, self.config, paddle=parser, qwen=qwen,
                              use_qwen=qwen is not None, **kwargs)

    def test_clean_integer_table_never_calls_qwen_and_retains_original(self):
        parser, qwen = Parser(), Qwen()
        with patch('urllib.request.urlopen', side_effect=AssertionError('External network forbidden')):
            summary = self.run_pipeline(parser, qwen)
        self.assertEqual(summary['pages'], 2)
        self.assertEqual(qwen.calls, [])
        self.assertEqual(sha256(self.path), self.docs[0].version_id)
        assets = read_jsonl(self.out / 'extracted_assets.jsonl')
        self.assertFalse(any(a['numeric_evidence_eligible'] for a in assets))
        self.assertTrue(all(a['base']['quality']['issues'] == [] for a in assets))

    def test_one_call_budget_reaches_complete_caption_parent_before_orphan_crop(self):
        class CaptionParser(Parser):
            def parse(self, image):
                with Image.open(image) as img:
                    w, h = img.size
                blocks = []
                for label, text, box in [('chart', 'Uncaptioned fragment', [30, 160, 300, 230]),
                    ('paragraph_title', 'Another subsection', [30, 250, 300, 275]),
                    ('figure_title', 'Figure 1. Time Value', [30, 300, 300, 320]),
                    ('chart', 'Time Value A B Week 12', [30, 340, 300, 390])]:
                    blocks.append({'block_label': label, 'block_content': text,
                        'block_bbox': [v*(w/400 if i%2 == 0 else h/400) for i,v in enumerate(box)],
                        'block_order': len(blocks)})
                return {'res': {'width': w, 'height': h, 'model_settings': {'use_doc_preprocessor': False,
                    'use_layout_detection': True}, 'parsing_res_list': blocks}}
        class TrackingQwen(Qwen):
            def generate(self, task, images, context):
                self.last_context = context
                return super().generate(task, images, context)
        qwen = TrackingQwen()
        result = self.run_pipeline(CaptionParser(), qwen, pages=[1], max_qwen_calls=1)
        self.assertEqual(result['qwen']['attempted_calls'], 1)
        self.assertEqual(qwen.last_context['logical_group']['label'], 'Figure 1')
        pending = [a for a in read_jsonl(self.out/'extracted_assets.jsonl')
                   if (a.get('enrichment') or {}).get('status') == 'pending_budget']
        self.assertTrue(pending)
        self.assertTrue(all(not a.get('is_logical_group') for a in pending))

    def test_number_loss_routes_only_bad_table_and_keeps_both_candidates(self):
        parser = Parser(BAD)
        qwen = Qwen(parser)
        summary = self.run_pipeline(parser, qwen, pages=[1])
        asset = read_jsonl(self.out / 'extracted_assets.jsonl')[0]
        self.assertEqual(qwen.calls, ['repair_table'])
        self.assertEqual(asset['base']['content'], BAD)
        self.assertEqual(asset['selected']['content'], GOOD)
        self.assertEqual(asset['selected']['origin'], 'qwen_table_candidate')
        self.assertEqual(asset['enrichment']['validation']['semantic_status'], 'unverified')
        self.assertFalse(asset['numeric_evidence_eligible'])
        self.assertEqual(summary['qwen']['attempted_calls'], 1)

    def test_figures_use_description_task_instead_of_table_repair(self):
        parser = Parser(figure=True)
        qwen = Qwen(parser)
        self.run_pipeline(parser, qwen, pages=[1])
        self.assertEqual(qwen.calls, ['describe_figure'])
        assets = read_jsonl(self.out / 'extracted_assets.jsonl')
        self.assertEqual(assets[1]['selected']['origin'], 'figure_labels_candidate')
        self.assertNotIn(FIGURE['summary'], assets[1]['selected']['content'])
        self.assertEqual(assets[1]['validation_status'], 'derived_unverified')

    def test_per_document_page_plan_is_persisted_and_resumed(self):
        plan={self.docs[0].document_id:[2]}
        self.run_pipeline(Parser(BAD), Qwen(), pages_by_document=plan)
        self.assertEqual([p['pdf_page'] for p in read_jsonl(self.out/'extraction_pages.jsonl')],[2])
        manifest=json.loads((self.out/'extraction_manifest.json').read_text())
        self.assertEqual(manifest['pages_by_document'],plan)
        with contextlib.redirect_stdout(io.StringIO()):
            result=resume_enrichment(self.out,self.config,adapter=Qwen())
        self.assertEqual(result['qwen']['cache_reused'],1)

    def test_page_plan_cannot_mix_with_common_range_or_other_documents(self):
        with self.assertRaisesRegex(ValueError,'common page range'):
            self.run_pipeline(Parser(),pages=[1],pages_by_document={self.docs[0].document_id:[2]})
        with self.assertRaisesRegex(ValueError,'exactly'):
            self.run_pipeline(Parser(),pages_by_document={'other-upload':[1]})

    def test_figure_schema_rejects_label_flooding(self):
        checked=validate_answer('describe_figure',reply({**FIGURE,'printed_labels':['A']*9}),'','A')
        self.assertIn('invalid_response_schema',checked['issues'])

    def test_figure_parent_survives_resume_without_duplicate_panel_enrichment(self):
        parser=Parser(figure=True)
        original=parser.parse
        def parse(image):
            raw=original(image)
            rows=raw['res']['parsing_res_list']
            figure=rows[1]
            x0,y0,x1,y1=figure['block_bbox']
            rows.append({'block_label':'figure_title','block_content':'Figure 3. Two panels',
                         'block_bbox':[x0,y0-30,x1,y0-15],'block_order':2})
            rows.append({**figure,'block_bbox':[x0,y1+15,x1,y1+70],'block_order':4})
            return raw
        parser.parse=parse
        qwen=Qwen()
        qwen.generate=lambda *args: reply({**FIGURE,'uncertainties':['Scale unclear']})
        self.run_pipeline(parser,qwen,pages=[1])
        units=read_jsonl(self.out/'source_units.jsonl')
        parent=next(u for u in units if u['kind']=='figure_group')
        panels=[u for u in units if u['kind']=='figure']
        self.assertTrue(parent['retrieval_eligible'])
        self.assertTrue(parent['description_review_required'])
        self.assertTrue(all(not u['retrieval_eligible'] and u['parent_unit_id']==parent['unit_id'] for u in panels))
        with contextlib.redirect_stdout(io.StringIO()):
            result=resume_enrichment(self.out,self.config,adapter=Qwen(),max_calls=0)
        self.assertEqual(result['qwen']['cache_reused'],1)
        self.assertEqual(result['qwen']['pending'],0)
        self.assertEqual(len([u for u in read_jsonl(self.out/'source_units.jsonl') if u['kind']=='figure_group']),1)

    def test_hallucinated_repair_does_not_replace_base_table(self):
        self.run_pipeline(Parser(BAD), Qwen(hallucinated=True), pages=[1])
        asset = read_jsonl(self.out / 'extracted_assets.jsonl')[0]
        self.assertEqual(asset['selected']['origin'], 'paddle')
        self.assertIn('numbers_without_textual_support', asset['enrichment']['validation']['issues'])

    def test_truncated_generation_is_retained_but_retried(self):
        self.run_pipeline(Parser(BAD), Qwen(truncated=True), pages=[1])
        self.assertEqual(len(list((self.out / 'qwen_attempts').glob('*.json'))), 1)
        self.assertFalse(list((self.out / 'qwen_raw').glob('*.json')))
        qwen = Qwen()
        with contextlib.redirect_stdout(io.StringIO()):
            summary = resume_enrichment(self.out, self.config, adapter=qwen)
        self.assertEqual(summary['qwen']['attempted_calls'], 1)
        self.assertEqual(len(qwen.calls), 1)

    def test_parser_and_qwen_cache_are_source_bound_and_digest_bound(self):
        self.run_pipeline(Parser(BAD), Qwen(), pages=[1])
        parser, qwen = Parser(BAD), Qwen()
        summary = self.run_pipeline(parser, qwen, pages=[1])
        self.assertEqual(parser.calls, 0)
        self.assertEqual(qwen.calls, [])
        self.assertEqual(summary['qwen']['cache_reused'], 1)
        qwen = Qwen(digest='changed')
        self.run_pipeline(Parser(BAD), qwen, pages=[1])
        self.assertEqual(qwen.calls, ['repair_table'])

    def test_figure_only_prompt_change_reuses_existing_table_cache(self):
        self.run_pipeline(Parser(BAD,figure=True),Qwen(),pages=[1])
        qwen=Qwen()
        with patch.dict(PROMPT_VERSIONS,{'describe_figure':'new-figure-prompt'}):
            result=self.run_pipeline(Parser(BAD,figure=True),qwen,pages=[1])
        self.assertEqual(qwen.calls,['describe_figure'])
        self.assertEqual(result['qwen']['cache_reused'],1)

    def test_previous_table_image_is_context_only_and_same_source(self):
        from preprocess.hybrid import context_for, enrichment_images
        self.run_pipeline(Parser(BAD))
        assets=read_jsonl(self.out/'extracted_assets.jsonl')
        for a in assets:
            a['logical_group_id']='one-table'
        target=assets[1]
        other={**assets[0],'document_id':'other-document'}
        doc=self.docs[0]
        context=context_for(target,[*assets,other],{(doc.document_id,doc.version_id):doc})
        self.assertEqual([r['asset_id'] for r in context['previous_table_regions']],[assets[0]['asset_id']])
        images,meta=enrichment_images(target,doc,self.config.qwen,self.out,context)
        self.assertEqual([p['role'] for p in meta['images']],['target_crop','previous_table_context'])
        self.assertEqual(meta['images'][1]['pdf_page'],1)
        self.assertEqual(meta['images'][1]['source_refs'],assets[0]['source_refs'])
        self.assertEqual(len(images),2)

    def test_modified_checkpoint_is_not_used(self):
        self.run_pipeline(Parser(), pages=[1])
        path = next((self.out / 'paddle_raw').glob('*.json'))
        body = json.loads(path.read_text())
        body['payload']['res']['width'] = 10
        path.write_text(json.dumps(body))
        parser = Parser()
        self.run_pipeline(parser, pages=[1])
        self.assertEqual(parser.calls, 1)

    def test_changed_source_and_modified_saved_assets_are_rejected(self):
        self.run_pipeline(Parser(BAD), Qwen(), pages=[1])
        assets = self.out / 'extracted_assets.jsonl'
        assets.write_text(assets.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            resume_enrichment(self.out, self.config, adapter=Qwen())
        Path(self.docs[0].raw_path).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.run_pipeline(Parser())

    def test_missing_qwen_leaves_resumable_queue_and_parser_outputs(self):
        summary = self.run_pipeline(Parser(BAD), Qwen(unavailable=True), pages=[1])
        self.assertEqual(summary['qwen']['pending'], 1)
        self.assertTrue((self.out / 'source_units.jsonl').exists())
        self.assertEqual(read_jsonl(self.out / 'enrichment_queue.jsonl')[0]['status'], 'pending_model')

    def test_call_budget_is_explicit_and_cached_replies_do_not_consume_it(self):
        qwen = Qwen()
        summary = self.run_pipeline(Parser(BAD), qwen, max_qwen_calls=1)
        self.assertEqual(len(qwen.calls), 1)
        self.assertEqual(summary['qwen']['pending'], 1)
        qwen = Qwen()
        with contextlib.redirect_stdout(io.StringIO()):
            summary = resume_enrichment(self.out, self.config, max_calls=1, adapter=qwen)
        self.assertEqual(summary['qwen']['cache_reused'], 1)
        self.assertEqual(summary['qwen']['attempted_calls'], 1)
        self.assertEqual(summary['qwen']['pending'], 0)

    def test_failed_page_retries_without_rerunning_successful_page(self):
        summary = self.run_pipeline(Parser(fail_once=True))
        self.assertEqual(summary['page_statuses'], {'failed': 1, 'parsed': 1})
        parser = Parser()
        summary = self.run_pipeline(parser)
        self.assertEqual(parser.calls, 1)
        self.assertEqual(summary['parser_cache_reused'], 1)

    def test_rotated_coordinates_map_back_to_unrotated_pdf(self):
        with fitz.open(self.path) as pdf:
            page = pdf[0]
            for rotation in (0, 90, 180, 270):
                page.set_rotation(rotation)
                target = fitz.Rect(30, 50, 250, 140)
                displayed = target * page.rotation_matrix
                image_size = [round(page.rect.width * 2), round(page.rect.height * 2)]
                actual = pdf_box(page, [x * 2 for x in displayed], image_size)
                self.assertEqual(actual, list(target))
            with self.assertRaises(ValueError):
                pdf_box(page, [-1, 0, 10, 20], image_size)

    def test_local_endpoint_and_page_selection_are_strict(self):
        for endpoint in ('https://127.0.0.1:11434', 'http://example.com', 'http://127.0.0.1.evil', 'http://user@localhost'):
            with self.assertRaises(ValueError):
                local_endpoint(endpoint)
        self.assertEqual(parse_pages('1,3-5'), [1, 3, 4, 5])
        self.assertIsNone(parse_pages(None))
        for value in ('0', '5-3', '1,x'):
            with self.assertRaises(ValueError):
                parse_pages(value)

    def test_unprinted_figure_numbers_in_any_field_are_flagged(self):
        answer = {**FIGURE, 'panels': [{**PANEL,'scale_label':'A: n=999'}]}
        checked = validate_answer('describe_figure', reply(answer), 'Time Value A B Week 12', '')
        self.assertIn('999', checked['unsupported_numbers'])
        self.assertIn('numbers_without_textual_support', checked['issues'])

    def test_real_merged_cells_are_preserved_without_invented_cell_boxes(self):
        html = '<table><tr><th>Group</th><th>N</th></tr><tr><td rowspan="2">A</td><td>10</td></tr><tr><td>20</td></tr></table>'
        quality = inspect_table(html)
        self.assertEqual(quality['issues'], [])
        self.assertEqual(quality['cells'][2]['row_span'], 2)
        self.assertTrue(all(c['bbox_pdf'] is None for c in quality['cells']))
        self.assertIn('truncated_html', inspect_table('<table><tr><td>10')['issues'])

    def test_uncertain_figure_description_and_chart_values_are_not_selected(self):
        qwen = Qwen()
        qwen.generate = lambda *args: reply({**FIGURE, 'uncertainties': ['Panel label is unclear.']})
        self.run_pipeline(Parser(figure=True), qwen, pages=[1])
        asset = read_jsonl(self.out / 'extracted_assets.jsonl')[1]
        self.assertIn('Panel 1 x_axis: Time', asset['selected']['content'])
        self.assertNotIn(FIGURE['summary'], asset['selected']['content'])
        self.assertEqual(asset['enrichment']['status'], 'needs_review')
        self.assertTrue(asset['base']['content'])

    def test_malformed_complete_reply_is_retained_and_retried(self):
        qwen = Qwen()
        qwen.generate = lambda *args: {'done': True, 'done_reason': 'stop', 'message': {'content': '{}'}}
        self.run_pipeline(Parser(BAD), qwen, pages=[1])
        self.assertFalse(list((self.out / 'qwen_raw').glob('*.json')))
        self.assertEqual(len(list((self.out / 'qwen_attempts').glob('*.json'))), 1)
        with contextlib.redirect_stdout(io.StringIO()):
            result = resume_enrichment(self.out, self.config, adapter=Qwen())
        self.assertEqual(result['qwen']['attempted_calls'], 1)

    def test_one_bad_crop_does_not_stop_other_assets_or_lose_parser_output(self):
        from preprocess.hybrid import enrichment_images
        calls = 0
        def prepare(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError('Simulated crop failure')
            return enrichment_images(*args)
        with patch('preprocess.hybrid.enrichment_images', side_effect=prepare):
            result = self.run_pipeline(Parser(BAD), Qwen())
        self.assertEqual(result['qwen']['failed'], 1)
        self.assertEqual(result['qwen']['attempted_calls'], 1)
        self.assertEqual(len(read_jsonl(self.out / 'extracted_assets.jsonl')), 2)

    def test_incomplete_integrity_manifest_cannot_bypass_resume_checks(self):
        self.run_pipeline(Parser(), pages=[1])
        (self.out / 'extraction_integrity.json').write_text('{"files": {}}')
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            resume_enrichment(self.out, self.config, adapter=Qwen())

    def test_html_attributes_do_not_ground_printed_figure_numbers(self):
        checked = validate_answer('describe_figure', reply({**FIGURE, 'panels': [{**PANEL,'scale_label':'2'}]}),
            '', '<span colspan="2">Time Value A B Week 12</span>')
        self.assertIn('2', checked['unsupported_numbers'])

    def test_page_continuation_header_requires_context_without_repairing_it(self):
        html = '<table><tr><td rowspan="2">Parameter</td><td>N</td></tr><tr><td>10</td></tr></table>'
        self.assertIn('first_row_spans_entire_table_needs_context', inspect_table(html)['issues'])

    def test_encrypted_upload_does_not_block_other_uploaded_documents(self):
        encrypted = self.root / 'protected.pdf'
        with fitz.open(self.path) as pdf:
            pdf.save(encrypted, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='password')
        self.docs = from_inputs([self.path, encrypted], self.out)
        result = self.run_pipeline(Parser())
        self.assertEqual(result['documents'], 2)
        self.assertEqual(result['processed_documents'], 1)
        self.assertEqual(result['page_statuses'], {'parsed': 2})
        self.assertEqual(len(result['document_errors']), 1)
        manifest = json.loads((self.out / 'extraction_manifest.json').read_text())
        self.assertEqual(manifest['status'], 'partial')

    def test_curve_digitization_in_ocr_output_cannot_support_figure_numbers(self):
        from preprocess.text_checks import figure_label_reference
        text = figure_label_reference('Time Value A B Week 12\n0 | 999\n999\n')
        checked = validate_answer('describe_figure', reply({**FIGURE, 'summary': 'Value 999'}), '', text)
        self.assertEqual(text, 'Time Value A B Week 12')
        self.assertIn('999', checked['unsupported_numbers'])

    def test_render_failure_is_isolated_to_one_page(self):
        original = fitz.Page.get_pixmap
        calls = 0
        def render(page, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError('Simulated rendering failure')
            return original(page, *args, **kwargs)
        with patch.object(fitz.Page, 'get_pixmap', autospec=True, side_effect=render):
            result = self.run_pipeline(Parser())
        self.assertEqual(result['page_statuses'], {'failed': 1, 'parsed': 1})

    def test_invalid_parser_coordinate_contract_is_not_cached_as_success(self):
        parser = Parser()
        original = parser.parse
        def wrong(image):
            raw = original(image)
            raw['res']['width'] = 10
            return raw
        parser.parse = wrong
        result = self.run_pipeline(parser, pages=[1])
        self.assertEqual(result['page_statuses'], {'failed': 1})
        self.assertFalse(list((self.out / 'paddle_raw').glob('*.json')))
        self.assertEqual(len(list((self.out / 'paddle_attempts').glob('*.json'))), 1)
        result = self.run_pipeline(Parser(), pages=[1])
        self.assertEqual(result['parser_calls'], 1)

    def test_upload_report_distinguishes_pending_resume_and_processing_complete(self):
        from preprocess.reporting import build_report
        result = self.run_pipeline(Parser(BAD), pages=[1])
        report = build_report(self.out)
        self.assertEqual(report['state'], 'awaiting_enrichment')
        self.assertEqual(result['processing_state'], 'awaiting_enrichment')
        self.assertTrue(Path(result['processing_report_path']).is_file())
        self.assertEqual(report['documents'][0]['scope'], 'selected_pages')
        self.assertEqual(report['documents'][0]['planned_pages'], 1)
        self.assertEqual(report['documents'][0]['source_page_count'], 2)
        self.assertEqual(report['documents'][0]['qwen_statuses'], {'pending': 1})
        self.assertTrue((self.out/'processing_report.md').exists())
        with contextlib.redirect_stdout(io.StringIO()):
            resume_enrichment(self.out, self.config, adapter=Qwen())
        report = build_report(self.out)
        self.assertEqual(report['state'], 'completed')
        self.assertFalse(report['numeric_evidence_eligible'])
        self.assertFalse(report['accuracy_certified'])
        self.assertEqual(report['documents'][0]['qwen_statuses'], {'heuristic_checks_passed': 1})

    def test_report_retains_review_failure_and_pending_as_distinct_outcomes(self):
        from preprocess.reporting import build_report
        self.run_pipeline(Parser(BAD), Qwen(hallucinated=True), pages=[1])
        self.assertEqual(build_report(self.out)['state'], 'review_required')
        qwen = Qwen(digest='request-failure-probe')
        qwen.generate = lambda *args: (_ for _ in ()).throw(RuntimeError('Model request failed'))
        self.run_pipeline(Parser(BAD), qwen, pages=[1])
        report = build_report(self.out)
        self.assertEqual(report['state'], 'partial_failure')
        self.assertEqual(report['review_items'][0]['job_status'], 'failed')
        self.assertIn('Model request failed', report['review_items'][0]['error'])
        self.run_pipeline(Parser(BAD), Qwen(unavailable=True), pages=[1])
        report = build_report(self.out)
        self.assertEqual(report['state'], 'awaiting_enrichment')
        self.assertEqual(report['review_items'][0]['job_status'], 'pending_model')

    def test_live_status_observes_page_and_qwen_work_without_loading_stale_results(self):
        from preprocess.reporting import build_report
        observed = []
        parser, qwen = Parser(BAD), Qwen()
        original_parse, original_generate = parser.parse, qwen.generate
        def parse(image):
            report = build_report(self.out)
            observed.append(report)
            self.assertEqual(report['state'], 'running')
            self.assertFalse(report['details_available'])
            self.assertEqual(report['progress']['stage'], 'extracting')
            return original_parse(image)
        def generate(*args):
            report = build_report(self.out)
            observed.append(report)
            self.assertEqual(report['progress']['stage'], 'enriching')
            self.assertEqual(report['progress']['qwen']['total'], 1)
            self.assertEqual(report['progress']['current']['task'], 'repair_table')
            return original_generate(*args)
        parser.parse, qwen.generate = parse, generate
        self.run_pipeline(parser, qwen, pages=[1])
        self.assertEqual(len(observed), 2)
        self.assertEqual(build_report(self.out)['state'], 'completed')

    def test_unexpected_failure_cannot_present_previous_success_as_current_completion(self):
        from preprocess.reporting import build_report
        self.run_pipeline(Parser(), pages=[1])
        parser = Parser()
        parser.fingerprint = lambda: (_ for _ in ()).throw(RuntimeError('Runtime missing'))
        with self.assertRaisesRegex(RuntimeError, 'Runtime missing'):
            self.run_pipeline(parser, pages=[1])
        report = build_report(self.out)
        self.assertEqual(report['state'], 'failed')
        self.assertFalse(report['details_available'])
        self.assertIn('Runtime missing', report['error'])
        stored = json.loads((self.out/'processing_report.json').read_text(encoding='utf-8'))
        self.assertEqual(stored['state'], 'failed')
        self.assertEqual(stored['run_id'], report['run_id'])

    def test_report_exposes_mixed_document_errors_and_page_failures(self):
        from preprocess.reporting import build_report
        other = self.root/'broken.pdf'
        other.write_bytes(b'not a PDF')
        self.docs = from_inputs([self.path, other], self.out)
        self.run_pipeline(Parser(fail_once=True))
        report = build_report(self.out)
        self.assertEqual(report['state'], 'partial_failure')
        self.assertEqual([d['state'] for d in report['documents']], ['partial_failure', 'failed'])
        self.assertTrue(all(d['errors'] for d in report['documents']))

    def test_keyboard_interrupt_is_recorded_and_parser_is_closed(self):
        from preprocess.reporting import build_report
        parser = Parser()
        parser.parse = lambda image: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.run_pipeline(parser, pages=[1])
        self.assertTrue(parser.closed)
        report = build_report(self.out)
        self.assertEqual(report['state'], 'failed')
        self.assertIn('KeyboardInterrupt', report['error'])

    def test_caption_retrieval_does_not_hide_pending_figure_description_review(self):
        from preprocess.reporting import build_report
        parser, qwen = Parser(figure=True), Qwen(truncated=True)
        original = parser.parse
        def parse(image):
            raw = original(image)
            width, height = raw['res']['width'], raw['res']['height']
            raw['res']['parsing_res_list'].append({'block_label': 'figure_title',
                'block_content': 'Figure 1. Comparison.', 'block_order': 3,
                'block_bbox': [30*width/400, 265*height/400, 300*width/400, 280*height/400]})
            return raw
        parser.parse = parse
        self.run_pipeline(parser, qwen, pages=[1])
        report = build_report(self.out)
        self.assertEqual(report['state'], 'review_required')
        figures = [r for r in report['review_items'] if r['kind'] == 'figure_group']
        self.assertEqual(len(figures), 1)
        self.assertTrue(figures[0]['retrieval_eligible'])
        self.assertTrue(figures[0]['description_review_required'])
        self.assertEqual(report['documents'][0]['review_items'], 1)


if __name__ == '__main__':
    unittest.main()
