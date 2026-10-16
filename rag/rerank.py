"""Optional local cross-encoder. Rankings are relevance signals, not evidence.

No default provider download, hidden API use, question IDs or answer values.
Caller supplies an already scope-filtered broad candidate pool.
"""
import hashlib
import json
from pathlib import Path
import re

MODEL='BAAI/bge-reranker-v2-m3'
REVISION='953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e'
VERSION='local-cross-encoder-2'
# Qwen3-Reranker scores relevance as P("yes") of a causal LM after this chat template.
QWEN3_PREFIX=('<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the '
              'Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n')
QWEN3_SUFFIX='<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
QWEN3_INSTRUCT='Given a regulatory question, judge whether the passage states the requested fact'


def _compat_remote_xlm_roberta():
    """Remote XLM-R model code (e.g. jina-reranker-v2) imports a helper removed in transformers 5.
    Restore the original helper only when it is missing; the remote code itself is not edited."""
    import torch
    from transformers.models.xlm_roberta import modeling_xlm_roberta as m
    if not hasattr(m,'create_position_ids_from_input_ids'):
        def create_position_ids_from_input_ids(input_ids,padding_idx,past_key_values_length=0):
            mask=input_ids.ne(padding_idx).int()
            return ((torch.cumsum(mask,dim=1).type_as(mask)+past_key_values_length)*mask).long()+padding_idx
        m.create_position_ids_from_input_ids=create_position_ids_from_input_ids


class LocalReranker:
    """kind 'cross_encoder': a sequence-classification head scores (query, passage) pairs.
    kind 'qwen3_yes_no': a causal LM scores P("yes") after the Qwen3-Reranker template.
    trust_remote_code runs model code shipped with the (already downloaded) weights."""
    def __init__(self,model=MODEL,revision=REVISION,device=None,batch_size=4,max_length=512,cache_path=None,
                 kind='cross_encoder',trust_remote_code=False):
        if not 1<=batch_size<=16 or not 128<=max_length<=1024:raise ValueError('invalid bounded reranker settings')
        if kind not in ('cross_encoder','qwen3_yes_no'):raise ValueError('unknown reranker kind')
        import torch
        from transformers import AutoTokenizer,AutoModelForSequenceClassification,AutoModelForCausalLM
        self.torch=torch;self.device=device or ('cuda' if torch.cuda.is_available() else 'cpu');self.kind=kind
        options=dict(revision=revision,local_files_only=True,trust_remote_code=trust_remote_code)
        if trust_remote_code:_compat_remote_xlm_roberta()
        self.tokenizer=AutoTokenizer.from_pretrained(model,padding_side='left' if kind=='qwen3_yes_no' else 'right',**options)
        dtype=torch.float16 if self.device=='cuda' else torch.float32
        loader=AutoModelForCausalLM if kind=='qwen3_yes_no' else AutoModelForSequenceClassification
        self.model=loader.from_pretrained(model,dtype=dtype,**options).to(self.device).eval()
        if kind=='qwen3_yes_no':
            self.yes_id=self.tokenizer.convert_tokens_to_ids('yes');self.no_id=self.tokenizer.convert_tokens_to_ids('no')
        self.batch_size=batch_size;self.max_length=max_length;self.model_name=model;self.revision=revision
        self.cache_path=Path(cache_path) if cache_path else None;self.cache={}
        if self.cache_path and self.cache_path.exists():
            for line in self.cache_path.read_text(encoding='utf-8').splitlines():
                record=json.loads(line);self.cache[record['key']]=record['score']

    def score(self,query,passages):
        keys=[hashlib.sha256(json.dumps([VERSION,self.model_name,self.revision,self.max_length,self.device,query,p],ensure_ascii=False).encode()).hexdigest() for p in passages]
        missing=list(dict.fromkeys(k for k in keys if k not in self.cache));lookup=dict(zip(keys,passages))
        for start in range(0,len(missing),self.batch_size):
            batch=missing[start:start+self.batch_size]
            if self.kind=='qwen3_yes_no':
                texts=[QWEN3_PREFIX+f'<Instruct>: {QWEN3_INSTRUCT}\n<Query>: {query}\n<Document>: {lookup[k]}'+QWEN3_SUFFIX for k in batch]
                inputs=self.tokenizer(texts,padding=True,truncation=True,max_length=self.max_length,return_tensors='pt').to(self.device)
                with self.torch.inference_mode():
                    last=self.model(**inputs).logits[:,-1,:]
                    pair=self.torch.stack([last[:,self.no_id],last[:,self.yes_id]],dim=1).float()
                    scores=self.torch.log_softmax(pair,dim=1)[:,1].cpu().tolist()
            else:
                inputs=self.tokenizer([[query,lookup[k]] for k in batch],padding=True,truncation=True,max_length=self.max_length,return_tensors='pt').to(self.device)
                with self.torch.inference_mode():scores=self.model(**inputs,return_dict=True).logits.view(-1).float().cpu().tolist()
            for key,value in zip(batch,scores):
                self.cache[key]=float(value)
                if self.cache_path:
                    self.cache_path.parent.mkdir(parents=True,exist_ok=True)
                    with self.cache_path.open('a',encoding='utf-8') as stream:stream.write(json.dumps({'key':key,'score':float(value)})+'\n')
        return [self.cache[k] for k in keys]

    def rank(self,query,hits,chunks,limit=80):
        if not 1<=limit<=150:raise ValueError('reranker candidate limit must be 1..150')
        pool=list(dict.fromkeys(i for _,i in hits))[:limit]
        passages=[chunks[i]['header']+'\n'+re.sub(r'<[^>]+>',' ',chunks[i]['text']) for i in pool]
        scores=self.score(query,passages)
        return sorted(zip(scores,pool),key=lambda x:-x[0])

    def close(self):
        del self.model
        if self.device=='cuda':self.torch.cuda.empty_cache()
