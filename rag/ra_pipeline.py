"""RA research RAG: tasks -> hybrid retrieval -> reading -> checked synthesis.

An explicit alternative to the legacy numeric-selector path. The active release
is read-only. One planning, one synthesis and one selected-source clause review
call maximum, with no automatic retry.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from threading import RLock

from rag.pipeline import RAGPipeline
from rag import answer, ra_tasks, ra_retrieval, ra_answer, ra_timeline
from rag.ra_sources import GuidelineIndex, permitted
from rag.search import filter_idx
from rag.service_scope import allowed

VERSION = 'ra-research-rag-4.10.2'


class RAPipeline(RAGPipeline):
    def __init__(self, *args, guideline_snapshot=None, citation_mode='spans', research_rounds=1,
                 approval_timeline=False, retrieval_profile=None, **kwargs):
        if type(research_rounds) is not int or not 0 <= research_rounds <= 1:
            raise ValueError('Local research round cap must be 0 or 1')
        self.research_rounds = research_rounds
        # Experimental, opt-in: a question-type special case kept for comparison
        # while the general retrieval/generation path is fixed.
        self.approval_timeline = approval_timeline
        # Retrieval options (rag.ra_retrieval.DEFAULT_PROFILE keys); None keeps the defaults.
        self.retrieval_profile = dict(retrieval_profile) if retrieval_profile else None
        self._request_lock = RLock()
        self._ra_corpus = None
        self.warmup_info = {'ready':False}
        if citation_mode not in ('quote', 'spans'):
            raise ValueError('Unknown RA citation mode')
        self.citation_mode = citation_mode
        if kwargs.get('query_strategy') or kwargs.get('selection_strategy') or kwargs.get('retrieval_fn'):
            raise ValueError('RA research uses its own shared task/read/synthesis path')
        if kwargs.get('chat_client') is None:
            from rag.openai_chat import OpenAIPipelineChat
            kwargs['chat_client'] = OpenAIPipelineChat()
        super().__init__(*args, **kwargs)
        self.guidelines = GuidelineIndex(guideline_snapshot) if guideline_snapshot else None
        if self.guidelines:
            keys = sorted({(c['document_id'], c['version_id']) for c in self.guidelines.chunks if permitted(c['document_metadata'])})
            for n, identity in enumerate(keys, 1):
                own = [c for c in self.guidelines.chunks if (c['document_id'], c['version_id']) == identity]
                c = own[0]
                self.documents.append({'key': f'g{n}', 'document_id': identity[0], 'version_id': identity[1],
                    'product': '공통 지침', 'product_id': None, 'source': c['source'], 'jurisdiction': c['jurisdiction'],
                    'title': c['title'], 'pages': len(own), 'chunks': len(own),
                    'eligible_chunks': len(own), 'eligible_with_nondefault': len(own), 'role': 'guideline'})
            self.agencies = sorted(set(self.agencies) | {c['jurisdiction'] for c in self.guidelines.chunks})

    def summary(self):
        result = super().summary()
        result.update(pipeline_version=VERSION, mode='ra', answer_strategy=ra_answer.VERSION,
            citation_mode=self.citation_mode,
            product_index_chunks=self.index_meta['n_chunks'],
            chunks=self.index_meta['n_chunks'] + (len(self.guidelines.chunks) if self.guidelines else 0),
            query_strategy=ra_tasks.VERSION, retrieval_strategy=ra_retrieval.VERSION,
            generation_provider=getattr(self.client, 'provider', 'ollama'), default_llm_calls_maximum=3,
            reasoning_effort=getattr(self.client, 'reasoning_effort', None),
            guideline_snapshot=str(self.guidelines.path) if self.guidelines else None,
            guideline_chunks=len(self.guidelines.chunks) if self.guidelines else 0,
            capabilities={'task_planning': True, 'single_pass_retrieval_synthesis': True,
                          'gap_driven_research_loop': False,
                          'bounded_missing_facet_search': bool(self.research_rounds), 'issue_review_memo': True,
                          'regulator_meeting_question_draft': True},
            research_policy='pre_generation_missing_facet_candidates_only',
            local_research_rounds_maximum=self.research_rounds, additional_research_llm_calls=0,
            current_guideline_validity_verified=False, semantic_accuracy_certified=False)
        result['retrieval_readiness'] = deepcopy(self.warmup_info)
        result['search_object_reuse'] = 'serialized_process_cache_fixed_release_request_scope_reset'
        result['guideline_status_registry'] = self.guidelines.status_registry if self.guidelines else None
        result['excluded_guideline_documents'] = self.guidelines.excluded_documents if self.guidelines else []
        return result

    def retrieval_corpus(self):
        if self._ra_corpus is None:
            self._ra_corpus = answer.Corpus('bge-m3', self.bundle.candidate, local_only=True, bundle=self.bundle,
                review_url=self.review_url, include_nondefault=True, shared_cache=True)
        return self._ra_corpus

    def warmup(self):
        """Local-only startup work; no planning, answer or provider request."""
        with self._request_lock:
            started = time.monotonic()
            corpus = self.retrieval_corpus()
            if self._shared_encoder is None:
                self._shared_encoder = (self.encoder_factory or (lambda:answer.Embedder('bge-m3',local_only=True)))()
            corpus._emb = self._shared_encoder
            corpus.encode(['regulatory document evidence'])
            # Load the local reranker at startup so the first question does not pay for it.
            profile = {**ra_retrieval.DEFAULT_PROFILE, **(self.retrieval_profile or {})}
            if profile.get('rerank') and getattr(corpus, '_ra_reranker', None) is None:
                from rag.rerank import LocalReranker
                corpus._ra_reranker = LocalReranker(batch_size=8, max_length=1024)
            self.warmup_info = {'ready':True,'seconds':round(time.monotonic()-started,3),'provider_calls':0}
            return deepcopy(self.warmup_info)

    def ask(self, question, products=None, jurisdictions=None, documents=None, progress=None, include_nondefault=False,
            *, source_ids=None, context=None):
        self.validate_request(question, products, jurisdictions, documents, include_nondefault)
        user_question = question
        self._request_lock.acquire()
        started, call_start = time.monotonic(), len(getattr(self.client, 'calls', []))
        notify = progress or (lambda **event: None)
        self.last_failure = None
        corpus = None
        timings, evidence, groups, budget, research = {}, {}, [], {}, {}
        try:
            if hasattr(self.client, 'begin_request'):
                self.client.begin_request()
            selected_docs = [d for d in self.documents if (not documents or d['key'] in documents)
                and (not products or d['product_id'] in products or d.get('role') == 'guideline')
                and (not jurisdictions or d['jurisdiction'] in jurisdictions)]
            available_products = sorted({d['product_id'] for d in selected_docs if d['product_id'] is not None})
            available_agencies = sorted({d['jurisdiction'] for d in selected_docs})
            notify(stage='understanding', message='원질문에서 조사 항목과 필요한 문서 종류를 정하고 있습니다.')
            stamp = time.monotonic()
            understanding, _ = ra_tasks.interpret(question, self.client, available_products, available_agencies,
                selected_products=products, selected_agencies=jurisdictions, document_scope=bool(documents), documents=self.documents,
                context=context)
            # A follow-up is answered as its standalone question; the user's wording stays in the report.
            question = understanding['source_question']
            timings['understanding'] = round(time.monotonic() - stamp, 3)
            understanding_calls = deepcopy(getattr(self.client, 'calls', [])[call_start:])
            in_scope = understanding['request_scope']['status'] not in ('conflict', 'empty') and understanding['intent'] != 'out_of_scope'
            timeline = None
            if self.approval_timeline and in_scope and source_ids is None and ra_timeline.requested(question):
                # Approval dates sit in labelled fields of label/correspondence
                # documents that default retrieval excludes; read them by code.
                notify(stage='retrieving', message='범위 안 문서에서 제출일·허가일 필드를 읽고 있습니다.')
                stamp = time.monotonic()
                corpus = self.retrieval_corpus()
                corpus.allowed_documents = {(d['document_id'], d['version_id']) for d in selected_docs}
                scope_chunks = [corpus.chunks[int(i)] for i in filter_idx(corpus.chunks, include_nondefault=True)
                    if allowed(corpus.chunks[int(i)]) and permitted(corpus.chunks[int(i)].get('document_metadata') or {})
                    and (corpus.chunks[int(i)]['document_id'], corpus.chunks[int(i)]['version_id']) in corpus.allowed_documents]
                timeline = ra_timeline.answer(corpus, understanding, question, scope_chunks)
                timings['timeline'] = round(time.monotonic() - stamp, 3)
            if timeline is not None:
                evidence = timeline.pop('evidence')
                budget = {'mode': 'approval_timeline_fields', 'version': ra_timeline.VERSION, 'omitted': [],
                          'packets': len(evidence), 'semantic_approval': False}
                research = {'rounds': 0, 'searches': [], 'stop_reason': 'deterministic_approval_timeline',
                            'additional_llm_calls': 0}
                result = timeline
            elif in_scope:
                notify(stage='retrieving', message='조사 항목별로 제품 근거와 공통 지침을 검색하고 있습니다.')
                stamp = time.monotonic()
                corpus = self.retrieval_corpus()
                corpus.allowed_documents = {(d['document_id'], d['version_id']) for d in selected_docs}
                if any(set(t['source_roles']) - {'guideline'} for t in understanding['tasks']):
                    if self._shared_encoder is None:
                        self._shared_encoder = (self.encoder_factory or (lambda: answer.Embedder('bge-m3', local_only=True)))()
                    corpus._emb = self._shared_encoder
                groups = (ra_retrieval.oracle_groups(corpus, understanding, self.guidelines, source_ids, corpus.allowed_documents)
                    if source_ids is not None else ra_retrieval.retrieve(corpus, understanding, self.guidelines,
                        documents=corpus.allowed_documents, include_nondefault=include_nondefault, k=self.retrieval_pages,
                        profile=self.retrieval_profile))
                timings['retrieval'] = round(time.monotonic() - stamp, 3)
                stamp = time.monotonic()
                evidence, budget = ra_retrieval.context(corpus, groups, question, understanding, self.client, self.citation_mode,
                                                      guideline_index=self.guidelines)
                if source_ids is None:
                    from rag.ra_research import extend
                    evidence, budget, research = extend(corpus, groups, evidence, budget, question, understanding,
                        self.client, self.guidelines, max_rounds=self.research_rounds, citation_mode=self.citation_mode)
                else:
                    research = {'rounds': 0, 'searches': [], 'stop_reason': 'evaluation_source_ids_disable_research',
                                'additional_llm_calls': 0}
                timings['context'] = round(time.monotonic() - stamp, 3)
                notify(stage='answering', message='원문을 읽고 요청한 사실·차이·심사 맥락을 종합하고 있습니다.')
                stamp = time.monotonic()
                result = ra_answer.generate(question, understanding, evidence, self.client, self.citation_mode)
            if in_scope:
                if timeline is None:
                    timings['generation_and_review'] = round(time.monotonic() - stamp, 3)
                for gap in dict.fromkeys(g.get('scope_gap') for g in groups if g.get('scope_gap')):
                    text = '공통 지침 검색 자료가 연결되지 않아 해당 조사 항목을 확인하지 못했습니다.'
                    result['gaps'].append(text)
                    result['answer_ko'] += '\n\n' + text
                    if result['status'] == 'answered': result['status'] = 'partial'
                for task in understanding.get('out_of_scope_tasks', []):
                    # Reported, never searched: the registry and its documents cannot answer it.
                    part = task['intent_check']['quote'] if task['intent_check']['quote_verified'] else task['request']
                    text = f'질문 중 "{part}" 부분은 등록 제품·규제 문서 범위 밖이라 검색하거나 답하지 않았습니다.'
                    result['gaps'].append(text)
                    result['answer_ko'] += '\n\n' + text
                    if result['status'] == 'answered': result['status'] = 'partial'
            else:
                result = {'status': 'not_found', 'answer_ko': '질문과 선택 범위에서 조사할 문서 범위를 확보하지 못했습니다.',
                    'claims': [], 'items': [], 'gaps': [t['request'] for t in understanding['tasks']],
                    'validation': {'rejected_items': [], 'semantic_review': 'not_run'}}
            result.update(unit={'product': None, 'jurisdiction': None, 'studies': []}, evidence=evidence,
                          context_budget=budget, omitted_chunks=budget.get('omitted', []))
            calls = deepcopy(getattr(self.client, 'calls', [])[call_start:])
            report = {'pipeline_version': VERSION, 'mode': 'ra', 'citation_mode': self.citation_mode, 'question': question,
                'user_question': user_question, 'conversation': understanding.get('conversation'),
                'created_at': datetime.now(timezone.utc).isoformat(), 'understanding': understanding,
                'plan': [{k: v for k, v in g.items() if k != 'hits'} for g in groups],
                'retrieval': [{**{k: v for k, v in g.items() if k != 'hits'}, 'hits': [
                    {**{k: h['chunk'].get(k) for k in ('chunk_id', 'document_id', 'version_id', 'pdf_page', 'title')},
                     'score': h['score']} for h in g['hits']]} for g in groups],
                'scope': {'selected_documents': [d['key'] for d in selected_docs],
                          'excluded_documents': [], 'excluded_products': [], 'resolution': understanding['request_scope']},
                'context_origin': 'evaluation_selected_source_ids' if source_ids is not None else
                    'approval_timeline_fields' if timeline is not None else 'retrieved',
                'index': {'directory': str(self.bundle.candidate), 'chunks_sha256': self.index_meta['chunks_sha256'],
                    'model': self.index_meta['model'], 'chunks': self.index_meta['n_chunks'], 'active_index_updated': False},
                'guideline_index': self.guidelines.manifest if self.guidelines else None,
                'results': [result], 'status': 'scope_conflict' if understanding['request_scope']['conflicts'] else
                    'scope_unavailable' if not groups and timeline is None else 'completed',
                'timings': timings, 'research': research, 'seconds': round(time.monotonic() - started, 3),
                'generation_provider': getattr(self.client, 'provider', 'ollama'),
                'local_only': getattr(self.client, 'local_only', True),
                'generation_telemetry': calls, 'understanding_telemetry': understanding_calls,
                'usage': {'input_tokens': sum(c.get('prompt_tokens', 0) for c in calls),
                          'output_tokens': sum(c.get('completion_tokens', 0) for c in calls)},
                'numeric_accuracy_certified': False, 'semantic_accuracy_certified': False}
            from rag.ra_memo import build as build_memo
            report['review_memo'] = build_memo(report)
            return report
        except Exception as exc:
            self.last_failure = {'pipeline_version': VERSION, 'question': question,
                'error': f'{type(exc).__name__}: {exc}', 'evidence': evidence, 'retrieval': [
                    {**{k: v for k, v in g.items() if k != 'hits'}, 'chunk_ids': [h['chunk']['chunk_id'] for h in g['hits']]} for g in groups],
                'context_budget': budget, 'generation_telemetry': deepcopy(getattr(self.client, 'calls', [])[call_start:])}
            raise
        finally:
            try:
                if corpus is not None:
                    corpus.allowed_documents = None
                if hasattr(self.client, 'release'):
                    self.client.release()
                if hasattr(self.client, 'calls'):
                    del self.client.calls[call_start:]
            finally:
                self._request_lock.release()

    def close(self):
        if self._ra_corpus is not None:
            cached,self._ra_corpus = self._ra_corpus,None
            if getattr(cached,'_ra_bm25',None) is not None: cached._ra_bm25.db.close()
            cached.bm.db.close()
            cached._emb = None
        if self.guidelines:
            self.guidelines.close()
        super().close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--guidelines', type=Path)
    parser.add_argument('--query', required=True)
    parser.add_argument('--product', action='append')
    parser.add_argument('--jurisdiction', action='append')
    parser.add_argument('--document', action='append')
    parser.add_argument('--retrieval-pages', type=int, default=5, choices=range(1, 11))
    parser.add_argument('--include-nondefault', action='store_true')
    parser.add_argument('--citation-mode', choices=('quote', 'spans'), default='spans')
    parser.add_argument('--research-rounds', type=int, choices=(0,1), default=1)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.with_suffix('.failure.json').exists():
        parser.error('Choose a new output file')
    pipeline = RAPipeline(args.run, guideline_snapshot=args.guidelines, retrieval_pages=args.retrieval_pages,
                          citation_mode=args.citation_mode, research_rounds=args.research_rounds)
    try:
        result = pipeline.ask(args.query, args.product, args.jurisdiction, args.document,
                              include_nondefault=args.include_nondefault,
                              progress=lambda **event: print(event['message'], flush=True))
    except Exception:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix('.failure.json').write_text(json.dumps(pipeline.last_failure, ensure_ascii=False, indent=2), encoding='utf-8')
        raise
    finally:
        pipeline.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(result['results'][0]['answer_ko'])


if __name__ == '__main__':
    main()
