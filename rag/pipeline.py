"""Shared CLI/service entry: understand -> plan -> retrieve -> answer with citations."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import time

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'

import numpy as np
from preprocess.hybrid_config import load_config
from preprocess.qwen_adapter import QwenAdapter
from rag import answer
from rag.plan_query import plan
from rag.search import filter_idx, load_index
from rag.understand_query import understand
from rag.request_scope import bind_request_scope
from rag.local_chat import LocalChat
from review_viewer.data import Bundle
from rag.release import load_bundle, readiness
from rag.service_scope import allowed as service_allowed

VERSION = 'rag-pipeline-22-independent-question-contract'


class RAGPipeline:
    def __init__(self, run=None, candidate=None, config=None, review_url='http://127.0.0.1:8766',
                 bundle=None, chat_client=None, encoder_factory=None, retrieval_pages=5,
                 retrieval_fn=None,selection_strategy=None,query_strategy=None):
        if type(retrieval_pages) is not int or not 1 <= retrieval_pages <= 20:
            raise ValueError('검색 페이지 수는 1~20 사이의 정수여야 합니다.')
        self.retrieval_pages = retrieval_pages
        self.retrieval_fn=retrieval_fn
        self.selection_strategy=selection_strategy
        self.query_strategy=query_strategy
        if query_strategy is not None and not hasattr(selection_strategy,'catalog_for_query'):
            raise ValueError('질문 요구 계약은 이를 검증하는 답변 선택기와 함께 사용해야 합니다.')
        self.bundle = bundle or load_bundle(run,candidate)
        self.config = config or load_config()
        self.review_url = review_url
        self.client = chat_client or LocalChat(self.config)
        self.encoder_factory = encoder_factory
        self._shared_encoder = None
        chunks, vectors, meta = load_index('bge-m3',self.bundle.candidate)
        if vectors.shape != (len(chunks),meta['dim']) or not np.isfinite(vectors).all() or meta['n_chunks'] != len(chunks) or meta.get('truncated_inputs') != 0:
            raise ValueError('후보 임베딩의 크기·개수·유효성 검사가 실패했습니다.')
        if meta['chunks_sha256'] != self.bundle.chunks_sha256:
            raise ValueError('전처리 후보와 임베딩 버전이 다릅니다.')
        self.index_meta = meta
        service_chunks = [c for c in chunks if service_allowed(c)]
        from rag.regulatory_scope import approval_source
        self.approval_documents={(c['document_id'],c['version_id']) for c in service_chunks if approval_source(c)}
        eligible = [chunks[i] for i in filter_idx(chunks) if service_allowed(chunks[i])]
        counts = Counter((c['document_id'],c['version_id']) for c in eligible)
        research_counts = Counter((chunks[i]['document_id'],chunks[i]['version_id']) for i in filter_idx(chunks,include_nondefault=True) if service_allowed(chunks[i]))
        self.documents = []
        for doc in self.bundle.summary()['documents']:
            own = [c for c in service_chunks if (c['document_id'],c['version_id']) == (doc['document_id'],doc['version_id'])]
            if not own:continue
            self.documents.append({**doc,'chunks':len(own),'eligible_chunks':counts[(doc['document_id'],doc['version_id'])],
                'eligible_with_nondefault':research_counts[(doc['document_id'],doc['version_id'])],
                'jurisdiction':own[0]['jurisdiction'] if own else None,'product_id':own[0]['product_id'] if own else None,
                'doc_group':own[0].get('doc_group'),'doc_date':own[0].get('doc_date')})
        self.indexed_products=sorted({c['product_id'] for c in service_chunks},key=str)
        self.product_readiness=readiness(service_chunks)
        self.products=sorted(set(self.indexed_products)|{r['product_id'] for r in self.product_readiness},key=str)
        if getattr(self.bundle,'release',None) and set(self.bundle.release['expected_products'])!=set(self.products):
            raise ValueError('릴리스의 기대 상품과 등록된 상품이 다릅니다.')
        self.agencies = sorted({c['jurisdiction'] for c in service_chunks},key=str)

    def summary(self):
        return {'pipeline_version':VERSION,'run_name':self.bundle.root.name,'documents':self.documents,
            'products':self.products,'jurisdictions':self.agencies,'chunks':self.index_meta['n_chunks'],
            'indexed_products':self.indexed_products,'product_readiness':self.product_readiness,
            'complete_corpus':False,
            'eligible_chunks':sum(d['eligible_chunks'] for d in self.documents),
            'chunks_sha256':self.index_meta['chunks_sha256'],'embedding_model':self.index_meta['model'],
            'answer_model':getattr(self.client,'model',self.config.qwen.model),'review_url':self.review_url,
            'retrieval_pages':self.retrieval_pages,
            'query_strategy':getattr(self.query_strategy,'VERSION',None),
            'local_only':getattr(self.client,'local_only',True),'generation_provider':getattr(self.client,'provider','ollama'),
            'numeric_accuracy_certified':False}

    def validate_request(self, question, products=None, jurisdictions=None, documents=None, include_nondefault=False):
        if not isinstance(question,str) or not question.strip() or len(question)>2000:
            raise ValueError('질문은 1~2,000자로 입력하세요.')
        if re.search(r'\bPMDA\b|일본\s*(?:RA|규제기관|허가당국)|ClinicalTrials\.gov',question,re.I):
            raise ValueError('현재 서비스의 기관 범위는 EMA·FDA·HC입니다.')
        if type(include_nondefault) is not bool:raise ValueError('기본 제외 문서 포함 여부는 boolean이어야 합니다.')
        for values,allowed in ((products,self.products),(jurisdictions,self.agencies),(documents,[d['key'] for d in self.documents])):
            if values is not None and (not isinstance(values,list) or any(not isinstance(x,str) or x not in allowed for x in values)):
                raise ValueError('등록된 검색 범위를 선택하세요.')

    def ask(self, question, products=None, jurisdictions=None, documents=None, progress=None, include_nondefault=False):
        self.validate_request(question,products,jurisdictions,documents,include_nondefault)
        notify = progress or (lambda **event:None)
        started = time.monotonic()
        from rag.regulatory_scope import requests_approval_letter
        approval=requests_approval_letter(question)
        effective_nondefault=include_nondefault or approval
        corpus = answer.Corpus('bge-m3',self.bundle.candidate,local_only=True,bundle=self.bundle,review_url=self.review_url,include_nondefault=effective_nondefault)
        selected_docs = [d for d in self.documents if (not documents or d['key'] in documents)
            and (not products or d['product_id'] in products) and (not jurisdictions or d['jurisdiction'] in jurisdictions)]
        if approval:
            selected_docs=[d for d in selected_docs if (d['document_id'],d['version_id']) in self.approval_documents]
        corpus.allowed_documents = {(d['document_id'],d['version_id']) for d in selected_docs}
        eligible_ids = set(filter_idx(corpus.chunks,include_nondefault=effective_nondefault))
        available = [c for i,c in enumerate(corpus.chunks) if i in eligible_ids
            and (c['document_id'],c['version_id']) in corpus.allowed_documents]
        available_products = sorted({c['product_id'] for c in available},key=str)
        available_agencies = sorted({c['jurisdiction'] for c in available},key=str)
        results = []
        timings={};report=None
        self.last_failure=None
        all_call_start=len(getattr(self.client,'calls',[]))
        understanding_calls=[]
        try:
            notify(stage='understanding',message='질문에서 제품·기관·시험·분석 범위를 해석하고 있습니다.')
            stage_started=time.monotonic()
            interpreter=self.query_strategy.understand if self.query_strategy is not None else understand
            understood = interpreter(question,provider=getattr(self.client,'provider','ollama'),chat_client=self.client)
            timings['understanding']=round(time.monotonic()-stage_started,3)
            understanding_calls=list(getattr(self.client,'calls',[])[all_call_start:])
            understood = bind_request_scope(question,understood,available_products,available_agencies,
                selected_products=products,selected_agencies=jurisdictions,document_scope=bool(documents),
                documents=self.documents)
            understood['source_question']=question
            units = plan(understood,agencies=available_agencies,products=available_products)
            notify(stage='retrieving',message='기존 BGE-M3·BM25 색인에서 검색하고 있습니다.',units=len(units),understanding=understood)
            stage_started=time.monotonic()
            if available and units:
                if getattr(self.client,'provider','ollama')=='openai':
                    if self._shared_encoder is None:
                        self._shared_encoder=(self.encoder_factory or (lambda:answer.Embedder('bge-m3',local_only=True)))()
                    corpus._emb=self._shared_encoder
                elif self.encoder_factory:
                    corpus._emb=self.encoder_factory()
                qv=corpus.encode([understood['english_query']])[0]
            else:qv=None
            timings['embedding']=round(time.monotonic()-stage_started,3)
            stage_started=time.monotonic()
            retrieved = (self.retrieval_fn or answer.retrieve)(corpus,understood,qv,use_dense=qv is not None,k=self.retrieval_pages,policy='general',
                agencies=available_agencies,products=available_products)
            timings['retrieval']=round(time.monotonic()-stage_started,3)
            corpus.release_encoder()
            for n,(unit,pages) in enumerate(retrieved,1):
                notify(stage='answering',message=f'{answer.AGENCY.get(unit["jurisdiction"],"미상")} · {unit["product"]} 근거로 답변하고 있습니다.',
                    current=n,total=len(retrieved))
                stage_started=time.monotonic();unit_call_start=len(getattr(self.client,'calls',[]))
                corpus.evidence_adapter.last_evidence={};corpus.evidence_adapter.last_budget={};corpus.evidence_adapter.last_omitted=[]
                try:
                    result = answer.answer_unit(question,understood,unit,pages,corpus,qv=qv,
                        provider=getattr(self.client,'provider','ollama'),chat_client=self.client,selection_strategy=self.selection_strategy)
                except Exception as exc:
                    adapter=corpus.evidence_adapter
                    result = {'status':'error','unit':unit,'items':[],
                        'evidence':getattr(adapter,'last_evidence',{}),'answer_ko':'이 검색 단위의 답변 생성에 실패했습니다.',
                        'error':f'{type(exc).__name__}: {exc}','prompt_tokens':0,'completion_tokens':0,
                        'failure_stage':'generation_or_answer_validation','context_budget':getattr(adapter,'last_budget',{}),
                        'omitted_chunks':list(adapter.last_omitted)}
                result['retrieved_pages'] = [{k:p.get(k) for k in ('document_id','version_id','pdf_page','title','best_chunk','score')} for p in pages]
                result['seconds']=round(time.monotonic()-stage_started,3)
                calls=list(getattr(self.client,'calls',[])[unit_call_start:])
                if calls:
                    result['generation_telemetry']=calls
                    result['prompt_tokens']=sum(c.get('prompt_tokens',0) for c in calls)
                    result['completion_tokens']=sum(c.get('completion_tokens',0) for c in calls)
                results.append(result)
            report={'version':VERSION,'question':question,'understanding':understood,
                'document_routing':{'requested_include_nondefault':include_nondefault,'effective_include_nondefault':effective_nondefault,
                    'approval_letter_requested':approval,'added_document_family':'decision_letter' if approval and not include_nondefault else None},
                'scope':{'products':products or [],'jurisdictions':jurisdictions or [],'documents':documents or [],
                    'resolution':understood['request_scope'],
                    'include_nondefault':include_nondefault,
                    'excluded_products':[r for r in self.product_readiness if r['product_id'] in (products or []) and not r['indexed_chunks']],
                    'selected_documents':[d['key'] for d in selected_docs],
                    'excluded_documents':[{'key':d['key'],'title':d['title'],'reason':'no_search_eligible_chunks'} for d in selected_docs if not d['eligible_with_nondefault' if effective_nondefault else 'eligible_chunks']]},
                'index':{'directory':str(self.bundle.candidate),'chunks_sha256':self.index_meta['chunks_sha256'],
                    'chunks':self.index_meta['n_chunks'],'model':self.index_meta['model'],'active_index_updated':False},
                'plan':[unit for unit,_ in retrieved],'results':results,
                'status':'scope_conflict' if understood['request_scope']['conflicts'] else
                    'scope_unavailable' if products and not available and any(not r['indexed_chunks'] and r['product_id'] in products for r in self.product_readiness) else
                    'completed_with_errors' if any(r['status']=='error' for r in results) else 'completed',
                'numeric_accuracy_certified':False,'local_only':getattr(self.client,'local_only',True),
                'generation_provider':getattr(self.client,'provider','ollama'),
                'retrieval_pages':self.retrieval_pages,
                'timings':timings,'understanding_telemetry':understanding_calls,
                'usage':{'input_tokens':understood.get('prompt_tokens',0)+sum(r.get('prompt_tokens',0) or 0 for r in results),
                    'output_tokens':understood.get('completion_tokens',0)+sum(r.get('completion_tokens',0) or 0 for r in results)},
                'seconds':round(time.monotonic()-started,3),'created_at':datetime.now(timezone.utc).isoformat()}
            return report
        except Exception as exc:
            self.last_failure={'version':VERSION,'question':question,'error':f'{type(exc).__name__}: {exc}',
                'index_sha256':self.index_meta['chunks_sha256'],'timings':timings,
                'generation_telemetry':list(getattr(self.client,'calls',[])[all_call_start:])}
            raise
        finally:
            if corpus._emb is not None:
                corpus.release_encoder()
            corpus.bm.db.close()
            if hasattr(self.client,'release'):
                try:self.client.release()
                except (OSError,RuntimeError) as exc:
                    if report is not None:report['runtime_warnings']=[f'Model release failed: {exc}']
            if hasattr(self.client,'calls'):del self.client.calls[all_call_start:]

    def close(self):
        """Release the reusable API-evaluation encoder at the end of its run."""
        encoder,self._shared_encoder=self._shared_encoder,None
        if encoder is not None and hasattr(encoder,'close'):encoder.close()
        del encoder
        import gc
        gc.collect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--candidate',type=Path)
    parser.add_argument('--config',type=Path,default=Path('configs/preprocess.local.json'))
    parser.add_argument('--review-url',default='http://127.0.0.1:8766')
    parser.add_argument('--query',required=True)
    parser.add_argument('--product',action='append')
    parser.add_argument('--jurisdiction',action='append')
    parser.add_argument('--document',action='append')
    parser.add_argument('--include-nondefault',action='store_true',help='Explicitly include labels/protocols excluded from default search; evaluation remains excluded')
    parser.add_argument('--retrieval-pages',type=int,default=5,choices=range(1,11))
    parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    if args.output and args.output.exists():parser.error('Choose a new output file')
    pipeline = RAGPipeline(args.run,args.candidate,load_config(args.config),args.review_url,retrieval_pages=args.retrieval_pages)
    report = pipeline.ask(args.query,args.product,args.jurisdiction,args.document,include_nondefault=args.include_nondefault)
    output = json.dumps(report,ensure_ascii=False,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x',encoding='utf-8') as stream:stream.write(output+'\n')
    print(output)


if __name__ == '__main__':main()
