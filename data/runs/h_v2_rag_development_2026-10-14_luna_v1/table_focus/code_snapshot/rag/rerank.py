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
VERSION='local-cross-encoder-1'


class LocalReranker:
    def __init__(self,model=MODEL,revision=REVISION,device=None,batch_size=4,max_length=512,cache_path=None):
        if not 1<=batch_size<=16 or not 128<=max_length<=1024:raise ValueError('invalid bounded reranker settings')
        import torch
        from transformers import AutoTokenizer,AutoModelForSequenceClassification
        self.torch=torch;self.device=device or ('cuda' if torch.cuda.is_available() else 'cpu')
        self.tokenizer=AutoTokenizer.from_pretrained(model,revision=revision,local_files_only=True)
        dtype=torch.float16 if self.device=='cuda' else torch.float32
        self.model=AutoModelForSequenceClassification.from_pretrained(model,revision=revision,local_files_only=True,
            torch_dtype=dtype).to(self.device).eval()
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
