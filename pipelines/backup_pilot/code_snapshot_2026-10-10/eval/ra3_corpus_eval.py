"""Frozen local RAG evaluation, with generation and semantic review separated."""
import argparse,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.current_server_eval import build_cases,score,summarize,CODE
from preprocess.ingest import sha256
from preprocess.inventory import write_json
from preprocess.hybrid_config import load_config
from rag.pipeline import RAGPipeline

def cases_for(summary):
    cases=build_cases(summary)
    cards_path=ROOT/'data/runs/corpus_update_2026-10-09/multi_product_eval/source_cards.json'
    cards=json.loads(cards_path.read_text(encoding='utf-8'))
    for card in cards:
        document=next(d for d in summary['documents'] if d['document_id']==card['document_id'] and d['version_id']==card['version_id'])
        if sha256(card['raw_path'])!=card['raw_sha256']:raise ValueError('Five-product label source changed')
        product=card['product_id']
        question=f'FDA {product} 제품설명서에서 판상 건선 성인에게 권장되는 체중 100 kg 이하 및 100 kg 초과의 용량, 투여 경로, 초기 및 유지 투여 간격은 무엇인가?'
        cases.append({'id':'M_'+product,'suite':'five_product_label_development','topic':'adult_label_dosing',
            'question':question,'payload':{'question':question,'products':[product],'jurisdictions':['US'],
                'documents':[document['key']],'include_nondefault':True},'expected':'answerable',
            'document_id':card['document_id'],'version_id':card['version_id'],
            'gold_pages':[{'SB17':4,'ABP654':4,'CTP43':3,'BMAB1200':3,'BAT2206':3}[product]],
            'rubric':['Adult plaque psoriasis, not pediatric or psoriatic arthritis',
                '100 kg or less: 45 mg; more than 100 kg: 90 mg',
                'Subcutaneous; initial dose and 4 weeks later; then every 12 weeks',
                'Keep weight boundary attached to its dose; source quotations must support displayed conditions'],
            'gold_status':'Source-inspected agent development card; not independent holdout or expert gold'})
    return cases

def run(release,out):
    release,out=Path(release).resolve(),Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    pipeline=RAGPipeline(release,release/'candidate',load_config(ROOT/'configs/preprocess.local.json'))
    summary=pipeline.summary();cases=cases_for(summary)
    hashes={n:sha256(ROOT/n) for n in [*CODE,'eval/ra3_corpus_eval.py'] if (ROOT/n).is_file()}
    frozen={'summary':summary,'code_sha256':hashes,'cases':cases,'api_calls':0,
        'scope':'18 historical RA3 answerable development questions, 1 false-premise control, and 5 source-scoped FDA label checks. Five label checks do not measure cross-product clinical generalization.',
        'source_cards_sha256':sha256(ROOT/'data/runs/corpus_update_2026-10-09/multi_product_eval/source_cards.json'),
        'semantic_accuracy_certified':False,'policy':'No gold in generation requests; source-based semantic adjudication follows generation.'}
    path=out/'frozen.json'
    if path.exists() and json.loads(path.read_text(encoding='utf-8'))!=frozen:raise ValueError('Frozen evaluation changed')
    write_json(path,frozen);write_json(out/'cases.json',cases)
    rows=[]
    for number,case in enumerate(cases,1):
        if any(sha256(ROOT/n)!=h for n,h in hashes.items()):raise ValueError('Evaluation code changed')
        response=out/'responses'/(case['id']+'.json');response.parent.mkdir(exist_ok=True)
        print(f"[{number}/{len(cases)}] {case['id']} {case['topic']}",flush=True)
        if response.exists():
            report=json.loads(response.read_text(encoding='utf-8'))
            if report['question']!=case['question'] or report['index']['chunks_sha256']!=summary['chunks_sha256']:raise ValueError('Saved response changed')
        else:
            write_json(response.with_suffix('.request.json'),case['payload'])
            payload=case['payload']
            report=pipeline.ask(payload['question'],payload.get('products'),payload.get('jurisdictions'),payload.get('documents'),
                include_nondefault=payload.get('include_nondefault',False))
            write_json(response,report)
        row=score(case,report);rows.append(row)
        write_json(out/'automatic.json',{'summary':summarize(rows),'rows':rows,'api_calls':0,'semantic_accuracy_certified':False})
        print(json.dumps(row,ensure_ascii=False),flush=True)
        if len(rows)>=3 and all(r['status']=='error' for r in rows[-3:]):raise RuntimeError('Three consecutive RAG errors; inspect hardware/runtime before continuing')
    pipeline.client.release()
    write_json(out/'completed.json',{'n':len(rows),'summary':summarize(rows),'api_calls':0,
        'semantic_adjudication':'pending_source_review','answer_accuracy_certified':False,
        'all_requests_exclude_gold':True,'source_cards_sha256':frozen['source_cards_sha256']})
    return rows

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--release',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();run(a.release,a.out)
