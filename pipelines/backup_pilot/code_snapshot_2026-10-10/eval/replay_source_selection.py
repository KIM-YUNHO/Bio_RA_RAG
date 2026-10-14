"""Bounded local development replay using archived evidence and source ID output."""
import json,sys
from copy import deepcopy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rag.local_chat import LocalChat
from rag.source_selection import catalog,prompt,schema,resolve
from rag.preprocessed_evidence import grounded_answer
from rag.answer_conditions import missing,attribution_missing,label
from preprocess.hybrid_config import load_config
from preprocess.inventory import write_json
from preprocess.hybrid import read_jsonl


def main():
    baseline=Path('data/runs/rag_updates_2026-10-09/final_22')
    out=Path('data/runs/corpus_update_2026-10-09/source_selection_replay_v2');out.mkdir(parents=True,exist_ok=True)
    units={u['unit_id']:u for u in read_jsonl('data/preprocessed/pipeline_review_2026-10-08/source_units.jsonl')}
    client=LocalChat(load_config('configs/preprocess.local.json'));results=[]
    try:
        for key in ['Q003','Q013','Q010','D07','Q005']:
            dest=out/(key+'.json')
            if dest.exists():results.append(json.loads(dest.read_text(encoding='utf-8')));continue
            report=json.loads((baseline/'responses'/(key+'.json')).read_text(encoding='utf-8'))
            original=report['results'][0];evidence=deepcopy(original['evidence'])
            seen=set()
            for block in list(evidence.values()):
                for heading in block['structure'].get('heading_anchors',[]):
                    uid=heading['heading_location'].get('block_id');unit=units.get(uid)
                    if uid in seen or not unit or not unit.get('retrieval_eligible'):continue
                    seen.add(uid);context=deepcopy(block)
                    context.update(chunk_id='heading:'+uid,text=unit['text'],pdf_page=heading['heading_location']['pdf_page'],retrieval_role='source_heading')
                    context['source_spans']=[{'unit_id':uid,'representation_start':0,'representation_end':len(unit['text']),'separator_before':''}]
                    context['structure'].update(kind='text',table_id=None,figure_id=None,caption=None)
                    evidence[f'E{len(evidence)+1}']=context
            candidates=catalog(evidence);messages=prompt(report['question'],report['understanding'],original['unit'],evidence)
            output={'case':key,'question':report['question'],'comparison':'Same archived hit evidence plus source-linked headings; changed prompt/output contract. Not a model-only comparison.',
                'evidence':evidence,'candidates':candidates,'prompt_tokens_estimate':client.tokenizer.messages(messages)}
            write_json(out/(key+'.input.json'),{'messages':messages,'schema':schema(candidates),'evidence':evidence,'candidates':candidates})
            try:
                reply=client(messages,json_schema=schema(candidates))
                parsed=json.loads(reply['text']);checked=grounded_answer(resolve(parsed,candidates,evidence),evidence)
                absent=attribution_missing(checked['items'],evidence,report['understanding'],missing(checked['items'],report['question'],report['understanding']))
                for item in absent:
                    checked['items'][item['item_index']].update(exact=False,unresolved_conditions=item['dimensions'])
                if absent and checked['items']:checked['status']='partial'
                checked['answer_ko']='\n'.join(f"{label(i)}: {i['value']}" for i in checked['items'])
                checked['validation']['missing_conditions']=absent
                output.update(status='completed',reply=reply,parsed=parsed,validated=checked)
            except Exception as exc:output.update(status='error',error=type(exc).__name__+': '+str(exc))
            output['telemetry']=deepcopy(client.calls[-1:]);write_json(dest,output);results.append(output)
            print(json.dumps({'case':key,'status':output['status'],'validated_status':output.get('validated',{}).get('status'),
                'items':len(output.get('validated',{}).get('items',[])),'prompt_tokens_estimate':output['prompt_tokens_estimate']},ensure_ascii=False),flush=True)
        write_json(out/'summary.json',{'cases':[{k:r.get(k) for k in ('case','status','prompt_tokens_estimate')}|{
            'validated_status':r.get('validated',{}).get('status'),'items':len(r.get('validated',{}).get('items',[]))} for r in results],
            'model':'qwen3.5:9b','api_calls':0,'limitations':'Five selected development failures; no overall accuracy claim.'})
    finally:client.release()


if __name__=='__main__':main()
