"""Verify the live deployment and isolated review-store restart, no model calls."""
from copy import deepcopy
from http.server import ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
from threading import Thread
import requests
from rag_service.__main__ import Jobs,handler
from eval.ra_today_eval import ROOT

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    out=ROOT/'data/runs/ra_today_deployment_2026-10-12_v1'
    before=json.loads((out/'before.json').read_text(encoding='utf-8'))
    original=json.loads((out/'before_job.json').read_text(encoding='utf-8'))
    session=requests.Session();session.trust_env=False;url='http://127.0.0.1:8775'
    summary=session.get(url+'/api/summary',timeout=10).json()
    assert summary['pipeline_version']=='ra-research-rag-4.8.2' and summary['answer_model']=='gpt-5.6-luna'
    assert summary['retrieval_readiness']['ready'] and summary['retrieval_readiness']['provider_calls']==0
    assert summary['excluded_guideline_documents']==['GL:FDA-SCIENTIFIC-CONSIDERATIONS-2015']
    assert len([d for d in summary['documents'] if d.get('role')=='guideline'])==20
    restored=session.get(url+'/api/jobs/'+before['id'],timeout=10).json()
    assert restored==original
    assert sha(ROOT/'data/runs/ra_service_reports'/(before['id']+'.json'))==before['archive_sha256']
    loaded=session.get(url+'/api/jobs/'+before['id']+'/review',timeout=10);assert loaded.status_code==200
    records=session.get(url+'/api/jobs?limit=2&offset=0',timeout=10).json()
    assert len(records['jobs'])<=2 and records['total']>=1
    assets={}
    for path in ['/','/app.js','/review-notes.js','/style.css']:
        response=session.get(url+path,timeout=10);response.raise_for_status()
        assert 'script-src \'self\'' in response.headers['Content-Security-Policy']
        local=ROOT/'rag_service/static'/({'/':'index.html'}.get(path,path[1:]))
        assert response.content==local.read_bytes();assets[path]=sha(local)
    report=json.loads((ROOT/'data/runs/ra_today_completion_2026-10-12_v3/E04.json').read_text(encoding='utf-8'))
    identity='f'*32;job={'id':identity,'question':report['question'],'created_at':0,'state':'completed','report':report,
        'origin':'isolated_functional_verification_fixture'}
    archive=out/'fixture_reports';archive.mkdir(exist_ok=True)
    job_path=archive/(identity+'.json')
    if job_path.exists():raise ValueError('Do not repeat review writes automatically')
    job_path.write_text(json.dumps(job,ensure_ascii=False),encoding='utf-8');job_sha=sha(job_path)
    class FixturePipeline:
        def summary(self):return {'new_provider_calls':0}
    jobs=Jobs(FixturePipeline(),archive,out/'fixture_reviews')
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(jobs));thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    fixture_url=f'http://127.0.0.1:{server.server_port}/api/jobs/{identity}/review'
    try:
        empty=session.get(fixture_url,timeout=10).json();assert not empty['revisions']
        q=report['review_memo']['meeting_questions'][0]
        payload={'expected_revision':0,'status':'needs_work','note':'기능 검증용 기록: 원래 답변과 별도 저장.',
            'meeting_questions':[{'issue_id':q['issue_id'],'question_ko':q['question_ko']+' 적용 조건을 명시해 주세요.'}]}
        first=session.post(fixture_url,json=payload,timeout=10);first.raise_for_status()
        conflict=session.post(fixture_url,json=payload,timeout=10);assert conflict.status_code==409
        payload.update(expected_revision=1,status='checked',note='기능 검증용 기록: 수정 이력 보존 확인.')
        second=session.post(fixture_url,json=payload,timeout=10);second.raise_for_status()
        record=second.json();assert len(record['revisions'])==2
        assert record['revisions'][0]['note']=='기능 검증용 기록: 원래 답변과 별도 저장.'
        assert sha(job_path)==job_sha and jobs.get(identity)==job
    finally:
        jobs.close();server.shutdown();server.server_close();thread.join()
    restarted=Jobs(FixturePipeline(),archive,out/'fixture_reviews')
    try:
        assert restarted.reviews.get(restarted.get(identity))==record
        assert restarted.get(identity)['report']==report
    finally:restarted.close()
    protocol=json.loads((ROOT/'data/runs/ra_today_completion_2026-10-12_v3/protocol.json').read_text(encoding='utf-8'))
    assert all(sha(ROOT/name)==digest for name,digest in protocol['code_sha256'].items())
    result={'live_pipeline_version':summary['pipeline_version'],'model':summary['answer_model'],
        'actual_assets_verified':assets,'live_warmup':summary['retrieval_readiness'],
        'old_live_job_identical_after_process_restart':True,'old_live_archive_sha256_unchanged':True,
        'live_history_and_review_get_routes':True,'final_code_matches_frozen_replay':True,
        'withdrawn_guideline_excluded':True,'available_guideline_documents':20,
        'isolated_http_review_save_and_edit':True,'isolated_revision_conflict_409':True,
        'isolated_review_history_persisted_across_jobs_restart':True,'fixture_original_report_unchanged':True,
        'production_review_records_mutated':False,'new_provider_calls':0,'browser_visual_verification':False}
    (out/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
