"""Local product-routing smoke on available source types, not a clinical QA gold set."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from eval.current_server_eval import request,write


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    base='http://127.0.0.1:8765';summary=request(base,'/api/summary')
    cases=[('ABP654','EU','ABP654 건강인 PK 시험의 투여 용량은 무엇인가?'),
        ('CTP43','EU','CT-P43 건강인 PK 시험의 투여 용량은 무엇인가?'),
        ('BMAB1200','US','FDA Yesintek 품질 심사 문서에서 활성 성분은 무엇인가?'),
        ('BAT2206','EU','Usymro 개요 문서에서 활성 성분은 무엇인가?')]
    rows=[]
    for product,agency,question in cases:
        path=args.out/(product+'.json')
        if path.exists():report=json.loads(path.read_text(encoding='utf-8'))
        else:
            payload={'question':question,'products':[product],'jurisdictions':[agency]}
            write(args.out/(product+'.request.json'),payload)
            job=request(base,'/api/questions',payload);started=time.monotonic()
            print(product,flush=True)
            while True:
                job=request(base,'/api/jobs/'+job['id'])
                if job['state']=='completed':break
                if job['state']=='failed':raise RuntimeError(job.get('error'))
                if time.monotonic()-started>1200:raise TimeoutError(product)
                time.sleep(2)
            report=job['report'];write(path,report)
        assert report['index']['chunks_sha256']==summary['chunks_sha256']
        plans=report['plan']
        assert plans and all(p['product']==product and p['jurisdiction']==agency for p in plans)
        rows.append({'product':product,'scope_pass':True,'status':report['status'],
            'units':[r['status'] for r in report['results']],
            'items':sum(len(r['items']) for r in report['results']),
            'seconds':report['seconds'],'answer':[r['answer_ko'] for r in report['results']]})
    write(args.out/'summary.json',{'created_at':datetime.now(timezone.utc).isoformat(),'rows':rows,
        'index_sha256':summary['chunks_sha256'],'type':'routing and basic source-answer smoke; not clinical accuracy evaluation'})
    print(json.dumps(rows,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
