"""Record source-range integrity and assistant source review without an LLM grader."""
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import urllib.request

from eval.ra_pipeline_update_eval import ROOT,read,digest,write

OUT=ROOT/'data/runs/ra_pipeline_completion_2026-10-12_v2'
PRIOR=ROOT/'data/runs/ra_pipeline_completion_2026-10-12_v1'


def main():
    frozen=read(OUT/'candidate.json')
    assert all(digest(ROOT/name)==value for name,value in frozen['code_sha256'].items())
    assert all(digest(ROOT/name)==value for name,value in read(OUT/'baseline.json')['index_sha256'].items())
    reports=[*sorted((OUT/'api').glob('RA*.json')),
        *sorted((OUT/'package_eval').glob('EMA*.json')),OUT/'service/live_report.json']
    rows=[]
    for path in reports:
        report=read(path);result=report['results'][0];evidence=result['evidence'];spans=0
        for c in result['claims']:
            own=set(c['initial_span_ids'])
            assert set(s['citation_alias'] for s in c['sources'])<=own
            for s in c['sources']:
                a,b=s['source_range'];block=evidence[s['evidence']]
                assert block['text'][a:b]==s['quote']
                assert block['document_id'] and block['version_id']
                spans+=1
        rows.append({'id':path.stem,'status':result['status'],'claims':len(result['claims']),
            'citation_instances':spans,'exact_source_ranges':True,
            'inside_own_pre_review_selected_sources':True,'report_sha256':digest(path)})
    notes={
        'RA04':'ABP654 90 mg SC, SB17 45 mg SC preserved. SB17 comparator draft used mixed PK/phase III sources; no unverified expansion. Comparator completeness and trial-condition comparison remain insufficient.',
        'RA10':'Quality and general-principle scope now have same-guideline source paragraphs and selected-source review. Analytical always / clinical may bridging distinction still absent. Reflection-paper scope unavailable in supplied evidence.',
        'RA14':'45 mg SC actual regimen retained; requested 135 mg IV not invented. 18-week total duration has explicit source. Randomized translated as randomly selected remains a wording problem; no semantic-perfect claim.',
        'EMA_UST_PK_013':'Pilot safety facts survive. Full design conditions omitted; Part 2 PK translated as pharmacological equivalence/similarity and heading-only objectives now withheld. Required-fact completeness not achieved.',
        'EMA_UST_PK_014':'Supplied source describes planned 327 subjects and planned randomisation. Actual-enrolment/assignment assertions withheld. Requested healthy Japanese male population and study-centre / pre-dose-weight stratification are unanswered.',
        'EMA_UST_IMMUNOGENICITY_018':'Unsupported analyte assertion withheld. Remaining background study facts do not answer requested assay targets; serum ustekinumab, ADA and NAb are missing.',
        'live_report':'Actual v4.5.1 service: 45 mg, SC, healthy volunteers and SB17-1001 have own exact sources. Original neutral dose/route request independently fulfilled; no exact assertion duplicate.'}
    write(OUT/'source_review.json',{'created_at':datetime.now(timezone(timedelta(hours=9))).isoformat(),
        'reviewer':'assistant_direct_source_comparison','external_llm_grader':False,'expert_certified':False,
        'rows':rows,'source_review_notes':notes,
        'scope':'Source-range integrity passes; answer completeness and general semantic accuracy do not pass universally.'})
    audit=read(OUT/'validation_package_audit.json')
    questions=[json.loads(line) for name in ['qa_gold_indomain_test.jsonl','qa_gold_external_holdout.jsonl']
        for line in (ROOT/'validation_report'/name).read_text(encoding='utf-8').splitlines() if line.strip()]
    package=[]
    for q in questions:
        key=q['question_id'];path=OUT/'package_eval'/(key+'.json')
        if path.exists():
            result=read(path)['results'][0]
            package.append({'id':key,'split':q['split'],'status':result['status'],'executed_runtime_version':'4.5.0',
                'postprocessing_replay_version':'4.5.1','complete_required_facts':0,
                'required_facts':len(q['required_facts']),'assessment':'assistant comparison; not an independently validated score'})
        else:
            package.append({'id':key,'split':q['split'],'status':'corpus_unavailable',
                'missing_annotated_assessment_reports':[d['document_id'] for d in audit['source_documents'] if not d['indexed_chunks']],
                'model_execution':False,'new_api_calls':0})
    assert all(digest(ROOT/'validation_report'/name)==expected for name,expected in
               read(ROOT/'validation_report/release_manifest.json')['files'].items())
    write(OUT/'package_eval/coverage_summary.json',{'rows':package,'package_unchanged':True,
        'independent_holdout':False,'gold_sent':False,
        'limits':['Previously exposed preview; approvals mechanical, not domain-expert.',
            'The three executed cases now serve as diagnostic regression cases, not untouched holdout.',
            'Original runner checked product coverage too broadly; after nine diagnostic calls a Usymro planning attempt was blocked before provider execution by the nine-call cap. No external model call was made.',
            'Runner corrected to require indexed coverage of the package assessment report.']})
    ledger=read(ROOT/'data/runs/model_quota/2026-10-12.json')
    calls=[r for r in ledger['attempts'] if str(PRIOR) in r.get('evaluation_out','')]
    live_reports=[read(PRIOR/'service/live_report.json'),read(OUT/'service/live_report.json')]
    total={'calls':len(calls)+sum(len(r['generation_telemetry']) for r in live_reports),
        'input_tokens':sum(r.get('prompt_tokens',0) for r in calls)+sum(r['usage']['input_tokens'] for r in live_reports),
        'output_tokens':sum(r.get('completion_tokens',0) for r in calls)+sum(r['usage']['output_tokens'] for r in live_reports)}
    write(OUT/'execution_summary.json',{'pipeline_version':'ra-research-rag-4.5.1','model':'gpt-4o-mini',
        'tests':{'ra':191,'boundary_api_service':25,'total':216},'source_rows':rows,
        'new_framework_calls':total,'postprocessing_replay_new_calls':0,
        'retrieval_core_ast_preserved':True,'index_hashes_preserved':True,'service_url':'http://127.0.0.1:8775/',
        'service_runtime_version':'4.5.1','legacy_8777_stopped':True,'full_pipeline_quality_certified':False})
    write(OUT/'service/validation.json',{'version':'ra-research-rag-4.5.1','status':'answered',
        'model':'gpt-4o-mini','actual_framework_calls':3,'source_ranges_checked':True,
        'original_request_coverage':live_reports[-1]['results'][0]['original_request_coverage'],
        'no_duplicate_text':len({c['text'] for c in live_reports[-1]['results'][0]['claims']})==len(live_reports[-1]['results'][0]['claims'])})
    print(json.dumps(total));print('source assertions',sum(r['claims'] for r in rows));print('audit saved')


if __name__=='__main__':main()
