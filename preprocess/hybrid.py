"""Generic PDF upload -> Paddle -> selective Qwen -> versioned reviewable assets.

Inference results are transcription/interpretation candidates. No stage silently
turns these into verified numeric evidence or modifies the active RAG index.
"""
from collections import Counter
from dataclasses import asdict
import io
import json
from pathlib import Path
import time

import fitz
from PIL import Image
from collection import text_layer

from .ingest import Document, sha256, stable_hash
from .paddle_adapter import PaddleAdapter
from .qwen_adapter import QwenAdapter, task_fingerprint, validate_answer
from .table_quality import QUALITY_VERSION, REDACTION, inspect_table, route_asset
from .text_checks import figure_label_reference
from .visual_groups import GROUPING_VERSION, build_visual_groups
from .reporting import Progress
from .files import cache_read, cache_write, read_jsonl, save_rows, write_json
from .ingest import assert_sources
from .outputs import save_outputs

PIPELINE_VERSION = "paddle-qwen-2"
FIGURE_LABELS = {"image", "chart", "picture", "figure"}
CONTEXT_LABELS = {"figure_title", "caption", "footnote", "vision_footnote", "table_title"}


def unwrap_result(value, image_size):
    value = value.get("res", value)
    if not isinstance(value, dict) or not isinstance(value.get("parsing_res_list"), list):
        raise ValueError("Invalid Paddle full-pipeline JSON")
    if (value.get("width"), value.get("height")) != tuple(image_size):
        raise ValueError("Paddle output dimensions differ from the exact rendered input")
    settings = value.get("model_settings", {})
    if settings.get("use_doc_preprocessor"):
        raise ValueError("Transformed page coordinates require an explicit inverse transform")
    if settings.get("use_layout_detection") is False:
        raise ValueError("The full Paddle pipeline must include layout detection")
    return value


def pdf_box(page, box, image_size):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError("Paddle block needs an image-space bounding box")
    box = [float(x) for x in box]
    width, height = image_size
    if not all(__import__("math").isfinite(v) for v in box) or not 0 <= box[0] < box[2] <= width or not 0 <= box[1] < box[3] <= height:
        raise ValueError("Paddle block is outside the input image")
    displayed = fitz.Rect(box[0] * page.rect.width / width, box[1] * page.rect.height / height,
                          box[2] * page.rect.width / width, box[3] * page.rect.height / height)
    result = displayed * page.derotation_matrix
    return [round(x, 4) for x in result]


def native_region(page, box):
    quality, replacements = text_layer.inspect(page)
    text = text_layer.normalize(page.get_text("text", clip=fitz.Rect(box)), replacements)
    reliable = not quality["needs_ocr"] and not text_layer.private_use_count(text) and bool(text.strip())
    return text, reliable


def normalize_page(doc, page, image_path, image_size, raw, parser_key):
    result = unwrap_result(raw, image_size)
    records, invalid = [], []
    for index, block in enumerate(result["parsing_res_list"]):
        try:
            label = block["block_label"]
            content = block["block_content"]
            if not isinstance(label, str) or not isinstance(content, str):
                raise ValueError("Invalid Paddle block label/content")
            box = pdf_box(page, block["block_bbox"], image_size)
            kind = "table" if label == "table" else "figure" if label in FIGURE_LABELS else "text"
            figure_ocr = raw.get("ra_figure_ocr", {}).get(str(index))
            printed = figure_label_reference((figure_ocr or {}).get("text", content if not result.get("model_settings", {}).get("use_chart_recognition") else ""))
            native, reliable = native_region(page, box)
            quality = inspect_table(content, native, reliable) if kind == "table" else None
            route = route_asset(kind, quality, content)
            native_source=raw.get('ra_native_text',{}).get('block_sources',{}).get(str(index))
            light_proof=raw.get('ra_light_ocr',{}).get(str(index))
            if light_proof is not None or native_source:
                from .visual_router import decide
                route=decide(kind,quality,native_complete=bool(native_source),
                    light=(light_proof or {}).get('ocr'),complexity=(light_proof or {}).get('grid',{}).get('complexity'))
            asset_id = "x" + stable_hash([doc.document_id, doc.version_id, page.number + 1, index, parser_key])[:28]
            records.append({"schema_version": "ra-extraction-1", "asset_id": asset_id,
                "document_id": doc.document_id, "version_id": doc.version_id, "pdf_page": page.number + 1,
                "asset_type": kind, "block_label": label, "block_order": block.get("block_order"),
                "bbox_pdf": box, "bbox_input_px": block["block_bbox"], "input_image_size": list(image_size),
                "coordinate_space": "pymupdf_unrotated_crop_points_top_left",
                "source_refs": [doc.source_ref(page.number+1,box)], "page_image": str(image_path),
                "base": {"engine": "PyMuPDF/native" if native_source else "PP-OCR-mobile" if light_proof else "PaddleOCR-VL-1.6",
                         "origin": native_source["origin"] if native_source else "model_transcription", "content": content,
                         **({'native_text_provenance':native_source} if native_source else {}),
                         **({'light_ocr_provenance':light_proof} if light_proof else {}),
                         "quality": quality, "printed_text_reference": printed if kind == "figure" else None,
                         "figure_ocr_error": (figure_ocr or {}).get("error")},
                "native_reference": {"text": native, "reliable": reliable},
                "route": route, "enrichment": None,
                "selected": {"origin": "pending_figure_description" if kind == "figure" else native_source["origin"] if native_source else "paddle",
                             "content": "" if kind == "figure" else content},
                "validation_status": "pending_enrichment" if route["task"] else "extracted_unverified",
                "numeric_evidence_eligible": False})
        except (KeyError, ValueError, TypeError) as exc:
            invalid.append({"block_index": index, "error": str(exc), "block": block})
    from .native_visual_context import recover
    records.extend(recover(doc, page, records, image_path, image_size))
    return records, invalid


