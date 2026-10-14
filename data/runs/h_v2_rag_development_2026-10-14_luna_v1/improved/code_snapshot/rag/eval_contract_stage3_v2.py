"""Frozen, bounded evaluation of the final query schema and source relations.

Development diagnostics only. Same saved evidence for both selectors; no gold
in prompts, no OCR, embeddings, or production changes. One question-only repair
at most; API errors never retried. Older stage-2 outputs remain immutable.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rag import contract_query, contract_candidate, source_selection
from rag.eval_contract_stage2 import read, write, sha, TESTS, MODULES, BASE, RELEASE
from rag.openai_chat import EvaluationAPIChat, ApiTokenizer, KST

RUN = ROOT / 'data/runs/rag_contract_stage3_2026-10-11_v2'
SOURCE = ROOT / 'data/runs/rag_contract_stage2_2026-10-11_v4'
CAP = 9
CODE = [*MODULES, 'rag/question_anchors.py', 'rag/eval_contract_stage3_v2.py']
CHECKS = [*TESTS, 'tests.test_contract_invariants', 'tests.test_question_anchors']


def check(prepared):
    assert all(sha(ROOT/n) == h for n,h in prepared['candidate_code_sha256'].items()), 'frozen candidate changed'
    assert all(sha(ROOT/n) == h for n,h in prepared['production_code_sha256'].items()), 'production baseline changed'
    assert sha(RUN/'inputs.json') == prepared['inputs_sha256']
    assert sha(RELEASE/'release_manifest.json') == prepared['release_manifest_sha256']
    assert sha(RELEASE/'candidate/chunks.jsonl') == prepared['chunks_sha256']


def prepare():
    if (RUN/'prepared.json').exists():
        check(read(RUN/'prepared.json')); return
    RUN.mkdir(parents=True, exist_ok=True)
    baseline = read(BASE/'prepared.json')
    with (RUN/'tests.txt').open('w', encoding='utf-8') as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
            unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(t) for t in CHECKS))
    assert result.wasSuccessful()
    cases = [c for c in read(SOURCE/'inputs.json') if c['id'] in ('T05','Q038','Q057','Q012')]
    protected = set(baseline['protected_excluded'])
    for c in cases:
        assert all(b['jurisdiction'] in ('EU','US','CA') and b['version_id'] not in protected for b in c['evidence'].values())
    write(RUN/'inputs.json', cases)
    day = datetime.now(KST).date().isoformat()
    daily = ROOT/'data/runs/model_quota'/(day+'.json')
    before = len(read(daily)['attempts']) if daily.exists() else 0
    tokenizer = ApiTokenizer()
    query_preflight = [{
        'id':c['id'], 'tokens':tokenizer.messages(contract_query.messages(c['question']))
            + tokenizer.one(json.dumps(contract_query.schema(c['question']), ensure_ascii=False, separators=(',',':')))
    } for c in cases]
    prepared = {
        'version':'contract-stage3-evaluation-1', 'date_kst':day,
        'candidate_code_sha256':{n:sha(ROOT/n) for n in CODE},
        'production_code_sha256':baseline['code_sha256'],
        'inputs_sha256':sha(RUN/'inputs.json'),
        'release_manifest_sha256':baseline['release_manifest_sha256'],
        'chunks_sha256':baseline['chunks_sha256'],
        'tests_passed':result.testsRun, 'fresh_call_cap':CAP,
        'project_daily_attempts_before':before, 'project_daily_envelope':before+CAP,
        'account_remaining_quota_known':False, 'query_preflight':query_preflight,
        'context_tokens':32768, 'query_output_tokens':4500, 'answer_output_tokens':3000,
        'query_repair_limit':1, 'automatic_api_retries':0, 'gold_in_generation':False,
        'independent_generalization_evaluation':False, 'same_context_comparison':True,
        'production_changed':False, 'new_ocr_calls':0, 'new_document_embedding_calls':0,
        'protected_excluded':baseline['protected_excluded'],
        'selection_during_question_review_is_diagnostic_only':True,
        'baseline_is_saved_selection_replay_not_new_generation':True,
        'stage3_v1_calls':15,'stage3_v1_plus_v2_cap':24,'v2_query_repair_limit_total':1,
    }
    for name in CODE + [t.replace('.','/')+'.py' for t in CHECKS]:
        target=RUN/'code_snapshot'/name; target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((ROOT/name).read_bytes())
    write(RUN/'prepared.json', prepared); check(prepared)
    print(json.dumps({'prepared':True,'tests':result.testsRun,'cap':CAP,'daily_before':before,'query_preflight':query_preflight}),flush=True)


class BoundedChat(EvaluationAPIChat):
    def _reserve(self, key, request_path, name, output):
        p=read(RUN/'prepared.json'); check(p)
        if datetime.now(KST).date().isoformat()!=p['date_kst']:
            raise RuntimeError('KST date changed; daily usage review required')
        if self.ledger_path.exists() and len(read(self.ledger_path)['attempts'])>=p['project_daily_envelope']:
            raise RuntimeError('stage3 project daily envelope reached')
        return super()._reserve(key,request_path,name,output)


def run(limit=None):
    prepare(); p=read(RUN/'prepared.json'); check(p)
    client=BoundedChat(RUN/'api_eval',max_new_calls=CAP); client.config.num_ctx=p['context_tokens']
    try:
        for c in read(RUN/'inputs.json')[:limit]:
            folder=RUN/'results'/c['id']; folder.mkdir(parents=True,exist_ok=True)
            if (folder/'complete.json').exists(): continue
            started=time.monotonic()
            if (folder/'query_checked.json').exists():
                checked=read(folder/'query_checked.json')
            else:
                client.case=c['id']+':question'; client.config.num_predict=p['query_output_tokens']
                used_repair=any(read(f).get('model_calls',1)>1 for f in (RUN/'results').glob('*/query_checked.json'))
                checked=contract_query.interpret_checked(c['question'],chat_client=client,allow_repair=not used_repair)
                write(folder/'query_checked.json',checked)
            inter=checked['interpretation']
            if inter is None:
                write(folder/'complete.json',{'question_state':checked['state'],'selection_skipped':True,'error':checked['error']})
                continue
            candidates,audits=contract_candidate.inventory(c['question'],inter,c['unit'],c['evidence'])
            write(folder/'source_audits.json',audits)
            for mode in ('baseline','candidate'):
                if (folder/(mode+'.json')).exists(): continue
                messages=(contract_candidate.selector_prompt(c['question'],inter['understanding'],c['unit'],c['evidence'],candidates)
                          if mode=='baseline' else contract_candidate.prompt(c['question'],inter,c['unit'],c['evidence'],candidates,audits))
                client.case=c['id']+':'+mode; client.config.num_predict=p['answer_output_tokens']
                if mode=='baseline':
                    raw=read(SOURCE/'results'/c['id']/'baseline.json')['raw_selection']
                    reply={'prompt_tokens':0,'completion_tokens':0}
                else:
                    reply=client(messages,json_schema=source_selection.schema(candidates))
                    raw=json.loads(reply['text'])
                literal=source_selection.resolve(raw,candidates,c['evidence'],c['question'],inter['understanding'])
                validated=contract_candidate.validate_selection(raw,candidates,audits,c['evidence'],inter,c['question'])
                write(folder/(mode+'.json'),{'raw_selection':raw,'literal_resolved':literal,'contract_validated':validated,
                    'question_state':checked['state'],'diagnostic_only':checked['state']!='contract_candidate_ready',
                    'fresh_generation':mode=='candidate',
                    'tokens':{k:reply.get(k) for k in ('prompt_tokens','completion_tokens')}})
            summary={'question_state':checked['state'],'question_calls':checked['model_calls'],
                'contract_status':inter['requirements']['status'],
                'missing_question_roles':inter['requirements']['missing_known_mentions'],
                'question_warnings':inter['requirements']['warnings'],
                'requests':len(inter['requirements']['requests']), 'seconds':round(time.monotonic()-started,3)}
            for mode in ('baseline','candidate'):
                v=read(folder/(mode+'.json'))['contract_validated']
                summary[mode]={'status':v['status'],'accepted':len(v['items']),'related':len(v['related_items']),
                    'rejected':len(v['rejected_items']),'coverage':v['request_coverage']}
            write(folder/'complete.json',summary)
            print(json.dumps({'id':c['id'],**summary},ensure_ascii=False),flush=True)
    finally:
        client.close();check(p)
        calls=[a for a in read(client.ledger_path)['attempts'] if a.get('evaluation_out')==str(client.out)] if client.ledger_path.exists() else []
        summaries={c['id']:read(RUN/'results'/c['id']/'complete.json') for c in read(RUN/'inputs.json')
                   if (RUN/'results'/c['id']/'complete.json').exists()}
        write(RUN/'progress.json',{'cases':summaries,'attempts':len(calls),'cap':CAP,
            'prompt_tokens':sum(a.get('prompt_tokens') or 0 for a in calls),
            'completion_tokens':sum(a.get('completion_tokens') or 0 for a in calls),
            'api_errors':[a for a in calls if a['status']!='completed'],
            'production_unchanged':True,'generalization_measured':False})


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--prepare',action='store_true');parser.add_argument('--limit',type=int)
    args=parser.parse_args();prepare() if args.prepare else run(args.limit)
