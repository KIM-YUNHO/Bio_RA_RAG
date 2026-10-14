"""Freeze and audit the approved release without OCR, generation, or network calls."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def identity(row):
    return row['document_id'], row['version_id']

def normalized(text):
    text = re.sub(r'<[^>]+>', ' ', text or '')
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', html.unescape(text))).strip().casefold()

def network_forbidden(*args, **kwargs):
    raise RuntimeError('This audit forbids all network connections')

def run(release, out, query_cache=None, receipt_path=None):
    started = time.perf_counter()
    release, out = release.resolve(), out.resolve()
    if not out.is_relative_to(ROOT) or out.exists():
        raise ValueError('Choose a new audit output within the workspace')
    out.mkdir(parents=True)
    socket.socket.connect = network_forbidden
    socket.create_connection = network_forbidden
    from rag.pipeline import RAGPipeline
    from rag.release import load_bundle
    from rag import answer, source_selection
    from rag.search import filter_idx
    from rag.service_scope import SOURCES, JURISDICTIONS, allowed
    from rag.request_scope import bind_request_scope
    from rag.preprocessed_prompt import configure_budget
    from rag.preprocessed_evidence import EXTRA_RULES
    from rag.answer_conditions import QUOTE_RULES
    from rag.openai_chat import ApiTokenizer
    from types import SimpleNamespace
    import numpy as np

    base = ROOT / 'data/runs/corpus_update_2026-10-09'
    manifest = read(release/'release_manifest.json')
    receipt = read(receipt_path or ROOT/'data/runs/preprocessing_completion_2026-10-10/preprocessing_completion_v2.json')
    approved = read(base/'corpus_plan.json')
    if digest(release/'release_manifest.json') != receipt['release_manifest_sha256']:
        raise ValueError('Completion receipt does not identify this exact release')
    print('Validating frozen release, original source hashes, and service scope', flush=True)
    bundle = load_bundle(release)
    client = SimpleNamespace(tokenizer=ApiTokenizer(), input_limit=4936, output_tokens=3000,
        config=SimpleNamespace(num_ctx=8192), model='gpt-4o-mini', provider='openai',
        local_only=True, source_selection=True)
    pipeline = RAGPipeline(bundle=bundle, chat_client=client)
    summary = pipeline.summary()
    corpus = answer.Corpus('bge-m3', bundle.candidate, local_only=True, bundle=bundle,
        include_nondefault=True)
    chunks = corpus.chunks
    indexed = {identity(c) for c in chunks}
    expected = {identity(d) for d in approved['documents']}
    held = {identity(d) for d in approved['protected_exclusions']}
    default_ids = set(map(int, filter_idx(chunks)))
    full_ids = set(map(int, filter_idx(chunks, include_nondefault=True)))
    invariants = {
        'approved_documents_exact': indexed == expected,
        'protected_documents_absent': not indexed & held,
        'source_scope_exact': {c['source'] for c in chunks} == set(SOURCES),
        'jurisdiction_scope_exact': {c['jurisdiction'] for c in chunks} == set(JURISDICTIONS),
        'all_chunks_service_allowed': all(allowed(c) for c in chunks),
        'products_exact': set(summary['indexed_products']) == set(manifest['expected_products']),
        'all_approved_chunks_eligible_with_nondefault': len(full_ids) == len(chunks),
        'vectors_finite_aligned': corpus.vec.shape == (len(chunks),1024) and bool(np.isfinite(corpus.vec).all()),
        'vectors_normalized': bool(np.allclose(np.linalg.norm(corpus.vec,axis=1),1,atol=.002)),
        'no_truncated_embedding_inputs': corpus.index_meta.get('truncated_inputs') == 0,
    }
    if not all(invariants.values()):
        write(out/'failed_scope.json',invariants)
        raise ValueError('Scope or embedding audit failed')
    policy_documents = []
    for doc in summary['documents']:
        own = [i for i,c in enumerate(chunks) if identity(c)==identity(doc)]
        meta = chunks[own[0]].get('document_metadata',{})
        planned = next(d for d in approved['documents'] if identity(d)==identity(doc))
        policy_documents.append({**doc, 'family':planned['family'],
            'default_search':meta.get('default_search'), 'evaluation_cohort':meta.get('evaluation_cohort'),
            'default_chunks':sum(i in default_ids for i in own),
            'nondefault_reason':'catalog_default_search_false' if meta.get('default_search') is False else None})
    product_rows = []
    for product in summary['products']:
        own = [d for d in policy_documents if d['product_id']==product]
        product_rows.append({'product':product,'documents':len(own),'chunks':sum(d['chunks'] for d in own),
            'default_documents':sum(bool(d['default_chunks']) for d in own),
            'default_chunks':sum(d['default_chunks'] for d in own),
            'agencies':{agency:{'documents':sum(d['jurisdiction']==agency for d in own),
                'chunks':sum(d['chunks'] for d in own if d['jurisdiction']==agency),
                'default_chunks':sum(d['default_chunks'] for d in own if d['jurisdiction']==agency)}
                for agency in sorted(JURISDICTIONS)}})
    scope_tests=[]
    for question,kwargs in [('PMDA 일본 RA 평가',{}),('일본 규제기관 보고서',{}),
        ('범위 확인',{'jurisdictions':['JP']}),('범위 확인',{'products':['unknown']}),
        ('범위 확인',{'documents':['unknown']})]:
        try:
            pipeline.validate_request(question,**kwargs)
            passed=False
        except ValueError:
            passed=True
        scope_tests.append({'question':question,'selection':kwargs,'rejected':passed})
    if not all(row['rejected'] for row in scope_tests):
        raise ValueError('Out-of-scope request guard failed')
    write(out/'scope_audit.json',{'invariants':invariants,'products':product_rows,
        'documents':policy_documents,'request_guards':scope_tests,'pipeline_summary':summary,
        'indexed_documents':len(indexed),'protected_excluded_documents':len(held),
        'default_eligible_chunks':len(default_ids),'all_approved_eligible_chunks':len(full_ids),
        'service_started':False,'active_index_promoted':False,'api_calls':0})
    cases=read(base/'ra3_openai_evaluation/cases.json')
    caches=[]
    for case in cases:
        paths=[base/name/'responses'/(case['id']+'.json') for name in
            ['updated_openai_comparator_v4','updated_openai_regression_v3','ra3_openai_evaluation']]
        cached=next(path for path in paths if path.exists())
        old=read(cached)
        if old['question']!=case['question']:
            raise ValueError('Cached interpretation belongs to a different question')
        payload=deepcopy(case['payload'])
        if payload.get('documents'):
            payload['documents']=[bundle.doc_by_identity[identity(case)]]
        caches.append({'case':case,'payload':payload,'understanding':old['understanding'],
            'cache_path':str(cached.relative_to(ROOT)),'cache_sha256':digest(cached)})
    code_paths=sorted({*ROOT.joinpath('rag').glob('*.py'),*ROOT.joinpath('review_viewer').glob('*.py'),
        ROOT/'eval/audit_latest_retrieval.py'})
    code_hashes={str(p.relative_to(ROOT)):digest(p) for p in code_paths}
    frozen={'created_at':datetime.now(timezone.utc).isoformat(),'release':str(release),
        'manifest_sha256':digest(release/'release_manifest.json'),
        'candidate_hashes':manifest['candidate_hashes'],'embedding_identity':receipt['embedding_identity'],
        'code_sha256':code_hashes,'cases':caches,'api_calls':0,'generation_calls':0,
        'evaluation_type':'exposed development diagnosis, cached OpenAI interpretation replay',
        'not_end_to_end_accuracy':True,'not_unseen_generalization':True,
        'source_scope':['EMA','FDA','Health Canada'],'protected_exclusions':approved['protected_exclusions'],
        'criteria':{'strict_page_hit':'same source document, version, and at least one annotated page in top 5',
            'passage_presence':'normalized contiguous literal quote at each stage; diagnostic only, not semantic grading',
            'candidate_presence':'literal source candidate coverage, not selected answer accuracy',
            'prompt_budget':'4936 estimated input tokens including JSON schema; 3000 output and 256 framing reserved',
            'boundaries':'No held source, other product or other regulator in selected evidence',
            'counterfactuals':'default/all-approved label policy and D02 intent override are separately named diagnostics'},
        'gold_policy':'Gold and rubrics are read only after retrieval/context selection for diagnostics; never prompt inputs'}
    write(out/'frozen.json',frozen)
    if query_cache:
        prior=read(query_cache/'frozen.json')
        # Query vectors depend on exact translated requests and the encoder,
        # not on which document release is searched.
        if prior['cases']!=caches:
            raise ValueError('Query cache inputs differ from frozen audit inputs')
        query_identity=read(query_cache/'query_embedding_identity.json')
        if any(query_identity[k]!=receipt['embedding_identity'][k] for k in query_identity):
            raise ValueError('Cached query encoder differs from document encoder')
        queries=np.load(query_cache/'query_vectors.npy')
        if queries.shape!=(len(caches),1024) or not np.isfinite(queries).all():
            raise ValueError('Cached query vectors invalid')
        write(out/'query_cache_receipt.json',{'directory':str(query_cache),
            'vectors_sha256':digest(query_cache/'query_vectors.npy'),'exact_frozen_inputs_match':True})
        print('Scope passed; reusing exact-input local query vectors, no model reload',flush=True)
    else:
        print('Scope passed; frozen 24 development questions. Loading one local BGE-M3 query encoder',flush=True)
        encoder=answer.Embedder('bge-m3',device='cuda',local_only=True)
        import torch
        model_hash=hashlib.sha256()
        for name,tensor in sorted(encoder.model.state_dict().items()):
            model_hash.update(name.encode());model_hash.update(str(tuple(tensor.shape)).encode());model_hash.update(str(tensor.dtype).encode())
            model_hash.update(tensor.detach().cpu().contiguous().view(torch.uint8).numpy().tobytes())
        query_identity={'loaded_state_sha256':model_hash.hexdigest(),
            'tokenizer_sha256':hashlib.sha256(encoder.tok.backend_tokenizer.to_str().encode()).hexdigest(),
            'half_precision':encoder.half,'pooling':encoder.model[1].get_config_dict()}
        if any(query_identity[k]!=receipt['embedding_identity'][k] for k in query_identity):
            raise ValueError('Query encoder differs from the frozen document encoder')
        queries=encoder.encode_queries([row['understanding']['english_query'] for row in caches])
        if not np.isfinite(queries).all() or encoder.truncated:
            raise ValueError('Query embedding invalid or truncated')
    np.save(out/'query_vectors.npy',queries)
    write(out/'query_embedding_identity.json',query_identity)
    rows=[]
    for number,row in enumerate(caches):
        case=row['case']; payload=row['payload']; qv=queries[number]
        variants=[('requested_scope',payload,None)]
        if case['id'].startswith('M_'):
            for flag in [False,True]:
                variant={**payload,'documents':None,'include_nondefault':flag}
                variants.append(('all_approved_unpinned' if flag else 'default_unpinned',variant,None))
        if case['id']=='D02':
            variants.append(('intent_fact_lookup_diagnostic',payload,'fact_lookup'))
        for mode,request,intent_override in variants:
            corpus.include_nondefault=request.get('include_nondefault',False)
            corpus.by_page={}
            eligible_ids=full_ids if corpus.include_nondefault else default_ids
            for i in eligible_ids:
                c=chunks[i]
                corpus.by_page.setdefault((*identity(c),c['pdf_page']),[]).append(i)
            selected=[d for d in pipeline.documents if
                (not request.get('documents') or d['key'] in request['documents']) and
                (not request.get('products') or d['product_id'] in request['products']) and
                (not request.get('jurisdictions') or d['jurisdiction'] in request['jurisdictions'])]
            corpus.allowed_documents={identity(d) for d in selected}
            available=[chunks[i] for i in eligible_ids if identity(chunks[i]) in corpus.allowed_documents]
            products=sorted({c['product_id'] for c in available})
            agencies=sorted({c['jurisdiction'] for c in available})
            u=bind_request_scope(case['question'],row['understanding'],products,agencies,
                selected_products=request.get('products'),selected_agencies=request.get('jurisdictions'),
                document_scope=bool(request.get('documents')),documents=pipeline.documents)
            if intent_override:u['intent']=intent_override
            results=[]
            for unit,pages in answer.retrieve(corpus,u,qv,k=5,policy='general',agencies=agencies,products=products):
                adapter=corpus.evidence_adapter
                make=configure_budget(adapter,client,case['question'],u,unit,pages,EXTRA_RULES,QUOTE_RULES,builder=source_selection.prompt)
                evidence=adapter.from_pages(pages,corpus,qv,unit)
                candidates=source_selection.catalog(evidence,case['question'])
                messages=make(evidence)
                schema=source_selection.schema(candidates)
                cost=client.tokenizer.messages(messages)+client.tokenizer.one(json.dumps(schema,ensure_ascii=False,separators=(',',':')))
                if cost>client.input_limit:raise ValueError('Prepared context exceeds frozen model input budget')
                if any(identity(b) not in corpus.allowed_documents or b['product_id']!=unit['product'] or b['jurisdiction']!=unit['jurisdiction'] for b in evidence.values()):
                    raise ValueError('Evidence crosses a selected boundary')
                results.append({'unit':unit,'pages':pages,'evidence':evidence,'candidates':candidates,
                    'messages':messages,'schema':schema,'estimated_input_tokens':cost,
                    'context_budget':adapter.last_budget,'omitted_chunks':adapter.last_omitted})
            def target(item):
                return identity(item)==identity(case)
            strict_hit=any(target(p) and p['pdf_page'] in case['gold_pages'] for r in results for p in r['pages'])
            supplied_hit=any(target(b) and b['pdf_page'] in case['gold_pages'] for r in results for b in r['evidence'].values())
            all_target=[c for c in chunks if target(c) and c['pdf_page'] in case['gold_pages']]
            supplied=[b for r in results for b in r['evidence'].values() if target(b)]
            checks=[]
            for gold in case.get('gold',[]):
                quote=normalized(gold.get('evidence_quote',''))
                page=int(gold['pdf_page'])
                native=[p.get('native_page_text','') for p in bundle.pages.values() if target(p) and p['pdf_page']==page]
                units=[v['text'] for uid,v in bundle.units.items() if target(v) and bundle.assets.get(uid,{}).get('pdf_page')==page]
                indexed_text=[c['text'] for c in chunks if target(c) and c['pdf_page']==page]
                supplied_text=[b['text'] for b in supplied if b['pdf_page']==page]
                checks.append({'gold_id':gold['gold_id'],'page':page,'quote':gold.get('evidence_quote'),
                    'native_page':bool(quote) and any(quote in normalized(t) for t in native),
                    'source_representation':bool(quote) and any(quote in normalized(t) for t in units),
                    'indexed_chunk':bool(quote) and any(quote in normalized(t) for t in indexed_text),
                    'supplied_context':bool(quote) and any(quote in normalized(t) for t in supplied_text)})
            record={'case_id':case['id'],'mode':mode,'question':case['question'],'request':request,
                'understanding':u,'cached_understanding_path':row['cache_path'],
                'strict_page_hit':strict_hit,'gold_page_supplied':supplied_hit,
                'target_page_chunks':[{k:c.get(k) for k in ('chunk_id','pdf_page','text','structure','representation')} for c in all_target],
                'results':results,'gold_passage_checks':checks,'api_calls':0,'generation_calls':0,
                'generation_accuracy':'not_evaluated'}
            write(out/'traces'/(case['id']+'__'+mode+'.json'),record)
            brief={'case':case['id'],'mode':mode,'expected':case['expected'],
                'cached_intent':row['understanding']['intent'],'effective_intent':u['intent'],
                'eligible_chunks':len(available),'strict_page_hit':strict_hit,'gold_page_supplied':supplied_hit,
                'target_page_chunks':len(all_target),'candidate_counts':[len(r['candidates']) for r in results],
                'input_tokens':[r['estimated_input_tokens'] for r in results],
                'quote_in_index':sum(c['indexed_chunk'] for c in checks),
                'quote_supplied':sum(c['supplied_context'] for c in checks),'quote_total':len(checks)}
            rows.append(brief);print(json.dumps(brief,ensure_ascii=False),flush=True)
            write(out/'progress.json',{'stage':'retrieval_context_audit','rows_completed':len(rows),'api_calls':0})
    if any(digest(ROOT/n)!=h for n,h in code_hashes.items()):raise ValueError('Code changed during frozen audit')
    primary=[r for r in rows if r['mode']=='requested_scope' and r['expected']=='answerable']
    result={'completed_at':datetime.now(timezone.utc).isoformat(),'execution_complete':True,
        'release':str(release),'rows':rows,'primary_answerable_cases':len(primary),
        'strict_page_hit':sum(r['strict_page_hit'] for r in primary),
        'gold_page_supplied':sum(r['gold_page_supplied'] for r in primary),
        'api_calls':0,'generation_calls':0,'ocr_calls':0,'qwen_calls':0,
        'network_connections_allowed':False,'service_started':False,'active_index_promoted':False,
        'end_to_end_accuracy_evaluated':False,'unseen_generalization_evaluated':False,
        'seconds':round(time.perf_counter()-started,3)}
    write(out/'summary.json',result);write(out/'progress.json',{'stage':'complete','execution_complete':True,'api_calls':0})
    corpus.bm.db.close();pipeline.close()
    print('Completed offline scope and context audit',flush=True)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--query-cache',type=Path)
    parser.add_argument('--receipt',type=Path)
    args=parser.parse_args()
    run(args.release,args.out,args.query_cache,args.receipt)
