"""Expose labelled verification examples in the isolated development service."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from eval.ra_workflow_update_eval import digest,write
from rag_service.reviews import ReviewStore

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'data/runs/ra_structure_development_2026-10-12_v1'


def main():
    doc=ROOT/'docs/rag/RA_STRUCTURE_DEVELOPMENT_2026-10-12.json'
    review=json.loads(doc.read_text(encoding='utf-8'))
    annotations={c['case']:c for c in review['review']['cases']}
    archive=ROOT/'data/runs/ra_development_service_reports'
    store=ReviewStore(ROOT/'data/runs/ra_development_review_records')
    archive.mkdir(parents=True,exist_ok=True)
    manifest=[]
    for case,entry in review['reports'].items():
        source=ROOT/entry['path'];assert digest(source)==entry['sha256']
        report=json.loads(source.read_text(encoding='utf-8'))
        identity=hashlib.sha256((case+entry['sha256']).encode()).hexdigest()[:32]
        question=f'[개발 검증 {case}] '+report['question']
        job={'id':identity,'state':'completed','stage':'completed','message':'저장된 개발 검증 사례입니다.',
             'created_at':datetime.fromisoformat(report['created_at'].replace('Z','+00:00')).timestamp(),
             'question':question,'report':report,'origin':'labelled_development_verification',
             'source_report':str(source),'source_report_sha256':entry['sha256']}
        path=archive/(identity+'.json')
        if path.exists():assert json.loads(path.read_text(encoding='utf-8'))==job
        else:
            with path.open('x',encoding='utf-8') as stream:json.dump(job,stream,ensure_ascii=False,allow_nan=False)
        a=annotations[case]
        note='Codex AI 출처 검토 (사람 전문가 인증 아님)\n'+a['finding']+'\n남은 점: '+a['remaining']
        note+='\n검증 기록: '+str(source)+'\n완전히 같은 입력은 저장된 응답을 재사용했으며 변경된 입력은 새로 검토했습니다.'
        existing=store.get(job)
        payload={'expected_revision':len(existing['revisions']),
                 'status':'checked' if entry['status']=='answered' else 'needs_work',
                 'note':note,'meeting_questions':[]}
        store.append(job,payload)
        manifest.append({'case':case,'job_id':identity,'url':f'http://127.0.0.1:8785/#job={identity}',
                         'archive_path':str(path),'archive_sha256':digest(path),'source_report_sha256':entry['sha256']})
    write(RUN/'development_ui_examples.json',{'cases':manifest,'production_changes':False,'new_provider_calls':0})
    print(json.dumps({'labelled_examples':len(manifest),'ai_review_notes_saved':len(manifest),'new_provider_calls':0}))


if __name__=='__main__':main()
