"""At most five API attempts, replaying archived production prompts without gold."""
import argparse,json,sys,time
from pathlib import Path
from datetime import datetime, timezone, timedelta
KST=timezone(timedelta(hours=9))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rag.llm import settings
from rag.answer import SCHEMA
from rag.answer_conditions import quote_schema
from rag.preprocessed_evidence import grounded_answer, validate_shape
from preprocess.ingest import stable_hash
from preprocess.inventory import write_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--case',action='append',required=True);args=p.parse_args()
    if not 1<=len(args.case)<=5 or len(set(args.case))!=len(args.case):raise ValueError('Choose at most five unique cases')
    day=datetime.now(KST).date().isoformat()
    ledger_path=Path('data/runs/model_quota')/(day+'.json')
    ledger=json.loads(ledger_path.read_text(encoding='utf-8')) if ledger_path.exists() else {'date_kst':day,'scope':'This project evaluation only; other API usage is not observable','attempts':[]}
    args.out.mkdir(parents=True,exist_ok=True)
    config=settings('openai')
    from openai import OpenAI
    client=OpenAI(api_key=config['api_key'],base_url=config['base_url'],timeout=90,max_retries=0)
    results=[]
    for key in args.case:
        dest=args.out/(key+'.json')
        if dest.exists():results.append(json.loads(dest.read_text(encoding='utf-8')));continue
        if len(ledger['attempts'])>=5:raise ValueError('Five API attempts already reserved today; no further calls')
        report=json.loads((args.baseline/'responses'/(key+'.json')).read_text(encoding='utf-8'))
        result=report['results'][0]
        if result['unit']['jurisdiction'] not in ('EU','US','CA'):raise ValueError('Case outside the requested RA scope')
        messages=next(c['messages'] for c in result['generation_telemetry'] if c['schema']=='unit_answer')
        evidence=result['evidence'];schema=quote_schema(SCHEMA)
        item=schema['schema']['properties']['items']['items']
        item['properties']['evidence']['items']['enum']=list(evidence)
        item['properties']['conditions']['items']['properties']['evidence']['enum']=list(evidence)
        record={'case':key,'out':str(dest),'model':'gpt-4o-mini','max_output_tokens':3000,
            'messages_sha256':stable_hash(messages),'evidence_sha256':stable_hash(evidence),
            'status':'reserved','reserved_at':datetime.now(KST).isoformat()}
        if any(a.get('out')==str(dest) for a in ledger['attempts']):raise ValueError('A prior in-flight attempt is not repeated automatically')
        ledger['attempts'].append(record);write_json(ledger_path,ledger)
        write_json(args.out/(key+'.input.json'),{'question':report['question'],'messages':messages,'schema':schema,
            'evidence':evidence,'baseline_model':result.get('model'),'model_only_comparison':True,'gold_sent':False})
        started=time.monotonic()
        output={**record,'question':report['question'],'baseline_status':result['status'],'baseline_items':result['items']}
        try:
            response=client.chat.completions.create(model='gpt-4o-mini',messages=messages,
                temperature=0,seed=0,max_tokens=3000,response_format={'type':'json_schema','json_schema':{**schema,'strict':True}})
            choice=response.choices[0];raw=choice.message.content or ''
            output.update(raw_text=raw,finish_reason=choice.finish_reason,model_reported=response.model,
                prompt_tokens=response.usage.prompt_tokens,completion_tokens=response.usage.completion_tokens,
                system_fingerprint=response.system_fingerprint,status='completed')
            if choice.finish_reason!='stop':raise ValueError('Incomplete API generation')
            parsed=json.loads(raw);validate_shape(parsed)
            output['validated']=grounded_answer(parsed,evidence)
        except Exception as exc:
            output.update(status='error',error=(type(exc).__name__+': '+str(exc)).replace(config['api_key'],'[REDACTED]'))
        output['seconds']=round(time.monotonic()-started,3)
        record.update({k:output.get(k) for k in ('status','prompt_tokens','completion_tokens','finish_reason','seconds')})
        write_json(ledger_path,ledger);write_json(dest,output);results.append(output)
        print(json.dumps({k:output.get(k) for k in ('case','status','finish_reason','prompt_tokens','completion_tokens','seconds')},ensure_ascii=False),flush=True)
        if output['status']=='error':break
    summary={'model':'gpt-4o-mini','attempts':len(results),'maximum_project_attempts_today':5,
        'no_connection_probe':True,'no_automatic_retries':True,'same_archived_prompts':True,
        'input_tokens':sum(r.get('prompt_tokens') or 0 for r in results),'output_tokens':sum(r.get('completion_tokens') or 0 for r in results),
        'cases':[{k:r.get(k) for k in ('case','status','seconds')}|{'validated_status':(r.get('validated') or {}).get('status')} for r in results],
        'interpretation':'Selected development failures only; not an overall accuracy estimate. Semantic source review is separate.'}
    write_json(args.out/'summary.json',summary)


if __name__=='__main__':main()