def context_for(asset, assets, documents):
    doc = documents[(asset["document_id"], asset["version_id"])]
    nearby = [a for a in assets if a["document_id"] == asset["document_id"] and a["version_id"] == asset["version_id"]
              and abs(a["pdf_page"] - asset["pdf_page"]) <= 1 and a["block_label"] in CONTEXT_LABELS]
    if asset["route"]["task"] == "describe_figure":
        group_id = asset.get("logical_group_id")
        nearby = [a for a in assets if a["document_id"] == asset["document_id"] and a["version_id"] == asset["version_id"]
            and a["pdf_page"] == asset["pdf_page"] and a["asset_id"] != asset["asset_id"]
            and (a.get("logical_group_id") == group_id and group_id is not None
                 and a["asset_type"] == "text" or not group_id and a["block_label"] in CONTEXT_LABELS)]
    context = {"document_id": doc.document_id, "version_id": doc.version_id,
               "target_page": asset["pdf_page"], "context_items": [], "adjacent_native_pages": [],
               "context_truncated": False}
    if asset.get('group_label'):
        lookup = {a['asset_id']:a for a in assets}
        bounds = asset['bbox_input_px']
        width, height = bounds[2]-bounds[0], bounds[3]-bounds[1]
        context['logical_group'] = {"label":asset['group_label'],"layout_region_count":len(asset['member_asset_ids']),
            "scope":"entire_caption_anchored_figure", 'panel_regions':[
                {'panel_index':i, 'asset_id':identifier, 'source_refs':lookup[identifier]['source_refs'],
                 'bbox_relative_1000':[round(1000*(v-bounds[j%2])/(width if j%2==0 else height),1)
                    for j,v in enumerate(lookup[identifier]['bbox_input_px'])]}
                for i,identifier in enumerate(asset['member_asset_ids'])]}
    if asset['route']['task']=='repair_table':
        context['redactions_may_be_present'] = bool(REDACTION.search(
            asset['base']['quality']['text']+'\n'+asset['native_reference']['text']))
        context['table_layout'] = {'column_count_hint':asset['base']['quality']['columns'],
            'source_contains_spans':any(c['row_span']>1 or c['col_span']>1 for c in asset['base']['quality']['cells']),
            'hint_status':'paddle_structure_candidate'}
        group_id = asset.get('logical_group_id')
        context['previous_table_regions'] = [{key:a[key] for key in
            ('asset_id','pdf_page','bbox_pdf','bbox_input_px','input_image_size','source_refs')}
            for a in assets if group_id is not None and a.get('logical_group_id')==group_id
            and a['asset_type']=='table' and a['document_id']==asset['document_id']
            and a['version_id']==asset['version_id'] and a['pdf_page']==asset['pdf_page']-1][:1]
        if context['previous_table_regions']:
            nearby = [a for a in nearby if a.get('logical_group_id')==group_id
                      or a['pdf_page']==asset['pdf_page'] and a['block_label'] in {'footnote','vision_footnote'}]
    # These are context candidates, not certified caption/footnote associations.
    budget = 6000
    for row in sorted(nearby, key=lambda a: (abs(a["pdf_page"] - asset["pdf_page"]), a["pdf_page"])):
        text = row["base"]["content"]
        if len(text) > budget:
            context["context_truncated"] = True
        context["context_items"].append({"asset_id": row["asset_id"], "pdf_page": row["pdf_page"],
            "label": row["block_label"], "text": text[:budget], "bbox_pdf": row["bbox_pdf"],
            "bbox_input_px": row["bbox_input_px"], "source_refs": row["source_refs"],
            "association": "context_candidate"})
        budget -= min(len(text), budget)
        if not budget:
            break
    with fitz.open(doc.pdf_path) as pdf:
        for number in ((asset["pdf_page"] - 1, asset["pdf_page"] + 1) if asset["route"]["task"]=='repair_table' else ()):
            if 1 <= number <= len(pdf):
                text, reliable = native_region(pdf[number - 1], [0, 0, pdf[number - 1].cropbox.width, pdf[number - 1].cropbox.height])
                context["adjacent_native_pages"].append({"pdf_page": number, "text": text[:2000], "reliable": reliable,
                    "source_ref": {"path": doc.raw_path, "sha256": doc.version_id, "pdf_page": number,
                                   "origin": "native_context_only"}})
                context["context_truncated"] |= len(text) > 2000
    return context


