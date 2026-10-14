"""End-to-end API RAG evaluation; original public corpus and gold stay separate."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eval.ra3_corpus_eval import cases_for
from eval.current_server_eval import CODE,score,summarize
from rag.openai_chat import EvaluationAPIChat
from rag.pipeline import RAGPipeline
from preprocess.hybrid_config import load_config
from preprocess.ingest import sha256
from preprocess.inventory import write_json
from preprocess.workflow import timestamp,exclusive


def run(release,out,max_calls=60,case_ids=None,default_labels=False):
    release,out=Path(release).resolve(),Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    with exclusive(out):
        client=EvaluationAPIChat(out,max_new_calls=max_calls)
        pipeline=RAGPipeline(release,release/'candidate',load_config(ROOT/'configs/preprocess.local.json'),chat_client=client)
        summary=pipeline.summary();cases=cases_for(summary)
        if summary['answer_model']!='gpt-4o-mini' or summary['local_only']:raise ValueError('Evaluation provider metadata is incorrect')
        if len(cases)!=24 or set(summary['jurisdictions'])!={'EU','US','CA'}:raise ValueError('Unexpected evaluation scope')
        if case_ids:
            requested=set(case_ids)
            if len(requested)!=len(case_ids) or not requested<={c['id'] for c in cases}:raise ValueError('Unknown or repeated evaluation case')
            cases=[c for c in cases if c['id'] in requested]
        if default_labels:
            for case in cases:
                if case['suite']=='five_product_label_development':
                    case['payload'].pop('documents',None)
                    case['payload']['include_nondefault']=False
        plan=json.loads((ROOT/'data/runs/corpus_update_2026-10-09/corpus_plan.json').read_text(encoding='utf-8'))
        protected={d['version_id'] for d in plan['protected_exclusions']}
        if any(d['version_id'] in protected for d in summary['documents']):raise ValueError('Protected document entered evaluation')
        names={*CODE,'eval/ra3_corpus_eval.py','eval/ra3_openai_eval.py','rag/openai_chat.py'}
        names.update(str(p.relative_to(ROOT)).replace('\\','/') for folder in ('rag','preprocess','review_viewer') for p in (ROOT/folder).glob('*.py'))
        hashes={n:sha256(ROOT/n) for n in sorted(names) if (ROOT/n).is_file()}
        frozen={'summary':summary,'cases':cases,'code_sha256':hashes,
            'provider':'openai','model':'gpt-4o-mini','destination':'https://api.openai.com/v1/chat/completions',
            'max_new_api_attempts':max_calls,'case_count':len(cases),'case_selection':case_ids,'no_availability_probe':True,
            'label_request_mode':'default_search_without_document_pin' if default_labels else 'historical_pinned',
            'automatic_http_retries':0,'context_tokens':client.config.num_ctx,'max_answer_tokens':client.output_tokens,
            'gold_sent':False,'semantic_accuracy_certified':False,'automatic_promotion':False,
            'source_cards_sha256':sha256(ROOT/'data/runs/corpus_update_2026-10-09/multi_product_eval/source_cards.json')}
        path=out/'frozen.json'
        if path.exists() and json.loads(path.read_text(encoding='utf-8'))!=frozen:raise ValueError('Frozen API evaluation changed')
        write_json(path,frozen);write_json(out/'cases.json',cases)
        rows=[]
        def api_usage():
            ledger=json.loads(client.ledger_path.read_text(encoding='utf-8')) if client.ledger_path.exists() else {'attempts':[]}
            own=[a for a in ledger['attempts'] if a.get('evaluation_out')==str(out)]
            return {'attempts':len(own),'prompt_tokens':sum(a.get('prompt_tokens') or 0 for a in own),
                'completion_tokens':sum(a.get('completion_tokens') or 0 for a in own),'max_new_attempts':max_calls}
        try:
            for number,case in enumerate(cases,1):
                if any(sha256(ROOT/n)!=h for n,h in hashes.items()):raise ValueError('API evaluation code changed')
                client.case=case['id'];dest=out/'responses'/(case['id']+'.json');dest.parent.mkdir(exist_ok=True)
                write_json(out/'progress.json',{'stage':'evaluating_openai_rag','current_case':case['id'],
                    'completed_cases':len(rows),'total_cases':len(cases),'api_usage':api_usage(),'updated_at':timestamp()})
                print(f'[{number}/{len(cases)}] {case["id"]} {case["topic"]}',flush=True)
                if dest.exists():
                    report=json.loads(dest.read_text(encoding='utf-8'))
                    if (report['question']!=case['question'] or report['index']['chunks_sha256']!=summary['chunks_sha256']
                        or report.get('generation_provider')!='openai'):raise ValueError('Saved API response differs from frozen inputs')
                else:
                    write_json(dest.with_suffix('.request.json'),case['payload']);payload=case['payload']
                    report=pipeline.ask(payload['question'],payload.get('products'),payload.get('jurisdictions'),
                        payload.get('documents'),include_nondefault=payload.get('include_nondefault',False))
                    write_json(dest,report)
                row=score(case,report);rows.append(row)
                write_json(out/'automatic.json',{'summary':summarize(rows),'rows':rows,'provider':'openai','model':'gpt-4o-mini',
                    'api_usage':api_usage(),'semantic_accuracy_certified':False})
                print(json.dumps({'case':case['id'],'status':row['status'],'page_hit':row['gold_page_hit@5'],
                    'items':row['items'],'seconds':row['seconds'],'api_usage':api_usage()},ensure_ascii=False),flush=True)
                if client.halted or any(c.get('status')=='error' for unit in report['results'] for c in unit.get('generation_telemetry',[])):
                    raise RuntimeError('API error/quota reached; inspect saved failure before continuation')
            final={'n':len(rows),'summary':summarize(rows),'provider':'openai','model':'gpt-4o-mini','api_usage':api_usage(),
                'semantic_adjudication':'pending_source_review','answer_accuracy_certified':False,
                'all_requests_exclude_gold':True,'source_cards_sha256':frozen['source_cards_sha256']}
            write_json(out/'completed.json',final)
            write_json(out/'progress.json',{'stage':'generation_complete_source_review_pending','completed_cases':len(rows),
                'total_cases':len(cases),'api_usage':api_usage(),'updated_at':timestamp()})
            return final
        except BaseException as exc:
            message=type(exc).__name__+': '+str(exc)
            if client.secret:message=message.replace(client.secret,'[REDACTED]')
            write_json(out/'progress.json',{'stage':'requires_attention','current_case':client.case,'completed_cases':len(rows),
                'total_cases':len(cases),'api_usage':api_usage(),'error':message,'updated_at':timestamp()})
            raise
        finally:
            pipeline.close();client.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--release',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--max-api-calls',type=int,default=60)
    parser.add_argument('--case-id',action='append',help='Development regression subset; does not measure full/general accuracy')
    parser.add_argument('--default-labels',action='store_true')
    args=parser.parse_args();run(args.release,args.out,args.max_api_calls,args.case_id,args.default_labels)
