"""Reapply postprocessing to saved framework responses; zero provider calls."""
from copy import deepcopy
import json
from pathlib import Path
from eval.ra_pipeline_update_eval import ROOT,read,write,digest
from rag import ra_answer
from rag.openai_chat import EvaluationAPIChat
from rag.ra_pipeline import VERSION


def run(out):
    v6=ROOT/'data/runs/ra_pipeline_obligations_2026-10-12_v6'
    v5=ROOT/'data/runs/ra_pipeline_obligations_2026-10-12_v5'
    sources={k:v6/'service'/(k+'.json') for k in ['normal','RA04','RA10']}
    sources.update({k:v5/'api'/(k+'.json') for k in ['EMA_UST_PK_013','EMA_UST_PK_014']})
    accounting=EvaluationAPIChat(out/'replay_accounting',max_new_calls=1,client=object())
    rows=[]
    try:
        for key,path in sources.items():
            report=read(path);before=report['results'][0]
            class Saved:
                tokenizer=accounting.tokenizer;input_limit=accounting.input_limit;provider='saved_response_replay'
                def __init__(self):self.n=0
                def __call__(self,messages,json_schema=None,provider=None):
                    self.n+=1
                    value=(before['request_generation'] if self.n==1 else
                        {'claims':before['validation']['semantic_review']['claims']})
                    assert self.n<=2
                    return {'text':json.dumps(value,ensure_ascii=False)}
            client=Saved()
            result=ra_answer.generate(report['question'],report['understanding'],before['evidence'],client,'spans')
            result.update({k:deepcopy(before[k]) for k in ['unit','evidence','context_budget','omitted_chunks']})
            report.update(pipeline_version=VERSION,results=[result],postprocessing_replay={
                'source_report':str(path),'source_sha256':digest(path),'new_provider_calls':0,
                'new_generation_or_semantic_review':False,'saved_model_response_version':read(path)['pipeline_version'],
                'not_a_new_service_execution':True})
            write(out/'replay'/(key+'.json'),report)
            row={'id':key,'before_status':before['status'],'status':result['status'],'claims':len(result['claims']),
                'rejected':len(result['validation']['rejected_items']),
                'whole_reviewed_proposals_preserved':sum(c.get('reviewed_proposal_preserved',False) for c in result['claims'])}
            rows.append(row);print(row,flush=True);print(result['answer_ko'],flush=True)
        write(out/'replay_summary.json',{'rows':rows,'new_provider_calls':0,'independent_holdout':False,
            'source':'saved actual framework responses; postprocessing regression, not fresh model execution'})
    finally:accounting.close()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True,type=Path)
    run(parser.parse_args().out.resolve())