def enrichment_images(asset, doc, settings, out, context=None):
    with fitz.open(doc.pdf_path) as pdf:
        page = pdf[asset["pdf_page"] - 1]
        pix = page.get_pixmap(dpi=settings.crop_dpi, alpha=False)
        overview = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    sx, sy = overview.width / asset["input_image_size"][0], overview.height / asset["input_image_size"][1]
    x0, y0, x1, y1 = asset["bbox_input_px"]
    expansions = []
    # Include small panel titles immediately above the plot, not the whole figure's caption.
    if asset["asset_type"] == "figure":
        target = asset["bbox_pdf"]
        for item in (context or {}).get("context_items", []):
            box = item["bbox_pdf"]
            if (item["pdf_page"] == asset["pdf_page"] and item["label"] == "figure_title"
                and 0 <= target[1] - box[1] <= 32 and box[3] <= target[1]
                and min(target[2], box[2]) > max(target[0], box[0])):
                title = item["bbox_input_px"]
                x0, y0, x1 = min(x0, title[0]), min(y0, title[1]), max(x1, title[2])
                expansions.append(item["asset_id"])
    padding = settings.crop_padding_px
    clip = [max(0, int(x0 * sx) - padding), max(0, int(y0 * sy) - padding),
            min(overview.width, int(x1 * sx + 1) + padding), min(overview.height, int(y1 * sy + 1) + padding)]
    crop = overview.crop(clip)
    images, paths = [], []
    views = [('crop',crop,'target_crop',settings.max_image_edge,asset,clip,[pix.width,pix.height])]
    previous = (context or {}).get('previous_table_regions', [])
    if previous:
        with fitz.open(doc.pdf_path) as pdf:
            for region in previous:
                prior_pix = pdf[region['pdf_page']-1].get_pixmap(dpi=settings.crop_dpi,alpha=False)
                prior = Image.open(io.BytesIO(prior_pix.tobytes('png'))).convert('RGB')
                pw,ph = region['input_image_size']
                pb = [v*(prior.width/pw if i%2==0 else prior.height/ph) for i,v in enumerate(region['bbox_input_px'])]
                pc = [max(0,int(pb[0])-padding),max(0,int(pb[1])-padding),
                      min(prior.width,int(pb[2]+1)+padding),min(prior.height,int(pb[3]+1)+padding)]
                views.append((f'previous_p{region["pdf_page"]}',prior.crop(pc),'previous_table_context',
                              min(settings.max_image_edge,1200),region,pc,[prior.width,prior.height]))
    else:
        views.append(('overview',overview,'page_overview',min(settings.max_image_edge,1024),asset,
                      [0,0,pix.width,pix.height],[pix.width,pix.height]))
    for name, img, role, edge, region, image_clip, render_size in views:
        img.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        data = buffer.getvalue()
        path = out / "crops" / f"{asset['asset_id']}_{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        images.append(data)
        paths.append({"path": str(path), "sha256": sha256(path), "width": img.width, "height": img.height,
            'role':role,'pdf_page':region['pdf_page'],'source_refs':region['source_refs'],
            'crop_box_render_px':image_clip,'render_size':render_size})
    return images, {"render_dpi": settings.crop_dpi, "crop_box_render_px": clip,
                    "target_bbox_input_px": asset["bbox_input_px"], "included_title_candidates": expansions,
                    "render_size": [pix.width, pix.height], "images": paths}


