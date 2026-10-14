"""Import complete prior documents by integrity and full source-page coverage."""
import json,shutil
from pathlib import Path
import fitz
from .ingest import sha256
from .hybrid import read_jsonl
from .hybrid_validate import validate_extraction


def collect(prior_corpus, plan, out):
    prior=Path(prior_corpus).resolve();out=Path(out).resolve()
    if prior==out:raise ValueError('Selective processing requires a new run')
    permitted={d['version_id']:d for d in plan['documents']}
    protected={d['version_id'] for d in plan['protected_exclusions']}
    result={}
    # Finished workflow reports also cover a boundary kill before corpus state save.
    for report_path in sorted((prior/'documents').glob('*/workflow_report.json')):
        run=report_path.parent
        report=json.loads(report_path.read_text(encoding='utf-8'))
        workflow=json.loads((run/'workflow_manifest.json').read_text(encoding='utf-8'))
        if workflow.get('lifecycle')!='finished' or not report.get('execution_complete'):continue
        source=Path(report['final_extraction_directory'])
        manifest=json.loads((source/'extraction_manifest.json').read_text(encoding='utf-8'))
        if len(manifest['documents'])!=1:raise ValueError('Expected a single-source prior workflow')
        doc=manifest['documents'][0];key=doc['version_id']
        if key in protected:raise ValueError('Protected prior document cannot be reused')
        if key not in permitted:continue
        if doc['document_id']!=permitted[key]['document_id']:raise ValueError('Prior identity differs from plan')
        with fitz.open(doc.get('rendered_path') or doc['raw_path']) as pdf:total=len(pdf)
        pages=read_jsonl(source/'extraction_pages.jsonl')
        if (len(pages)!=total or {p['pdf_page'] for p in pages}!=set(range(1,total+1))
                or any(p['status']!='parsed' or p['version_id']!=key for p in pages)):
            continue
        validate_extraction(source)
        candidate=Path(report['candidate_directory'])
        chunk_stage=workflow['stages']['chunk']
        if any(sha256(candidate/name)!=digest for name,digest in chunk_stage['files'].items()):
            raise ValueError('Prior candidate changed after chunk stage')
        copied=out/'reused_candidates'/key[:20]
        if not copied.exists():shutil.copytree(candidate,copied)
        if any(sha256(copied/name)!=digest for name,digest in chunk_stage['files'].items()):
            raise ValueError('Reused candidate copy differs')
        result[key]={'document_id':doc['document_id'],'product_id':permitted[key]['product_id'],
            'status':'reused_complete_page_extraction','run':str(run),'extraction':str(source),
            'candidate':str(copied),'pages':total,'chunks':report['chunks'],
            'prior_workflow_manifest_sha256':sha256(run/'workflow_manifest.json'),
            'prior_extraction_integrity_sha256':sha256(source/'extraction_integrity.json'),
            'candidate_chunks_sha256':sha256(candidate/'chunks.jsonl')}
    return result
