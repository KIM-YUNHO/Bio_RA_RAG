"""Local generation with token preflight and failure-preserving telemetry."""
from copy import deepcopy
import time
from preprocess.qwen_adapter import QwenAdapter
from rag.local_tokenizer import for_model


class GenerationFailure(RuntimeError):
    def __init__(self, message, telemetry):
        super().__init__(message);self.telemetry=telemetry


class LocalChat:
    def __init__(self, config):
        self.config=config.qwen;self.adapter=QwenAdapter(self.config);self.calls=[]
        # Source-ID selection remains opt-in until it improves development recall.
        self.source_selection=False

    @property
    def tokenizer(self): return for_model(self.config.model)

    @property
    def output_tokens(self): return self.config.num_predict

    @property
    def input_limit(self): return self.config.num_ctx-self.output_tokens-256

    def release(self): self.adapter.unload()

    def __call__(self,messages,json_schema=None,provider=None):
        name=(json_schema or {}).get('name','text')
        output=min(1000,self.output_tokens) if name=='query_understanding' else self.output_tokens
        estimated=self.tokenizer.messages(messages)
        telemetry={'schema':name,'model_requested':self.config.model,'model_digest':self.tokenizer.model_digest,
            'context_tokens':self.config.num_ctx,'max_output_tokens':output,
            'estimated_prompt_tokens':estimated,'token_count_basis':self.tokenizer.basis,
            'framing_safety_tokens':256,'prompt_tokens':0,'completion_tokens':0,
            'messages':deepcopy(messages)}
        self.calls.append(telemetry)
        if estimated+output+256>self.config.num_ctx:
            telemetry.update(status='input_budget_exceeded',finish_reason=None)
            raise GenerationFailure('예약한 출력 공간을 포함해 입력 토큰 예산을 초과했습니다.',telemetry)
        body={'model':self.config.model,'stream':False,'think':False,
            'keep_alive':'0' if name=='query_understanding' else self.config.keep_alive,
            'messages':messages,'options':{'temperature':0,'seed':0,'num_ctx':self.config.num_ctx,'num_predict':output}}
        if json_schema:body['format']=json_schema['schema']
        started=time.monotonic()
        try:
            reply=self.adapter.request('/api/chat',body)
        except Exception as exc:
            telemetry.update(status='transport_error',seconds=round(time.monotonic()-started,3),error=f'{type(exc).__name__}: {exc}')
            raise GenerationFailure(str(exc),telemetry) from exc
        text=reply.get('message',{}).get('content','')
        telemetry.update(status='completed',done=reply.get('done'),finish_reason=reply.get('done_reason'),
            model_reported=reply.get('model',self.config.model),raw_text=text,
            prompt_tokens=reply.get('prompt_eval_count') or 0,completion_tokens=reply.get('eval_count') or 0,
            seconds=round(time.monotonic()-started,3),
            durations={k:round(reply[k]/1e9,4) for k in ['total_duration','load_duration','prompt_eval_duration','eval_duration'] if k in reply})
        if not reply.get('done') or reply.get('done_reason')=='length' or not text:
            telemetry['status']='generation_incomplete' if text else 'empty_response'
            raise GenerationFailure('로컬 모델 생성 실패: '+str(reply.get('done_reason') or 'incomplete'),telemetry)
        return {'text':text,'model_reported':telemetry['model_reported'],
            'prompt_tokens':telemetry['prompt_tokens'],'completion_tokens':telemetry['completion_tokens'],
            'finish_reason':telemetry['finish_reason'],'telemetry':telemetry}