def enrich_assets(assets, documents, out, config, adapter=None, max_calls=None, progress=None, only_asset_ids=None):
    if max_calls is not None and (type(max_calls) is not int or max_calls < 0):
        raise ValueError("Qwen call limit must be a nonnegative integer")
    assert_sources(documents)
    out = Path(out).resolve()
    adapter = adapter or QwenAdapter(config.qwen)
    # Caption parents are appended after physical regions. A call budget must
    # not starve complete figures behind many small uncaptioned diagram crops.
    wanted = sorted([a for a in assets if a["route"]["task"]
        and (only_asset_ids is None or a['asset_id'] in only_asset_ids)], key=lambda a: (
        a['route']['task'] != 'repair_table', not a.get('is_logical_group', False),
        a['document_id'], a['pdf_page'], a.get('block_order', 0) or 0, a['asset_id']))
    completed_statuses = Counter()
    def checkpoint(asset=None):
        if asset is not None:
            completed_statuses[(asset.get('enrichment') or {}).get('status', 'pending')] += 1
        if progress:
            progress.enrichment(len(wanted), completed_statuses)
    checkpoint()
    if not wanted:
        return {"attempted_calls": 0, "cache_reused": 0, "pending": 0, "failed": 0}
    try:
        fingerprint = adapter.fingerprint()
    except Exception as exc:
        for asset in wanted:
            asset["enrichment"] = {"status": "pending_model", "error": f"{type(exc).__name__}: {exc}"}
            checkpoint(asset)
        return {"attempted_calls": 0, "cache_reused": 0, "pending": len(wanted), "failed": 0, "model_error": str(exc)}
    doc_lookup = {(d.document_id, d.version_id): d for d in documents}
    stats = {"attempted_calls": 0, "cache_reused": 0, "pending": 0, "failed": 0}
    candidates = []
    try:
        for asset in wanted:
            task = asset["route"]["task"]
            if progress:
                progress.enrichment(len(wanted), completed_statuses,
                    {'document_id': asset['document_id'], 'pdf_page': asset['pdf_page'],
                     'asset_id': asset['asset_id'], 'task': task})
            try:
                context = context_for(asset, assets, doc_lookup)
                images, image_meta = enrichment_images(asset, doc_lookup[(asset["document_id"], asset["version_id"])], config.qwen, out, context)
            except Exception as exc:
                asset["enrichment"] = {"status": "failed", "task": task,
                    "error": f"Image/context preparation: {type(exc).__name__}: {exc}"}
                stats["failed"] += 1
                checkpoint(asset)
                continue
            model_contract = task_fingerprint(fingerprint, task)
            from .visual_task_inputs import input_state
            bound_state=input_state(asset,assets,documents,context)
            context['image_roles'] = [{'image_index':i+1,'role':p['role'],'pdf_page':p['pdf_page']}
                                     for i,p in enumerate(image_meta['images'])]
            key = stable_hash([model_contract, task, context, image_meta,
                               bound_state])
            path = out / "qwen_raw" / f"{key}.json"
            reply_path = path
            reply = cache_read(path, key)
            if reply is not None and "invalid_response_schema" in validate_answer(task, reply, "", "")["issues"]:
                reply = None
            if reply is not None:
                stats["cache_reused"] += 1
            elif max_calls is not None and stats["attempted_calls"] >= max_calls:
                asset["enrichment"] = {"status": "pending_budget", "task": task}
                stats["pending"] += 1
                checkpoint(asset)
                continue
            else:
                stats["attempted_calls"] += 1
                print(f"Qwen {task}: {asset['document_id']} p{asset['pdf_page']}", flush=True)
                try:
                    reply = adapter.generate(task, images, context)
                    # Retain every attempted reply, including malformed/truncated output.
                    attempt_path = out / "qwen_attempts" / f"{key}_{time.time_ns()}.json"
                    reply_path = attempt_path
                    write_json(attempt_path, {"model": model_contract, "task": task, "image_metadata": image_meta,
                                             "context": context, "reply": reply})
                    if (reply.get("done") and reply.get("done_reason") != "length"
                        and "invalid_response_schema" not in validate_answer(task, reply, "", "")["issues"]):
                        cache_write(path, key, reply)
                except Exception as exc:
                    asset["enrichment"] = {"status": "failed", "task": task, "error": f"{type(exc).__name__}: {exc}"}
                    write_json(out / "qwen_attempts" / f"{key}_{time.time_ns()}.json", asset["enrichment"])
                    stats["failed"] += 1
                    checkpoint(asset)
                    continue
            native = asset["native_reference"]["text"] if asset["native_reference"]["reliable"] else ""
            parsed = asset["base"]["quality"]["text"] if task == "repair_table" else asset["base"].get("printed_text_reference", "")
            if task == "describe_figure":
                parsed += "\n" + "\n".join(item["text"] for item in context["context_items"]
                    if item["asset_id"] in image_meta["included_title_candidates"])
            checked = validate_answer(task, reply, native, parsed, context)
            if asset.get('continuation_status') == 'data_fragment_continuation_candidate':
                checked['issues'] = sorted(set(checked['issues'] + ['continuation_semantics_require_review']))
            asset["enrichment"] = {"status": "needs_review" if checked["issues"] else "heuristic_checks_passed",
                "task": task, "model": model_contract, "raw_response_path": str(reply_path),
                "image_metadata": image_meta, "context": context, "validation": checked,
                "input_tokens": reply.get("prompt_eval_count"), "output_tokens": reply.get("eval_count")}
            asset["validation_status"] = "derived_unverified"
            if task == "repair_table" and not checked["issues"]:
                asset["selected"] = {"origin": "qwen_table_candidate", "content": checked["answer"]["table_html"]}
            elif task == "describe_figure" and checked["answer"] and not checked["issues"]:
                asset["selected"] = {"origin": "qwen_figure_description", "content": json.dumps(checked["answer"], ensure_ascii=False)}
            candidates.append({"asset_id": asset["asset_id"], **asset["enrichment"]})
            checkpoint(asset)
    finally:
        try:
            adapter.unload()
        except Exception as exc:
            stats["unload_warning"] = str(exc)
    assert_sources(documents)
    save_rows(out / "qwen_candidates.jsonl", candidates)
    return stats


