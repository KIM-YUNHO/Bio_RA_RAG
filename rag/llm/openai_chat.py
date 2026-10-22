"""Budgeted OpenAI transport for RA framework requests and scoped experiments."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import json,time
from pathlib import Path
from types import SimpleNamespace
from rag.llm.llm import settings
from rag.llm.local_chat import GenerationFailure
from preprocess.ingest import stable_hash
from preprocess.files import write_json, exclusive

ROOT=Path(__file__).resolve().parents[2]
KST=timezone(timedelta(hours=9))


class ApiTokenizer:
    basis='tiktoken_o200k_base_with_explicit_framing_allowance'
    model_digest='openai:gpt-4o-mini:o200k_base'
    def __init__(self, model='gpt-4o-mini'):
        import tiktoken
        self.encoding=tiktoken.encoding_for_model(model)
        self.model_digest='openai:'+model+':'+self.encoding.name
    def one(self,text):return len(self.encoding.encode(text,disallowed_special=()))
    def messages(self,messages):return sum(self.one(m['content'])+self.one(m['role'])+4 for m in messages)+32


class EvaluationAPIChat:
    provider='openai';model='gpt-4o-mini';local_only=False;source_selection=True
    def __init__(self,out,max_new_calls=60,client=None,tokenizer=None,ledger_dir=None,model='gpt-4o-mini'):
        if type(max_new_calls) is not int or not 1<=max_new_calls<=60:raise ValueError('Evaluation API call cap must be 1..60')
        if model not in ('gpt-4o-mini','gpt-4.1-2025-04-14','gpt-5.6-luna'):raise ValueError('Unsupported evaluation model')
        self.model=model
        self.reasoning_effort='none' if model=='gpt-5.6-luna' else None
        self.out=Path(out).resolve();self.out.mkdir(parents=True,exist_ok=True)
        self.ledger_dir=Path(ledger_dir or ROOT/'data/runs/model_quota');self.ledger_dir.mkdir(parents=True,exist_ok=True)
        self.day=datetime.now(KST).date().isoformat();self.ledger_path=self.ledger_dir/(self.day+'.json')
        self.calls=[];self.case=None;self.max_new_calls=max_new_calls;self.attempts=0;self.halted=None
        # An evaluation envelope, independent of the local Ollama context size.
        # Exact preflight still includes source-ID schema and output reservation.
        self.config=SimpleNamespace(model=self.model,num_ctx=16384,num_predict=3000)
        self.tokenizer=tokenizer or ApiTokenizer(model)
        self.secret=''
        if client is None:
            config=settings('openai');self.secret=config['api_key']
            # Evaluation goes directly to the explicitly requested provider.
            from openai import OpenAI
            import httpx
            client=OpenAI(api_key=self.secret,base_url='https://api.openai.com/v1',timeout=90,max_retries=0,
                http_client=httpx.Client(trust_env=False,follow_redirects=False,timeout=90))
        self.sdk=client

    @property
    def output_tokens(self):return self.config.num_predict
    @property
    def input_limit(self):return self.config.num_ctx-self.output_tokens-256
    def release(self):pass
    def close(self):
        if hasattr(self.sdk,'close'):self.sdk.close()

    def _reserve(self,key,request_path,name,output):
        if self.halted:raise RuntimeError('API evaluation halted: '+self.halted)
        if datetime.now(KST).date().isoformat()!=self.day:raise RuntimeError('New KST day; review the new daily budget before continuation')
        with exclusive(self.ledger_dir):
            ledger=json.loads(self.ledger_path.read_text(encoding='utf-8')) if self.ledger_path.exists() else {
                'date_kst':self.day,'scope':'This project evaluation only; other API usage is not observable','attempts':[]}
            own=[a for a in ledger['attempts'] if a.get('evaluation_out')==str(self.out)]
            if any(a.get('request_key')==key for a in own):raise RuntimeError('Prior in-flight API request is never automatically repeated')
            if len(own)>=self.max_new_calls:
                self.halted='project_evaluation_call_cap';raise RuntimeError('API evaluation call cap reached')
            record={'case':self.case,'schema':name,'model':self.model,'status':'reserved',
                'evaluation_out':str(self.out),'request_key':key,'request_path':str(request_path),
                'max_output_tokens':output,'reserved_at':datetime.now(KST).isoformat()}
            ledger['attempts'].append(record);write_json(self.ledger_path,ledger)
            self.attempts=len(own)+1
        return record

    def _finish(self,key,values):
        with exclusive(self.ledger_dir):
            ledger=json.loads(self.ledger_path.read_text(encoding='utf-8'))
            record=next(a for a in ledger['attempts'] if a.get('evaluation_out')==str(self.out) and a.get('request_key')==key)
            record.update(values);write_json(self.ledger_path,ledger)

    def __call__(self,messages,json_schema=None,provider=None):
        if json_schema:
            from rag.evidence.schema_budget import assert_supported
            assert_supported(json_schema.get('schema',json_schema))
        name=(json_schema or {}).get('name','text');output=1600 if name=='query_understanding' else self.output_tokens
        if json_schema and 'max_output_tokens' in json_schema:
            # Per-call output budget (a local field, never sent to the provider).
            output=int(json_schema['max_output_tokens'])
            json_schema={k:v for k,v in json_schema.items() if k!='max_output_tokens'}
        tokens=self.tokenizer.messages(messages)
        if json_schema:tokens+=self.tokenizer.one(json.dumps(json_schema,ensure_ascii=False,separators=(',',':')))
        telemetry={'provider':self.provider,'schema':name,'model_requested':self.model,
            'context_tokens':self.config.num_ctx,'max_output_tokens':output,'estimated_prompt_tokens':tokens,
            'token_count_basis':self.tokenizer.basis,'framing_safety_tokens':256,
            'prompt_tokens':0,'completion_tokens':0,'messages':deepcopy(messages)}
        self.calls.append(telemetry)
        if tokens+output+256>self.config.num_ctx:
            telemetry['status']='input_budget_exceeded';raise GenerationFailure('API input token budget exceeded',telemetry)
        if not self.case:raise RuntimeError('Set the evaluation case before generation')
        payload={'model':self.model,'messages':messages,'temperature':0,'seed':0,'max_tokens':output}
        if self.model=='gpt-5.6-luna':
            payload={'model':self.model,'messages':messages,'max_completion_tokens':output,
                'reasoning_effort':self.reasoning_effort}
            telemetry['reasoning_effort']=self.reasoning_effort
            # Opt-in only: Luna requests have been sent without sampling controls.
            if getattr(self,'sampling',None):
                payload.update(self.sampling);telemetry['sampling']=dict(self.sampling)
        if json_schema:payload['response_format']={'type':'json_schema','json_schema':{**json_schema,'strict':True}}
        key=stable_hash(payload);folder=self.out/'api_calls';folder.mkdir(exist_ok=True)
        request=folder/(key+'.request.json');response=folder/(key+'.response.json')
        if response.exists():
            saved=json.loads(response.read_text(encoding='utf-8'))
            if saved['request_key']!=key or saved['case']!=self.case:raise ValueError('API cache identity changed')
            if saved['status']!='completed':raise GenerationFailure('Failed API request is not retried automatically',saved)
            telemetry.update(saved,cache_reused=True)
            return {'text':saved['raw_text'],'model_reported':saved['model_reported'],'prompt_tokens':saved['prompt_tokens'],
                'completion_tokens':saved['completion_tokens'],'finish_reason':saved['finish_reason'],'telemetry':telemetry}
        write_json(request,{'case':self.case,'request_key':key,'payload':payload,'gold_sent':False})
        self._reserve(key,request,name,output);started=time.monotonic()
        try:
            reply=self.sdk.chat.completions.create(**payload);choice=reply.choices[0]
            telemetry.update(status='completed',raw_text=choice.message.content or '',finish_reason=choice.finish_reason,
                model_reported=reply.model,system_fingerprint=getattr(reply,'system_fingerprint',None),
                prompt_tokens=reply.usage.prompt_tokens if reply.usage else 0,
                completion_tokens=reply.usage.completion_tokens if reply.usage else 0)
            if choice.finish_reason!='stop' or not telemetry['raw_text']:raise ValueError('Incomplete or refused API generation')
        except Exception as exc:
            message=type(exc).__name__+': '+str(exc)
            if self.secret:message=message.replace(self.secret,'[REDACTED]')
            telemetry.update(status='error',error=message)
            if getattr(exc,'status_code',None) in (401,403,429):self.halted='provider_access_or_quota_error'
        telemetry.update(case=self.case,request_key=key,seconds=round(time.monotonic()-started,3))
        write_json(response,telemetry)
        self._finish(key,{k:telemetry.get(k) for k in ('status','prompt_tokens','completion_tokens','finish_reason','seconds','error')})
        if telemetry['status']!='completed':raise GenerationFailure(telemetry['error'],telemetry)
        return {'text':telemetry['raw_text'],'model_reported':telemetry['model_reported'],
            'prompt_tokens':telemetry['prompt_tokens'],'completion_tokens':telemetry['completion_tokens'],
            'finish_reason':telemetry['finish_reason'],'telemetry':telemetry}


class OpenAIPipelineChat:
    """The RA framework's selected model; three calls per request, no fallback.

    Reuses the existing audited transport and exact token preflight. This runs
    planning, synthesis and internal clause review, not a separate evaluator.
    Provider access is initialized only when a user request starts.
    """
    provider = 'openai'
    model = 'gpt-4o-mini'
    local_only = False
    source_selection = True

    def __init__(self, out_root=None, session_factory=None, tokenizer=None, model='gpt-4o-mini',
                 context_tokens=16384, sampling=None):
        if model not in ('gpt-4o-mini', 'gpt-5.6-luna'):
            raise ValueError('Unsupported RA framework model')
        if type(context_tokens) is not int or not 8192 <= context_tokens <= 131072:
            raise ValueError('RA context tokens must be 8192..131072')
        self.model = model
        self.reasoning_effort = 'none' if model == 'gpt-5.6-luna' else None
        self.out_root = Path(out_root or ROOT / 'data/runs/ra_pipeline_requests')
        # Project reading budget (prompt + output), not the provider's model limit.
        self.config = SimpleNamespace(model=self.model, num_ctx=context_tokens, num_predict=3000)
        self.sampling = dict(sampling) if sampling else None
        self.tokenizer = tokenizer or ApiTokenizer(self.model)
        self.calls = []
        self.session = None
        self.session_factory = session_factory or (lambda folder: EvaluationAPIChat(folder, max_new_calls=3, model=self.model))

    @property
    def output_tokens(self): return self.config.num_predict

    @property
    def input_limit(self): return self.config.num_ctx - self.output_tokens - 256

    def begin_request(self):
        from uuid import uuid4
        if self.session is not None:
            raise RuntimeError('Previous RA request is still active')
        folder = self.out_root / (datetime.now(KST).strftime('%Y-%m-%d_%H%M%S_') + uuid4().hex[:12])
        session = self.session_factory(folder)
        if session.model != self.model or session.provider != self.provider:
            session.close()
            raise ValueError(f'RA pipeline requires {self.model} at OpenAI')
        session.config = self.config
        session.sampling = self.sampling
        session.case = 'ra_pipeline_request'
        self.session = session
        self.calls = session.calls

    def __call__(self, messages, json_schema=None, provider=None):
        if provider not in (None, self.provider):
            raise ValueError('RA pipeline provider must be OpenAI')
        if self.session is None:
            self.begin_request()
        return self.session(messages, json_schema=json_schema, provider=self.provider)

    def release(self):
        if self.session is not None:
            self.session.close()
            self.session = None

    def close(self): self.release()
