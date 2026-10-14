"""Adapt H into the existing operational provenance, visual-context and chunk contract."""
import argparse,copy,html,time
from collections import Counter,defaultdict
from pathlib import Path
import fitz
from collection import text_layer
from preprocess.ingest import Document,sha256,stable_hash
from preprocess.hybrid import save_outputs,save_rows,read_jsonl
from preprocess.text_checks import figure_label_reference
from preprocess.table_quality import inspect_table,comparable_text
from preprocess.visual_groups import build_visual_groups,GROUPING_VERSION
from preprocess.hybrid_validate import validate_extraction
from preprocess.html_inventory import inspect_html
from pipelines.pipeline_h.prepare import read,write
from pipelines.pipeline_h.policy import VERSION,candidate_gate,glyph_gate,table_alarms,SUSPECT_GLYPH
from pipelines.pipeline_h.structure import table_html,table_rows,box_top_left,parsed_rows,cell_structure_issues
from pipelines.pipeline_h.metadata_tools import printed_page_candidates

FATAL={'invalid_span','span_outside_table','overlapping_spans','truncated_html','invalid_cell_nesting','invalid_row_end','unclosed_row','nested_table','invalid_html'}

def merge(run):
    states=read(run/'selected_documents.json');selection=read(run/'selection_manifest.json')
    if sha256(run/'selected_documents.json')!=selection['selected_documents_sha256']:raise ValueError('Selected source changed')
    by_doc={s['document']['document_id']:s for s in states};decisions=[]
    for job in selection['jobs']:
        candidate=read(run/'vlm'/(job['id']+'.json'));state=by_doc[job['document_id']]
        if candidate['status']=='complete':
            if job['task']=='table':
                table=parsed_rows(candidate['content']);gate=candidate_gate(table,candidate['content'],job.get('original_table'))
            else:gate=glyph_gate(job['original_text'],candidate['content'])
        else:gate={'accepted':False,'issues':['model_call_failed'],'error':candidate.get('error')}
        decision={'job':job,'gate':gate,'candidate_path':str(run/'vlm'/(job['id']+'.json')),
                  'candidate_sha256':sha256(run/'vlm'/(job['id']+'.json'))}
        decisions.append(decision);state.setdefault('candidates',[]).append(decision)
        if job['action']=='add' and gate['accepted']:
            l,t,r,b=job['bbox'];h=read(run/'layout'/f"{job['document_index']:03d}"/f"{job['pdf_page']:04d}.json")['size'][1]
            state['blocks'].append({'type':'table','ref':job['id'],'page':job['pdf_page'],'bbox':[l,h-b,r,h-t],
                'selected_html':candidate['content'],'source':'PaddleOCR-VL-1.6-0.9B','layer':'body','status':'source_candidate',
                'caption':'','candidate':decision,'cells':[], 'h_added':True})
        elif job['action']!='add':
            block=next(b for b in state['blocks'] if b['ref']==job['block_ref'] and b['page']==job['pdf_page'])
            block.setdefault('h_candidates',[]).append(decision)
            if gate['accepted']:
                if job['action']=='replace' and gate.get('glyph_cell_checks') and not gate.get('structural_alarm_reduction'):
                    # A glyph-only reread must not rewrite correct numerical or
                    # structural cells. Keep the complete candidate for audit,
                    # then apply only source-supported cells at unchanged r/c.
                    block.setdefault('original_cells',copy.deepcopy(block['cells']))
                    for check in gate['glyph_cell_checks']:
                        matches=[c for c in block['cells'] if c['r']==check['r'] and c['c']==check['c']]
                        if len(matches)!=1:raise ValueError('Glyph cell identity is ambiguous')
                        matches[0].update(text=check['content'],status='source_context_reread_candidate')
                    block['h_glyph_context_repaired']=True
                elif job['action']=='replace':block['selected_html']=candidate['content'];block['h_replaced']=True
                else:
                    block.setdefault('original_cells',copy.deepcopy(block['cells']))
                    block['cells'][job['cell_index']]['text']=gate['content'];block['cells'][job['cell_index']]['status']='source_reread_candidate'
            else:block.setdefault('h_review_issues',[]).extend(gate['issues'])
    write(run/'candidate_decisions.json',decisions);return states,decisions