def run_hybrid(documents, out, config, pages=None, use_qwen=True, max_qwen_calls=None, paddle=None, qwen=None,
               pages_by_document=None):
    from .ingest import assert_model_execution_allowed
    assert_model_execution_allowed(documents)
    progress = Progress(out, documents, 'process')
    try:
        result = _run_hybrid(documents, out, config, pages, use_qwen, max_qwen_calls, paddle, qwen,
                             pages_by_document, progress)
        report = progress.finish()
        return {**result, 'processing_state': report['state'],
            'processing_status_path': str(progress.out/'processing_status.json'),
            'processing_report_path': str(progress.out/'processing_report.md')}
    except BaseException as exc:
        try:
            progress.fail(exc)
        except Exception as report_exc:
            exc.add_note(f'Could not save failure status: {report_exc}')
        raise


def _run_hybrid(documents, out, config, pages=None, use_qwen=True, max_qwen_calls=None, paddle=None, qwen=None,
                pages_by_document=None, progress=None):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    assert_sources(documents)
    config.validate()
    if pages is not None and pages_by_document is not None:
        raise ValueError("Choose a common page range or an explicit per-document page plan")
    if pages_by_document is not None and set(pages_by_document) != {d.document_id for d in documents}:
        raise ValueError("Per-document page plan must cover exactly the selected documents")
    if max_qwen_calls is not None and max_qwen_calls < 0:
        raise ValueError("Qwen call limit must be nonnegative")
    backend = paddle or PaddleAdapter(config.paddle, out)
    started = time.perf_counter()
    readable, document_errors = [], []
    for doc in documents:
        try:
            with fitz.open(doc.pdf_path) as pdf:
                if pdf.needs_pass:
                    raise ValueError("Password-protected PDF")
                if not len(pdf):
                    raise ValueError("PDF has no pages")
                planned = pages_by_document[doc.document_id] if pages_by_document is not None else pages
                if planned is not None and (not planned or len(set(planned)) != len(planned)
                    or any(type(p) is not int or not 1 <= p <= len(pdf) for p in planned)):
                    raise ValueError("Requested physical page is outside the PDF")
                source_page_count = len(pdf)
            readable.append(doc)
            progress.plan(doc, source_page_count, planned)
        except Exception as exc:
            document_errors.append({"document_id": doc.document_id, "version_id": doc.version_id,
                                    "error": f"{type(exc).__name__}: {exc}"})
            progress.plan(doc, None, error=f'{type(exc).__name__}: {exc}')
    fingerprint = backend.fingerprint() if readable else {"skipped": "No readable PDF input"}
    parser_key = stable_hash([PIPELINE_VERSION, fingerprint, fitz.VersionBind])
    legacy_parser_key=None
    if isinstance(backend,PaddleAdapter) and config.paddle.parser_backend=='paddle_vl' and config.paddle.native_text and config.paddle.vl_rec_batch_size==1:
        from copy import deepcopy
        legacy=deepcopy(fingerprint);legacy.pop('native_text',None)
        legacy_parser_key=stable_hash([PIPELINE_VERSION,legacy,fitz.VersionBind])
    manifest = {"pipeline_version": PIPELINE_VERSION, "config": config.to_dict(),
        "documents": [d.to_dict() for d in documents], "document_errors": document_errors,
        "pages": pages, "parser": fingerprint, "status": "running"}
    manifest['status_run_id'] = progress.value['run_id']
    if pages_by_document is not None:
        manifest["pages_by_document"] = pages_by_document
    write_json(out / "extraction_manifest.json", manifest)
    if isinstance(backend, PaddleAdapter) and config.paddle.release_qwen_before_parser:
        try:
            QwenAdapter(config.qwen).unload()
        except Exception as exc:
            # Ollama may be offline while a parser-only job is running.
            manifest["qwen_release_warning"] = str(exc)
    assets, page_rows, calls, reused = [], [], 0, 0
    try:
        for doc in readable:
            with fitz.open(doc.pdf_path) as pdf:
                planned = pages_by_document[doc.document_id] if pages_by_document is not None else pages
                selected = planned if planned is not None else list(range(1, len(pdf) + 1))
                for number in selected:
                    progress.page(doc, number)
                    page_id = stable_hash([doc.document_id, doc.version_id, number])[:24]
                    image_path = out / "page_images" / f"{page_id}.png"
                    row = {"page_id": page_id, "document_id": doc.document_id, "version_id": doc.version_id,
                           "pdf_page": number, "status": "failed", "coverage": "not_certified_exhaustive"}
                    try:
                        page_started = time.perf_counter()
                        page = pdf[number - 1]
                        image_path.parent.mkdir(parents=True, exist_ok=True)
                        pix = page.get_pixmap(dpi=config.paddle.dpi, alpha=False)
                        image_path.write_bytes(pix.tobytes("png"))
                        key = stable_hash([parser_key, doc.version_id, number, sha256(image_path)])
                        raw_path = out / "paddle_raw" / f"{key}.json"
                        row.update(original_rotation=page.rotation, image_path=str(image_path),
                                   image_size=[pix.width, pix.height], raw_result_path=str(raw_path))
                        raw = cache_read(raw_path, key)
                        if raw is None and legacy_parser_key:
                            legacy_key=stable_hash([legacy_parser_key,doc.version_id,number,sha256(image_path)])
                            legacy=cache_read(out/'paddle_raw'/f'{legacy_key}.json',legacy_key)
                            if legacy is not None:
                                from .native_text import packet,project
                                raw=project(legacy,packet(page,[pix.width,pix.height]),cached=True)
                                raw['ra_native_text']['legacy_cache_key']=legacy_key
                                cache_write(raw_path,key,raw)
                        row["cache_reused"] = raw is not None
                        if raw is None:
                            if isinstance(backend,PaddleAdapter) and config.paddle.native_text:
                                from .native_page import parse as native_page
                                raw=native_page(page,[pix.width,pix.height])
                            if raw is None:
                                calls += 1
                                print(f"Paddle: {doc.document_id} p{number}/{len(pdf)}", flush=True)
                                raw = backend.parse_page(image_path,page) if isinstance(backend,PaddleAdapter) else backend.parse(image_path)
                            else:
                                print(f"Native prose: {doc.document_id} p{number}/{len(pdf)}",flush=True)
                            try:
                                unwrap_result(raw, [pix.width, pix.height])
                            except Exception as exc:
                                write_json(out / "paddle_attempts" / f"{key}_{time.time_ns()}.json",
                                           {"error": str(exc), "raw": raw})
                                raise
                            cache_write(raw_path, key, raw)
                        else:
                            reused += 1
                        records, invalid = normalize_page(doc, page, image_path, [pix.width, pix.height], raw, parser_key)
                        # Empty results on a text-bearing page cannot be silently accepted.
                        if not records and page.get_text().strip():
                            raise ValueError("Empty parser output on a nonempty text page")
                        assets.extend(records)
                        row.update(status="partial" if invalid else "parsed", invalid_blocks=invalid,
                                   asset_count=len(records), native_page_text=page.get_text("text"),
                                   elapsed_seconds=round(time.perf_counter() - page_started, 3),
                                   runtime_metrics=raw.get("ra_runtime"))
                    except Exception as exc:
                        row["error"] = f"{type(exc).__name__}: {exc}"
                    page_rows.append(row)
                    progress.page(doc, number, row['status'])
    finally:
        backend.close()
    # Save the parser stage before invoking enrichment so a failed model cannot lose it.
    assert_sources(documents)
    groups, group_jobs = build_visual_groups(assets)
    physical_asset_count = len(assets)
    assets.extend(group_jobs)
    manifest['grouping_version'] = GROUPING_VERSION
    summary = {"pipeline_version": PIPELINE_VERSION, "quality_version": QUALITY_VERSION,
        "documents": len(documents), "processed_documents": len(readable), "document_errors": document_errors,
        "pages": len(page_rows), "page_statuses": dict(Counter(p["status"] for p in page_rows)),
        "assets": len(assets), "physical_assets":physical_asset_count, "logical_groups":len(groups),
        "grouping_version":GROUPING_VERSION,"asset_types": dict(Counter(a["asset_type"] for a in assets)),
        "parser_calls": calls, "parser_cache_reused": reused,
        "qwen": {"attempted_calls": 0, "cache_reused": 0, "pending": sum(bool(a["route"]["task"]) for a in assets), "failed": 0},
        "elapsed_seconds": round(time.perf_counter() - started, 3), "numeric_evidence_eligible": 0,
        "scope_notice": "Model transcriptions and derived candidates; semantic and numeric evidence remain unverified."}
    save_outputs(out, assets, page_rows, documents, summary, groups)
    write_json(out / "extraction_manifest.json", manifest)
    # Parser process has exited before the first Qwen generation.
    stats = enrich_assets(assets, documents, out, config, qwen, max_qwen_calls, progress) if use_qwen else {
        "attempted_calls": 0, "cache_reused": 0, "pending": sum(bool(a["route"]["task"]) for a in assets), "failed": 0}
    if not use_qwen:
        progress.enrichment(stats['pending'], {'pending': stats['pending']} if stats['pending'] else {})
    summary.update(qwen=stats, elapsed_seconds=round(time.perf_counter() - started, 3))
    save_outputs(out, assets, page_rows, documents, summary, groups)
    manifest["status"] = "partial" if document_errors or any(p["status"] != "parsed" for p in page_rows) or stats["pending"] or stats["failed"] else "extraction_complete"
    write_json(out / "extraction_manifest.json", manifest)
    return summary


