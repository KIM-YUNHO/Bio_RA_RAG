"""Controlled local output-schema A/B on identical frozen evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rag.answer import Corpus, retrieve, SCHEMA
from rag.answer_conditions import schema as condition_schema, RULES
from rag.budgeted_evidence import BudgetedEvidence
from rag.preprocessed_evidence import EXTRA_RULES, grounded_answer, validate_shape
from rag.preprocessed_prompt import build, configure_budget
from rag.local_chat import LocalChat
from preprocess.hybrid_config import load_config
from review_viewer.data import Bundle


def main():
    out=Path('data/runs/rag_updates_2026-10-09/stage5_schema_comparison');out.mkdir(parents=True,exist_ok=True)
    client=LocalChat(load_config('configs/preprocess.local.json'))
    source=Path('data/runs/rag_updates_2026-10-09/stage1_scope/responses')
    b=Bundle('data/preprocessed/pipeline_review_2026-10-08','data/rag/candidates/study_candidates_2026-10-09_v2')
    fixed=[]
    for key in ['Q001','Q006','Q010','D05','D06','D08']:
        report=json.loads((source/(key+'.json')).read_text(encoding='utf-8'))
        unit=report['results'][0]['unit']
        corpus=Corpus('bge-m3',b.candidate,local_only=True,bundle=b,include_nondefault=report['scope']['include_nondefault'])
        corpus.allowed_documents={(b.docs[k]['document_id'],b.docs[k]['version_id']) for k in report['scope']['selected_documents']}
        pages=report['results'][0].get('retrieved_pages')
        basis='saved_live_hybrid_pages'
        if not pages:
            pages=retrieve(corpus,report['understanding'],None,use_dense=False,policy='general',
                agencies=[unit['jurisdiction']],products=[unit['product']])[0][1]
            basis='BM25_replay_because_old_generation_error_dropped_live_pages'
        adapter=corpus.evidence_adapter
        configure_budget(adapter,client,report['question'],report['understanding'],unit,pages,EXTRA_RULES,RULES)
        evidence=adapter.from_pages(pages,corpus,None,unit)
        corpus.bm.db.close()
        fixed.append({'id':key,'question':report['question'],'understanding':report['understanding'],
            'unit':unit,'pages':pages,'evidence':evidence,'retrieval_basis':basis,'budget':adapter.last_budget})
    text=json.dumps(fixed,ensure_ascii=False,indent=2)
    path=out/'fixed_evidence.json'
    if path.exists() and path.read_text(encoding='utf-8')!=text:raise ValueError('Fixed evidence changed')
    path.write_text(text,encoding='utf-8')
    freeze={'fixed_evidence_sha256':hashlib.sha256(text.encode()).hexdigest(),
        'model':client.config.model,'model_digest':client.tokenizer.model_digest,
        'num_ctx':client.config.num_ctx,'num_predict':client.output_tokens,'temperature':0,'seed':0,
        'control':'Same question, scope, source blocks, generation settings and cell validator. Only output schema and its required-condition instructions differ.',
        'model_axis':'Only qwen3.5:9b is installed. No external API called and no second-model score claimed.'}
    (out/'frozen.json').write_text(json.dumps(freeze,ensure_ascii=False,indent=2),encoding='utf-8')
    rows=[]
    try:
        for case in fixed:
            for variant in ['original_fields','source_conditions']:
                path=out/(case['id']+'_'+variant+'.json')
                if path.exists():row=json.loads(path.read_text(encoding='utf-8'));rows.append(row);continue
                print(case['id'],variant,flush=True)
                evidence=case['evidence'];schema=deepcopy(SCHEMA) if variant=='original_fields' else condition_schema(SCHEMA)
                props=schema['schema']['properties']['items']['items']['properties']
                props['evidence']['items']['enum']=list(evidence)
                if 'conditions' in props:props['conditions']['items']['properties']['evidence']['enum']=list(evidence)
                messages=build(case['question'],case['understanding'],case['unit'],evidence,EXTRA_RULES,
                    RULES if variant=='source_conditions' else '')
                try:
                    r=client(messages,json_schema=schema)
                    raw=json.loads(r['text']);validate_shape(raw)
                    checked=grounded_answer(raw,evidence)
                    row={'id':case['id'],'variant':variant,'result':checked,'telemetry':client.calls[-1]}
                except Exception as exc:
                    row={'id':case['id'],'variant':variant,'error':f'{type(exc).__name__}: {exc}',
                        'telemetry':client.calls[-1] if client.calls else None}
                path.write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
                rows.append(row)
                print(json.dumps({'id':case['id'],'variant':variant,'status':row.get('result',{}).get('status','error'),
                    'items':len(row.get('result',{}).get('items',[])),
                    'finish_reason':(row.get('telemetry') or {}).get('finish_reason')},ensure_ascii=False),flush=True)
    finally:client.release()
    (out/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
