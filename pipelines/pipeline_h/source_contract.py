"""Validate DOM content against original source before final promotion."""
from pathlib import Path
from collections import Counter
from pipelines.pipeline_h.prepare import read,write
from pipelines.pipeline_h.html_source import parse
from preprocess.hybrid import read_jsonl
from preprocess.ingest import sha256

def validate(run,with_chunks=False):
    run=Path(run);intake=read(run/'intake_manifest.json');assets=read_jsonl(run/'extraction/extracted_assets.jsonl')
    by_id={a['asset_id']:a for a in assets};checks=[]
    for d in intake['documents']:
        if d['kind']!='html':continue
        if sha256(d['raw_path'])!=d['version_id']:raise ValueError('HTML original checksum changed')
        source=parse(Path(d['raw_path']).read_text(encoding='utf-8-sig'),d['document_id'],d['version_id'])
        from pipelines.pipeline_h.html_coverage import check
        independent=check(Path(d['raw_path']).read_text(encoding='utf-8-sig'),source['units'])
        if independent['missing_source_text_nodes']:raise ValueError('HTML raw source text node was lost: '+str(independent['missing_source_text_nodes'][:2]))
        for u in source['units']:
            a=by_id.get(u['id'])
            if a is None or a['selected']['content']!=u['text']:raise ValueError('Original DOM content lost: '+u['id'])
            if (a.get('source_structure',{}).get('dom_ref')!=u['dom_ref']
                or a['source_refs'][0].get('html_ref')!=u['dom_ref']):raise ValueError('DOM source range differs')
        checks.append({'document_id':d['document_id'],'source_units':len(source['units']),
            'tables':sum(u['kind']=='table' for u in source['units']),'explicit_non_content_exclusions':len(source['explicit_exclusions']),
            'content_source':'original HTML DOM','metadata_unchanged':True})
        checks[-1]['independent_raw_text_nodes']=independent['expected_nonempty_text_nodes']
    original={d['document_id']:d for d in intake['original_document_contract']}
    for d in intake['documents']:
        previous=original[d['document_id']]
        for key in ('document_id','version_id','kind','raw_path','expected_sha256','metadata'):
            if d[key]!=previous[key]:raise ValueError('Original document metadata changed: '+key)
    coverage={}
    if with_chunks:
        units=read_jsonl(run/'extraction/source_units.jsonl');chunks=read_jsonl(run/'candidate/chunks.jsonl')
        used={uid for c in chunks for uid in c['representation']['unit_ids']}
        exclusions={e['unit_id']:e['reasons'] for e in read_jsonl(run/'candidate/chunk_exclusions.jsonl')}
        for u in units:
            if u['unit_id'] in used:state='indexed'
            elif u['unit_id'] in exclusions:state='explicit_chunk_exclusion'
            elif not u['retrieval_eligible'] or u.get('review_required'):state='explicit_source_review_or_exclusion'
            else:raise ValueError('Source unit lost without accounting: '+u['unit_id'])
            coverage[state]=coverage.get(state,0)+1
        # Check DOM representation character ranges, not phrase substring hits.
        from rag.preprocessed_chunks import verify_ranges
        pages={(p['document_id'],p['version_id'],p['pdf_page']):p for p in read_jsonl(run/'extraction/extraction_pages.jsonl')}
        verify_ranges(chunks,units,by_id,pages,require_full_coverage=False)
    report={'status':'passed','scope':'original DOM units and representation accounting, not full-corpus semantic accuracy',
        'html_documents':len(checks),'dom_source_units':sum(c['source_units'] for c in checks),
        'original_metadata_unchanged':True,'checks':checks,'representation_accounting':coverage,
        'geometry_warning_blocks':len(read_jsonl(run/'extraction/geometry_warnings.jsonl')) if (run/'extraction/geometry_warnings.jsonl').exists() else 0}
    write(run/'source_content_audit.json',report);return report