def resume_enrichment(out, config, max_calls=None, adapter=None, only_pending=False):
    out = Path(out).resolve()
    # Verify before replacing an earlier operation's status or touching its selections.
    from .hybrid_validate import validate_extraction
    validate_extraction(out)
    manifest = json.loads((out / 'extraction_manifest.json').read_text(encoding='utf-8'))
    documents = [Document(**d) for d in manifest['documents']]
    from .ingest import assert_model_execution_allowed
    assert_model_execution_allowed(documents)
    progress = Progress(out, documents, 'enrich')
    try:
        result = _resume_enrichment(out, config, max_calls, adapter, progress, only_pending)
        report = progress.finish()
        return {**result, 'processing_state': report['state'],
            'processing_status_path': str(progress.out/'processing_status.json'),
            'processing_report_path': str(progress.out/'processing_report.md')}
    except BaseException as exc:
        try:
            progress.fail(exc)
        except Exception as report_exc:
            exc.add_note(f'Could not save failure status: {report_exc}')
        raise


def _resume_enrichment(out, config, max_calls=None, adapter=None, progress=None, only_pending=False):
    out = Path(out).resolve()
    config.validate()
    manifest = json.loads((out / "extraction_manifest.json").read_text(encoding="utf-8"))
    documents = [Document(**d) for d in manifest["documents"]]
    manifest['status_run_id'] = progress.value['run_id']
    manifest['status'] = 'running'
    write_json(out / 'extraction_manifest.json', manifest)
    prior_pages = read_jsonl(out / 'extraction_pages.jsonl')
    for doc in documents:
        requested = manifest.get('pages_by_document', {}).get(doc.document_id, manifest.get('pages'))
        try:
            with fitz.open(doc.pdf_path) as pdf:
                total = len(pdf)
        except Exception:
            total = None
        progress.plan(doc, total, requested)
        progress.documents[(doc.document_id, doc.version_id)]['page_counts'] = dict(Counter(
            p['status'] for p in prior_pages if (p['document_id'], p['version_id']) == (doc.document_id, doc.version_id)))
    original = read_jsonl(out / "extracted_assets.jsonl")
    preserved = {a['asset_id']:a for a in original if (a.get('enrichment') or {}).get('status')
        in {'heuristic_checks_passed','needs_review'}} if only_pending else {}
    if only_pending and manifest.get('config',{}).get('qwen')!=config.to_dict()['qwen']:
        raise ValueError('Pending-only enrichment requires the original model configuration')
    assets = [a for a in original if not a.get('is_logical_group')]
    # Re-evaluate selection; stale model candidates never survive a changed configuration.
    for asset in assets:
        if asset['asset_id'] in preserved:continue
        figure = asset["asset_type"] == "figure"
        if figure:
            asset["base"]["printed_text_reference"] = figure_label_reference(asset["base"].get("printed_text_reference", ""))
        asset["selected"] = {"origin": "pending_figure_description" if figure else
                             asset['base']['origin'] if asset['base']['origin'] in {'native_text_layer','native_table_grid'} else "paddle",
                             "content": "" if figure else asset["base"]["content"]}
        asset["enrichment"] = None
        if asset["asset_type"] == "table":
            native = asset["native_reference"]
            asset["base"]["quality"] = inspect_table(asset["base"]["content"], native["text"], native["reliable"])
        asset["route"] = route_asset(asset["asset_type"], asset["base"]["quality"], asset["base"]["content"])
        from .visual_router import reapply
        reapply(asset)
        asset["validation_status"] = "pending_enrichment" if asset["route"]["task"] else "extracted_unverified"
    groups, group_jobs = build_visual_groups(assets)
    physical_asset_count = len(assets)
    assets.extend(group_jobs)
    for asset in assets:
        previous=preserved.get(asset['asset_id'])
        if previous and asset.get('is_logical_group'):
            if not all(previous.get(k)==asset.get(k) for k in ('document_id','version_id','source_refs','member_asset_ids','base')):
                preserved.pop(asset['asset_id']);continue
            for k in ('selected','enrichment','validation_status'):asset[k]=previous[k]
    remaining={a['asset_id'] for a in assets if a['asset_id'] not in preserved}
    stats = enrich_assets(assets, documents, out, config, adapter, max_calls, progress,
        only_asset_ids=remaining if only_pending else None)
    stats['preserved_prior_results']=len(preserved)
    summary = json.loads((out / "extraction_summary.json").read_text(encoding="utf-8"))
    summary["qwen"] = stats
    summary["quality_version"] = QUALITY_VERSION
    summary.update(grouping_version=GROUPING_VERSION,logical_groups=len(groups),assets=len(assets),
                   physical_assets=physical_asset_count,asset_types=dict(Counter(a['asset_type'] for a in assets)))
    save_outputs(out, assets, read_jsonl(out / "extraction_pages.jsonl"), documents, summary, groups)
    manifest["config"] = config.to_dict()
    manifest['grouping_version'] = GROUPING_VERSION
    manifest["status"] = "partial" if summary.get("document_errors") or stats["pending"] or stats["failed"] or any(k != "parsed" for k in summary["page_statuses"]) else "extraction_complete"
    write_json(out / "extraction_manifest.json", manifest)
    return summary
