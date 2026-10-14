"""Export the authored AI source review; no provider calls or production writes."""
from datetime import datetime,timezone
import json
from pathlib import Path
from eval.ra_structure_compare_2026_10_12 import live_state
from eval.ra_workflow_update_eval import audit,digest
from rag import ra_quantities,ra_table_citations

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'data/runs/ra_structure_development_2026-10-12_v1'
FINAL=RUN/'eval_verified'
DOC=ROOT/'docs/rag/RA_STRUCTURE_DEVELOPMENT_2026-10-12'
PRIOR=ROOT/'data/runs/ra_structure_compare_2026-10-12_v1'


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def main():
    if DOC.with_suffix('.json').exists():raise ValueError('Export already exists')
    protocol,summary=read(FINAL/'protocol.json'),read(FINAL/'summary.json')
    annotations=read(DOC.with_suffix('.annotations.json'))
    assert summary['all_cases_present_without_process_error']
    assert all(summary[k] for k in ('frozen_code_unchanged','frozen_indices_unchanged','live_configuration_unchanged'))
    assert live_state()==protocol['live_before']
    assert all(digest(ROOT/n)==s for k in ('code_sha256','index_sha256') for n,s in protocol[k].items())
    assert {c['id'] for c in protocol['cases']}=={c['case'] for c in annotations['cases']}
    reports={}
    for spec in protocol['cases']:
        path=FINAL/(spec['id']+'.json');report=read(path);result=report['results'][0]
        assert audit(report)['exact_quote_ranges'] and audit(report)['own_review_source_boundary']
        relations=ra_table_citations.bindings(result['evidence'])
        clauses=[]
        for c in result['claims']:
            assert ra_quantities.check(c,result['evidence']) is None
            assert ra_table_citations.check(c,result['evidence'],relations) is None
            if c.get('initial_sources'):
                assert {s['citation_span_id'] for s in c['sources']} <= {s['citation_span_id'] for s in c['initial_sources']}
            sources=[]
            for s in c['sources']:
                e=result['evidence'][s['evidence']];start,end=s['source_range']
                assert e['text'][start:end]==s['quote']
                sources.append({**s,'document_id':e['document_id'],'version_id':e['version_id'],
                                'pdf_page':e['pdf_page'],'review_links':e.get('review_links',[])})
            clauses.append({'id':c['id'],'text':c['text'],'sources':sources})
        reports[spec['id']]={'path':str(path.relative_to(ROOT)),'sha256':digest(path),'status':result['status'],
            'question':report['question'],'claims':clauses,'audit':audit(report),
            'coverage':result['question_requirement_coverage'],'review_input':result['validation']['review_input'],
            'provider_executions':report['development_verification'],
            'rejected_claims':result['validation']['rejected_items']}
    proof=read(PRIOR/'source_review/render_provenance.json')
    for page in proof['pages']:
        assert digest(Path(page['source']))==page['sha256']
        assert digest(Path(page['rendered_png']))==page['rendered_png_sha256']
    runs={name:read(RUN/name/'summary.json') for name in ('eval_v1','eval_v2','eval_v3','eval_final','eval_release','eval_verified')}
    review={'created_at':datetime.now(timezone.utc).isoformat(),'review':annotations,'reports':reports,
            'protocol_sha256':digest(FINAL/'protocol.json'),'summary_sha256':digest(FINAL/'summary.json'),
            'annotations_sha256':digest(DOC.with_suffix('.annotations.json')),
            'markdown_sha256':digest(DOC.with_suffix('.md')),'review_export_code_sha256':digest(Path(__file__)),
            'verification_runs':{n:{k:s[k] for k in ('new_provider_calls','input_tokens','output_tokens','seconds',
              'all_cases_present_without_process_error')} for n,s in runs.items()},
            'all_development_new_provider_calls':sum(s['new_provider_calls'] for s in runs.values()),
            'pdf_source_review':proof,'production_configuration':live_state(),'new_provider_calls_for_export':0,
            'independent_accuracy_evaluation':False,'human_expert_certified':False,
            'literal_table_quantity_and_boundary_checks_passed':True,
            'checks_certify_complete_semantic_accuracy':False}
    DOC.with_suffix('.json').write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'reports':len(reports),'source_boundaries':'passed','production_unchanged':True,
                      'new_provider_calls':0,'output':str(DOC.with_suffix('.json'))}))


if __name__=='__main__':main()
