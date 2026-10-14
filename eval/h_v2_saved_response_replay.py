"""Replay only complete identical cached model inputs; no network client exists."""
import argparse
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from eval.h_v2_development_eval import read, save, digest, request_key, MODEL
from eval.ra_workflow_update_eval import audit
from rag import ra_answer
from rag.openai_chat import ApiTokenizer
from rag.ra_pipeline import VERSION
from rag.ra_memo import build as build_memo


def run(root):
    rows=[]
    dest=root/'saved_replay_4_9_3'
    if dest.exists():
        raise ValueError('Never overwrite a replay')
    for phase in ('table_focus', 'transfer_utf8'):
        cache=read(root/phase/'provider_cache.json')
        class OfflineClient:
            model=MODEL
            reasoning_effort='none'
            provider='saved_response_replay'
            source_selection=True
            config=SimpleNamespace(num_ctx=16384,num_predict=3000)
            tokenizer=ApiTokenizer(MODEL)
            input_limit=13128
            def __init__(self): self.keys=[]
            def __call__(self,messages,json_schema=None,provider=None):
                key=request_key(self,messages,json_schema)
                if key not in cache:
                    raise ValueError('Changed model input: offline replay cannot continue')
                self.keys.append(key)
                return deepcopy(cache[key]['reply'])
        for path in sorted((root/phase/'v2').glob('*.json')):
            old=read(path);before=old['results'][0];client=OfflineClient()
            result=ra_answer.generate(old['question'],old['understanding'],before['evidence'],client,'spans')
            result['evidence']=deepcopy(before['evidence'])
            # The entire pre-review model input (including selected citations)
            # is identical by cache key; only deterministic guards have changed.
            report={**old,'pipeline_version':VERSION,'results':[result],
                'postprocessing_replay':{'source_report':str(path),'source_sha256':digest(path),
                    'new_provider_calls':0,'fresh_generation':False,'fresh_semantic_review':False,
                    'all_model_inputs_exactly_identical':True,'cache_keys':client.keys}}
            report['review_memo']=build_memo(report)
            save(dest/phase/(path.stem+'.json'),report)
            rows.append({'phase':phase,'case':path.stem,'source_report_sha256':digest(path),
                'before_claims':len(before['claims']),'after_claims':len(result['claims']),
                'before_reasons':[c.get('reason') for c in before['validation']['rejected_items']],
                'after_reasons':[c.get('reason') for c in result['validation']['rejected_items']],
                'identical_cached_model_inputs':len(client.keys),**audit(report)})
    save(dest/'summary.json',{'pipeline_version':VERSION,'rows':rows,'new_provider_calls':0,
        'fresh_generation':False,'independent_holdout':False})
    print(dest)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True)
    run(p.parse_args().run.resolve())
