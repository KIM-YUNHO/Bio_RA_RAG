"""Opt-in broader hybrid retrieval; all existing evidence boundaries still apply.

Five established pages plus bounded supplementary candidates. Reranker scores
are only retrieval signals, never source attribution or answer validation.
"""
from rag.answer import retrieve as baseline_retrieve
from rag.search import filter_idx,to_pages,rrf
from rag.study_search import adaptive_hits

VERSION='bounded-retrieval-candidate-4'


def retrieve(corpus,u,qv,products=None,agencies=None,reranker=None,k=10,use_dense=True,policy=None):
    if not 5<=k<=10:raise ValueError('candidate page budget must be 5..10')
    units=baseline_retrieve(corpus,u,qv,k=5,policy='general',products=products,agencies=agencies)
    out=[]
    from rag.population_counts import requested,printed_cues
    from rag.source_event_scope import cues as event_cues
    from rag.budgeted_evidence import relevance
    for unit,established in units:
        idx=filter_idx(corpus.chunks,unit['product'],None,unit['jurisdiction'],include_nondefault=corpus.include_nondefault)
        if corpus.allowed_documents is not None:
            idx=[i for i in idx if (corpus.chunks[i]['document_id'],corpus.chunks[i]['version_id']) in corpus.allowed_documents]
        query=u['search_terms']+' '+u['english_query']
        broad=adaptive_hits(qv,query,idx,corpus.chunks,corpus.vec,corpus.bm,unit['studies'],unit['doc_groups'],True,unit['topic'])
        population=[]
        if requested(u.get('source_question','')+' '+query):
            population=sorted([(relevance(corpus.chunks[i],query)+4*len(printed_cues(corpus.chunks[i])),int(i))
                for i in idx if printed_cues(corpus.chunks[i])],reverse=True)
        rankings=[broad]
        if population:rankings.append(population)
        original=u.get('source_question','')
        from rag.printed_pk import request,retrieval_score
        requested_roles=request(original)
        from rag.literal_requests import compile_requests
        from rag.request_role_retrieval import coverage_score
        role_contract=compile_requests(original)
        role_hits=[]
        if requested_roles or role_contract:
            for i in idx:
                chunk=corpus.chunks[i]
                score=max(retrieval_score(requested_roles,chunk) if requested_roles else 0,coverage_score(role_contract,chunk))
                if score:role_hits.append((4*score+2*len(set(chunk.get('studies',[]))&set(unit['studies']))+relevance(chunk,query),int(i)))
            role_hits.sort(reverse=True)
        events=sorted([(relevance(corpus.chunks[i],query)+4*len(event_cues(original,corpus.chunks[i])),int(i))
            for i in idx if event_cues(original,corpus.chunks[i])],reverse=True)
        if events:rankings.append(events)
        pool=rrf(rankings,top_n=100)
        supplemental=rrf([pool,reranker.rank(u['english_query'],pool,corpus.chunks,limit=80)],top_n=100) if reranker else pool
        pages=list(established)
        seen={(p['document_id'],p['version_id'],p['pdf_page']) for p in pages}
        # At most two additional source-role pages; preserve all established
        # pages and leave the other supplementary slots to hybrid fusion.
        for p in to_pages(role_hits,corpus.chunks,2):
            key=(p['document_id'],p['version_id'],p['pdf_page'])
            if key not in seen and len(pages)<k:pages.append(p);seen.add(key)
        for p in to_pages(supplemental,corpus.chunks,100):
            key=(p['document_id'],p['version_id'],p['pdf_page'])
            if key not in seen:
                pages.append(p);seen.add(key)
            if len(pages)>=k:break
        out.append(({**unit,'retrieval_policy':VERSION,'reranker_used':reranker is not None},pages[:k]))
    return out
