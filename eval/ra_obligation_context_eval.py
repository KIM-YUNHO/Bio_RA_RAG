"""Rebuild reading context from actual saved rankings without provider/embedding calls."""
import argparse
import ast
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path

from eval.ra_pipeline_update_eval import ROOT,RELEASE,GUIDELINES,read,digest,write
from rag import answer,ra_retrieval
from rag.openai_chat import EvaluationAPIChat
from rag.ra_pipeline import RAPipeline,VERSION

PREVIOUS=ROOT/'data/runs/ra_pipeline_completion_2026-10-12_v2'


def run(out):
    old=read(PREVIOUS/'candidate.json')
    index=read(PREVIOUS/'baseline.json')['index_sha256']
    assert all(digest(ROOT/name)==value for name,value in index.items())
    core=['diverse_hits','retrieve','_retrieve','packet','blocks']
    def definitions(path):
        return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(path.read_text(encoding='utf-8')).body
                if isinstance(n,ast.FunctionDef) and n.name in core}
    assert definitions(ROOT/'rag/ra_retrieval.py')==definitions(PREVIOUS/'candidate_code/rag/ra_retrieval.py')
    client=EvaluationAPIChat(out/'local',max_new_calls=1,client=object())
    pipeline=RAPipeline(RELEASE,guideline_snapshot=GUIDELINES,chat_client=client)
    corpus=answer.Corpus('bge-m3',pipeline.bundle.candidate,local_only=True,bundle=pipeline.bundle,include_nondefault=True)
    chunks={c['chunk_id']:c for c in [*corpus.chunks,*pipeline.guidelines.chunks]}
    paths={k:PREVIOUS/'api'/(k+'.json') for k in ['RA04','RA10','RA14']}
    paths.update({k:PREVIOUS/'package_eval'/(k+'.json') for k in ['EMA_UST_PK_013','EMA_UST_PK_014']})
    rows=[]
    try:
        for key,path in paths.items():
            report=read(path)
            corpus.allowed_documents={(d['document_id'],d['version_id']) for d in pipeline.documents
                if d['key'] in report['scope']['selected_documents']}
            groups=[{**deepcopy(g),'hits':[{'score':h['score'],'chunk':chunks[h['chunk_id']]} for h in g['hits']]}
                for g in report['retrieval']]
            evidence,budget=ra_retrieval.context(corpus,groups,report['question'],report['understanding'],client,'spans',
                guideline_index=pipeline.guidelines)
            before=report['results'][0]['evidence']
            old_ids={b['chunk_id'] for b in before.values()};new_ids={b['chunk_id'] for b in evidence.values()}
            row={'id':key,'actual_saved_rankings_reused':True,'old_source_count':len(before),
                'new_source_count':len(evidence),'all_old_admitted_sources_preserved':old_ids<=new_ids,
                'missing_old_chunk_ids':sorted(old_ids-new_ids),'context_tokens':budget['estimated_prompt_and_schema_tokens']}
            write(out/'local'/(key+'.json'),{'source_report_sha256':digest(path),'question':report['question'],
                'understanding':report['understanding'],'retrieval':report['retrieval'],'before_evidence':before,
                'evidence':evidence,'context_budget':budget})
            rows.append(row);print(row,flush=True)
        write(out/'local_replay.json',{'rows':rows,'retrieval_core_functions_unchanged':core,
            'new_provider_calls':0,'new_embedding_calls':0,'source':'actual 4.5.1 saved hit lists',
            'scope':'Reading-context regression only; no independent accuracy claim.'})
        write(out/'baseline.json',{'pipeline_version':old['pipeline_version'],'index_sha256':index,
            'source_candidate_sha256':digest(PREVIOUS/'candidate.json')})
        paths=[*sorted((ROOT/'rag').glob('*.py')),Path(__file__),ROOT/'eval/ra_obligation_generation_eval.py']
        write(out/'candidate.json',{'created_at':datetime.now(timezone.utc).isoformat(),'pipeline_version':VERSION,
            'code_sha256':{str(p.relative_to(ROOT)):digest(p) for p in paths},'model':'gpt-4o-mini',
            'independent_holdout':False,'gold_sent':False,'automatic_retries':0})
        for path in paths:
            target=out/'candidate_code'/path.relative_to(ROOT)
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(path.read_bytes())
    finally:
        corpus.release_encoder();corpus.bm.db.close();pipeline.close();client.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=Path)
    run(parser.parse_args().out.resolve())
