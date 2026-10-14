"""Freeze postprocessing corrections and replay exact stored framework replies."""
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import ast
import json
from pathlib import Path
import shutil

from eval.ra_pipeline_update_eval import ROOT, read, digest, write
from preprocess.ingest import stable_hash
from rag import ra_answer
from rag.openai_chat import ApiTokenizer
from rag.ra_pipeline import VERSION


class SavedReplies:
    provider='openai';input_limit=13128
    tokenizer=ApiTokenizer('gpt-4o-mini')
    def __init__(self,report):
        self.saved={r['request_key']:r for r in report['generation_telemetry']}
        self.used=[]
    def __call__(self,messages,json_schema=None,provider=None):
        payload={'model':'gpt-4o-mini','messages':messages,'temperature':0,'seed':0,'max_tokens':3000,
            'response_format':{'type':'json_schema','json_schema':{**json_schema,'strict':True}}}
        key=stable_hash(payload)
        if key not in self.saved: raise RuntimeError('Framework input/schema changed; no automatic new call')
        reply=self.saved[key]
        if reply['status']!='completed': raise RuntimeError('Stored framework reply failed')
        self.used.append(key)
        return {'text':reply['raw_text']}


def run(prior,out):
    if out.exists(): raise ValueError('Use a new output directory')
    out.mkdir(parents=True)
    frozen=read(prior/'candidate.json')
    for name,value in read(prior/'baseline.json')['index_sha256'].items():
        if digest(ROOT/name)!=value: raise RuntimeError('Index changed')
    functions=['retrieve','_retrieve','diverse_hits','oracle_groups','packet']
    def fingerprints(path):
        return {node.name:ast.dump(node,include_attributes=False) for node in ast.parse(path.read_text(encoding='utf-8')).body
                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in functions}
    preserved=fingerprints(ROOT/'rag/ra_retrieval.py')==fingerprints(prior/'candidate_code/rag/ra_retrieval.py')
    if not preserved: raise RuntimeError('Core retrieval changed')
    paths=[ROOT/name for name in frozen['code_sha256']]+[Path(__file__)]
    hashes={str(p.relative_to(ROOT)):digest(p) for p in paths}
    write(out/'candidate.json',{'pipeline_version':VERSION,'model':'gpt-4o-mini','code_sha256':hashes,
        'created_at':datetime.now(timezone.utc).isoformat(),'prior_candidate_sha256':digest(prior/'candidate.json'),
        'correction_scope':'Selected-source postprocessing only; source-derived safety corrections after diagnostic package exposure.',
        'new_api_calls':0,'independent_holdout':False,'gold_sent':False})
    for path in paths:
        target=out/'candidate_code'/path.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,target)
    for name in ['baseline.json','validation_package_audit.json']:
        shutil.copy2(prior/name,out/name)
    replay=read(prior/'local_replay.json')
    replay.update(reused_from=str(prior/'local_replay.json'),new_search_executions=0,
        retrieval_core_ast_preserved=preserved,index_hashes_preserved=True,
        scope='Reuse previous actual retrieval after postprocessing-only correction; not a fresh search run.')
    write(out/'local_replay.json',replay)
    records=[(prior/'api'/f'{key}.json',out/'api'/f'{key}.json') for key in ['RA04','RA10','RA14']]
    records += [(p,out/'package_eval'/p.name) for p in sorted((prior/'package_eval').glob('EMA*.json'))
        if '.summary.' not in p.name and '.failure.' not in p.name]
    live=read(prior/'service/last_job.json')
    write(prior/'service/live_report.json',live['report'])
    records += [(prior/'service/live_report.json',out/'service_replay/live_report.json')]
    rows=[]
    for path,target in records:
        before=read(path);client=SavedReplies(before);report=deepcopy(before)
        previous=before['results'][0]
        result=ra_answer.generate(before['question'],before['understanding'],previous['evidence'],client,'spans')
        result.update({key:deepcopy(previous[key]) for key in ['unit','evidence','context_budget','omitted_chunks']})
        report.update(pipeline_version=VERSION,results=[result],verification_replay={
            'original_runtime_version':before['pipeline_version'],'source_report':str(path),'sha256':digest(path),
            'framework_reply_keys':client.used,'new_api_calls':0,'same_exact_model_input_and_schema':True})
        write(target,report)
        row={'id':path.stem,'status':result['status'],'claims':len(result['claims']),
            'rejected':len(result['validation']['rejected_items']),'new_api_calls':0}
        rows.append(row);print(json.dumps(row),flush=True)
    if any(digest(ROOT/name)!=value for name,value in hashes.items()): raise RuntimeError('Frozen code changed')
    write(out/'replay_receipt.json',{'rows':rows,'new_api_calls':0,'independent_holdout':False})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prior',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();run(args.prior.resolve(),args.out.resolve())
