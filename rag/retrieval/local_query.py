"""Optional local query translation with literal-term checks and original-query fallback."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import urllib.error

from preprocess.hybrid_config import load_config
from preprocess.ocr.qwen_adapter import QwenAdapter

VERSION = 'local-query-rewrite-1'
SCHEMA = {'type':'object','properties':{'english_query':{'type':'string'}},
          'required':['english_query'],'additionalProperties':False}
SYSTEM = ('Translate the entire Korean document-search question into one English search question. '
    'Do not answer it, add facts, expand study names, or infer document contents. '
    'Preserve all Latin-script terms, identifiers, numbers and units. '
    'Translate written Korean words as words; do not invent numeric identifiers. '
    'Return only JSON with english_query.')
LATIN = re.compile(r'[A-Za-z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)*')
NUMBER = re.compile(r'\d+(?:[.,]\d+)*')


def validate_rewrite(original, value):
    if not isinstance(value,dict) or set(value) != {'english_query'} or not isinstance(value['english_query'],str):
        return ['invalid_schema']
    query=value['english_query'].strip()
    issues=[]
    if not 3 <= len(query) <= 2000 or re.search(r'[가-힣]',query):
        issues.append('not_english_search_query')
    for term in dict.fromkeys(LATIN.findall(original)):
        left = r'(?<![A-Za-z])' if term.casefold() in {'kg','mg','ml','g','ng'} else r'(?<![A-Za-z0-9])'
        if not re.search(left+re.escape(term)+r'(?![A-Za-z0-9])',query,re.I):
            issues.append('missing_literal_term:'+term)
    if Counter(NUMBER.findall(original)) != Counter(NUMBER.findall(query)):
        issues.append('numeric_literals_changed')
    return issues


def rewrite(question, settings=None, adapter=None, max_attempts=2):
    if not isinstance(question,str) or not question.strip() or len(question)>4000:
        raise ValueError('Specify a nonempty query of at most 4000 characters')
    if type(max_attempts) is not int or not 1 <= max_attempts <= 2:
        raise ValueError('Query rewriting permits one or two attempts')
    base={'version':VERSION,'original_query':question,'query':question,'used_rewrite':False,
          'attempted_calls':0,'input_tokens':0,'output_tokens':0,'attempts':[]}
    if not re.search(r'[가-힣]',question):
        return {**base,'status':'original_query_no_translation_needed'}
    backend=adapter or QwenAdapter(settings or load_config().qwen)
    try:
        base['model']=backend.fingerprint()
        for attempt in range(max_attempts):
            body={'model':backend.settings.model,'stream':False,'think':False,'format':SCHEMA,
                'keep_alive':'0','messages':[{'role':'system','content':SYSTEM},
                    {'role':'user','content':json.dumps({'question':question,
                        'previous_issues':base['attempts'][-1]['issues'] if attempt else []},ensure_ascii=False)}],
                'options':{'temperature':0,'seed':0,'num_ctx':backend.settings.num_ctx,'num_predict':512}}
            base['attempted_calls']+=1
            reply=backend.request('/api/chat',body)
            base['input_tokens']+=reply.get('prompt_eval_count') or 0
            base['output_tokens']+=reply.get('eval_count') or 0
            try:value=json.loads(reply.get('message',{}).get('content',''))
            except (json.JSONDecodeError,TypeError):value=None
            issues=validate_rewrite(question,value)
            if not reply.get('done') or reply.get('done_reason')=='length':issues.append('generation_incomplete')
            base['attempts'].append({'issues':issues,'response':value})
            if not issues:
                return {**base,'query':value['english_query'].strip(),'used_rewrite':True,'status':'validated_translation_candidate'}
    except (RuntimeError,ValueError,urllib.error.URLError,TimeoutError) as exc:
        base['attempts'].append({'issues':['local_model_error'],'error':f'{type(exc).__name__}: {exc}'})
    return {**base,'status':'original_query_fallback'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--query',required=True)
    p.add_argument('--config',type=Path,default=Path('configs/preprocess.local.json'))
    args=p.parse_args()
    print(json.dumps(rewrite(args.query,load_config(args.config).qwen),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