def export(run):
    run=Path(run).resolve();out=run/'extraction';out.mkdir(parents=True,exist_ok=True);start=time.perf_counter()
    states,decisions=merge(run);documents=[Document(**s['document']) for s in states];assets=[];pages=[];invalid=[];dom=[];geometry_warnings=[]
    page_plan=read(run/'validation_page_plan.json') if (run/'validation_page_plan.json').exists() else None
    for di,(state,doc) in enumerate(zip(states,documents)):
        if doc.kind=='html' and (run/'html_dom_preview'/(doc.version_id+'.json')).exists():
            from pipelines.pipeline_h.repair_extraction import append_dom
            source=append_dom(doc,run,pages,assets)
            dom.append({'document_id':doc.document_id,'version_id':doc.version_id,'raw_path':doc.raw_path,
                'raw_sha256':doc.version_id,'nodes':[u['dom_ref'] for u in source['units']],
                'content_primary':'original_html_dom','explicit_exclusions':source['explicit_exclusions']})
            continue
        if doc.kind=='html':
            raw=Path(doc.raw_path).read_text(encoding='utf-8-sig')
            dom.append({'document_id':doc.document_id,'version_id':doc.version_id,'raw_path':doc.raw_path,
                        'raw_sha256':doc.version_id,'nodes':inspect_html(raw),'locator_note':'DOM offsets address original HTML; PDF page addresses registered local rendering'})
        raw_result=read(state['raw_path']);routes={r['page']:r for r in raw_result['result']['routes']}
        with fitz.open(doc.pdf_path) as pdf:
            for number,page in enumerate(pdf,1):
                if page_plan and number not in page_plan[doc.document_id]:continue
                layout=read(run/'layout'/f'{di:03d}'/f'{number:04d}.json');size=layout['pixels'];w,h=page.rect.width,page.rect.height
                own=[b for b in state['blocks'] if b['page']==number];quality,replacements=text_layer.inspect(page)
                page_id=stable_hash([doc.document_id,doc.version_id,number])[:24];errors=[]
                for bi,block in enumerate(own):
                    display_box=box_top_left(block,h,w)
                    box=[round(v,4) for v in (fitz.Rect(display_box)*page.derotation_matrix)]
                    if box[0]>=box[2] or box[1]>=box[3]:
                        from pipelines.pipeline_h.repair_extraction import native_recovery
                        repaired=native_recovery(page,block) if block['type']=='text' else None
                        if repaired:
                            box=repaired;display_box=list(fitz.Rect(box)*page.rotation_matrix)
                            block['geometry_repair']={'raw_bbox_bottom_left':block['bbox'],'native_bbox_top_left':box,'raw_text_unchanged':True}
                        elif block['type']=='text' and len(''.join(block.get('text','').split()))<=12 and abs(block['bbox'][3]-block['bbox'][1])<=.11:
                            geometry_warnings.append({'document_id':doc.document_id,'pdf_page':number,'block':copy.deepcopy(block),
                                'reason':'unresolved_degenerate_glyph_locator','source_pdf':doc.pdf_path,'source_page_image':layout['image'],
                                'content_not_certified':True})
                            continue
                        else:errors.append({'block':block,'reason':'invalid_source_bbox'});continue
                    kind='table' if block['type']=='table' else 'figure' if block['type']=='picture' else 'text'
                    label='table' if kind=='table' else 'chart' if block.get('chart_preserved') else 'figure' if kind=='figure' else {'section_header':'paragraph_title','caption':'caption'}.get(block.get('label'),block.get('label','text'))
                    # Marginal furniture is retained in raw/assets and separately excluded by the existing chunker.
                    if block.get('furniture') and kind=='text':label='page_footer' if box[1]>h*.7 else 'page_header'
                    content=block.get('selected_html') or table_html(block) if kind=='table' else block.get('ocr_text',block.get('text','')) if kind=='figure' else block.get('text','')
                    native=text_layer.normalize(page.get_text('text',clip=fitz.Rect(box)),replacements)
                    reliable=bool(native.strip()) and not quality['needs_ocr'] and not text_layer.private_use_count(native)
                    aid='h'+stable_hash([VERSION,doc.document_id,doc.version_id,number,block['ref'],box])[:28]
                    origin='paddle_h_table_candidate' if block.get('h_added') or block.get('h_replaced') else 'docling_structure_source_candidate'
                    engine='PaddleOCR-VL-1.6-0.9B/table' if origin.startswith('paddle') else 'Heron/TableFormer accurate + ra_prep rules' if kind=='table' else 'Heron + RapidOCR/PP-OCRv6 + ra_prep rules'
                    if block.get('h_glyph_context_repaired'):
                        origin='docling_with_paddle_glyph_source_candidate';engine+=' + PaddleOCR-VL-1.6-0.9B/table-context glyph reread'
                    redaction_reference=native if reliable else '[redacted]' if block.get('redacted') else ''
                    tq=inspect_table(content,source_redaction_text=redaction_reference) if kind=='table' else None;issues=list(block.get('h_review_issues',[]))
                    if block.get('status')=='needs_review':issues.append('original_ocr_block_needs_review')
                    if kind=='table':
                        issues+=sorted(FATAL & set(tq['issues']))
                        issues+=sorted({'source_redactions_require_review','source_redaction_marker_not_preserved','rowspan_in_redacted_table_requires_review',
                                       'redacted_cells_require_review','unreadable_cells'} & set(tq['issues']))
                        if not block.get('h_replaced'):issues+=cell_structure_issues(block)
                        if table_alarms(parsed_rows(content))['issues']:issues+=['unresolved_multiple_value_groups']
                        if SUSPECT_GLYPH.search(comparable_text(tq['text'])):issues+=['ambiguous_special_glyph']
                        if any(c.get('status')=='needs_review' for c in block.get('cells',[])):issues+=['original_ocr_cell_needs_review']
                    route={'task':None,'next_stage':'review' if issues else 'complete','reasons':sorted(set(issues)) or ['H_source_extraction_selected']}
                    px=[round(v*(size[0]/w if i%2==0 else size[1]/h),4) for i,v in enumerate(display_box)]
                    asset={'schema_version':'ra-extraction-1','asset_id':aid,'document_id':doc.document_id,'version_id':doc.version_id,
                           'pdf_page':number,'asset_type':kind,'block_label':label,'block_order':bi,'bbox_pdf':box,
                           'bbox_input_px':px,'input_image_size':size,'coordinate_space':'pymupdf_unrotated_crop_points_top_left',
                           'source_refs':[doc.source_ref(number,box)],'page_image':layout['image'],
                           'base':{'engine':engine,'origin':origin,'content':content,'quality':tq,
                                   'printed_text_reference':figure_label_reference(native if reliable else content) if kind=='figure' else None,
                                   'printed_literal_engine':'PyMuPDF source region' if kind=='figure' and reliable else engine,'figure_ocr_error':None},
                           'native_reference':{'text':native,'reliable':reliable},'route':route,'enrichment':None,
                           'selected':{'origin':'figure_labels_candidate' if kind=='figure' else origin,'content':figure_label_reference(content) if kind=='figure' else content},
                           'validation_status':'requires_review' if issues else 'extracted_unverified','numeric_evidence_eligible':False,
                           'docling_structure':copy.deepcopy(block),'h_candidates':block.get('h_candidates',[]),
                           'original_extraction_path':state['raw_path'],'document_metadata_ref':str(run/'intake_manifest.json')}
                    if kind=='table' and (block.get('h_replaced') or block.get('h_glyph_context_repaired') or block.get('original_cells')):
                        primary=copy.deepcopy(block)
                        if primary.get('original_cells'):primary['cells']=primary['original_cells']
                        primary.pop('selected_html',None)
                        asset['primary_candidate']={'engine':'Heron/TableFormer accurate + ra_prep rules',
                            'content':table_html(primary),'source_path':state['raw_path'],'source_block_ref':block['ref'],
                            'coordinate_space':'original Docling block/cell boxes are bottom-left displayed-page points',
                            'semantic_status':'unverified source extraction; retained before H corrections'}
                    if kind=='figure':
                        asset['visual_classification']={'kind':'chart' if block.get('chart_preserved') else 'picture',
                            'decorative_candidate':(display_box[2]-display_box[0])<w*.12 and (display_box[3]-display_box[1])<h*.12 and not block.get('caption'),
                            'status':'layout_source_candidate; not manually adjudicated'}
                        target=out/'visual_crops'/f'{aid}.png';target.parent.mkdir(parents=True,exist_ok=True)
                        page.get_pixmap(dpi=300,clip=fitz.Rect(display_box),alpha=False).save(target)
                        asset['source_visual']={'crop_path':str(target),'crop_sha256':sha256(target),'dpi':300,'meaning_generation':False}
                    assets.append(asset)
                if not own and routes[number]['route']!='BLANK':errors.append({'reason':'nonblank_page_without_extracted_blocks'})
                from pipelines.pipeline_h.caption_recovery import recover as recover_captions
                local=[a for a in assets if (a['document_id'],a['version_id'],a['pdf_page'])==(doc.document_id,doc.version_id,number)]
                assets.extend(recover_captions(page,local,doc))
                pages.append({'page_id':page_id,'document_id':doc.document_id,'version_id':doc.version_id,'pdf_page':number,
                    'pdf_page_label':page.get_label() or str(number),'status':'parsed' if not errors else 'partial',
                    'physical_pdf_page':number,'pdf_catalog_page_label':page.get_label() or None,
                    'printed_page_candidates':printed_page_candidates(own,h,w) if doc.kind=='pdf' else [],
                    'coverage':'not_certified_exhaustive','original_rotation':page.rotation,'image_path':layout['image'],'image_size':size,
                    'raw_result_path':state['raw_path'],'layout_result_path':str(run/'layout'/f'{di:03d}'/f'{number:04d}.json'),
                    'invalid_blocks':errors,'docling_route':routes[number]})
                invalid.extend(errors)
        print(f'EXPORT {di+1}/92 assets={len(assets)}',flush=True)
    groups,parents=build_visual_groups(assets);assets+=parents
    for group in groups:
        if group.get('caption_origin')=='paddle':group['caption_origin']='docling_source_transcription'
    for parent in parents:
        parent['base']['engine']='H source caption/region assembly (no generative description)'
        parent['route']={'task':None,'next_stage':'complete','reasons':['printed_figure_context_only']}
        parent['validation_status']='extracted_unverified'
    summary={'pipeline':VERSION,'documents':len(documents),'pages':len(pages),'assets':len(assets),
             'by_asset_type':dict(Counter(a['asset_type'] for a in assets)),'vlm_calls':len(decisions),
             'accepted_candidates':sum(d['gate']['accepted'] for d in decisions),'invalid_blocks':len(invalid),
             'figure_meaning_generation':False,'numeric_evidence_eligible':False,'export_seconds':time.perf_counter()-start}
    # The old writer invokes a Qwen-oriented figure projection. Substitute the
    # literal-only H projection for this invocation, while preserving its schema
    # and context writer. The original module/file is unchanged.
    from preprocess import figure_labels
    from pipelines.pipeline_h.figure_literals import apply as literal_figures
    original_apply=figure_labels.apply
    try:
        figure_labels.apply=literal_figures
        save_outputs(out,assets,pages,documents,summary,groups)
    finally:figure_labels.apply=original_apply
    save_rows(out/'html_dom_inventory.jsonl',dom)
    save_rows(out/'geometry_warnings.jsonl',geometry_warnings)
    save_rows(out/'document_structures.jsonl',[{'document_id':d.document_id,'version_id':d.version_id,'original_path':d.raw_path,
        'raw_extraction_path':s['raw_path'],'blocks':read(s['raw_path'])['result']['blocks'],
        'suppressed_table_candidates':s['suppressed_table_candidates'],'visual_context_regions':s['visual_context_regions']} for d,s in zip(documents,states)])
    manifest={'pipeline_version':VERSION,'parser':read(run/'docling_runtime.json'),'config':read(run.parent.parent/'config.local.json'),
              'documents':[d.to_dict() for d in documents],'document_errors':[], 'pages':None,'status':'partial' if invalid else 'extraction_complete',
              'grouping_version':GROUPING_VERSION,'selection_manifest_sha256':sha256(run/'selection_manifest.json'),
              'source_metadata_contract':'original document/version/catalog fields retained; physical source refs and raw structure alongside selected representation'}
    write(out/'extraction_manifest.json',manifest)
    if page_plan:manifest['validation_only_page_plan']=page_plan;write(out/'extraction_manifest.json',manifest)
    write(out/'processing_status.json',{'lifecycle':'finished','state':'partial' if invalid else 'complete','documents':len(documents),'pages':len(pages)})
    report=validate_extraction(out);write(out/'validation_report.json',report)
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);a=p.parse_args();print(export(a.run))
