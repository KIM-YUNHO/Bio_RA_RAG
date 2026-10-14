"""Bounded retrieval signals from the original request and own table roles.

Matches rank pages only. They do not approve study attribution or an answer.
The source cache is finite, immutable, and keyed by text and source identity.
"""
from functools import lru_cache
from copy import deepcopy
import json
from rag.grounded_table_graph import build,selected_fact
from rag.contract_relations import enrich
from rag.contract_match import match_request

VERSION='own-request-role-retrieval-2'


@lru_cache(maxsize=128)
def _facts(text,product,identity):
    block={'text':text,'product_id':product,**dict(identity)};graph=build(block);facts=[]
    for table in graph['tables']:
        if table['relationship_status']!='structural_candidate':continue
        nodes={n['id']:n for n in table['cells']}
        for f in table['facts']:
            n=nodes[f['value_node']]
            if n['explicit_header']:continue
            fact=selected_fact(graph,n['source_range'])
            if fact:facts.append(enrich(fact,graph,block))
    return json.dumps(facts,ensure_ascii=False,separators=(',',':'))


def coverage_score(contract,chunk):
    if not contract or '<table' not in chunk.get('text',''):return 0
    requests=deepcopy(contract['requests'])
    for request in requests:
        request['normalization_status']='grounded'
        # Identity metadata can rank a trial page but cannot prove attribution.
        request['conditions']=[c for c in request['conditions'] if c['dimension']!='study']
    identity=tuple((k,chunk.get(k)) for k in ('document_id','version_id','chunk_id','pdf_page','locator_type'))
    matched={}
    for fact in json.loads(_facts(chunk['text'],chunk.get('product_id'),identity)):
        for request in requests:
            check=match_request(contract,request,fact)
            if check['status']!='mismatch' and check['checks'][0]['status']=='match' and any(c['dimension']=='endpoint' and c['status']=='match' for c in check['checks']):
                # Unprinted population/timing is unknown, not a reason to hide
                # an explicitly matching outcome. Reward complete AND support
                # more, and never combine conditions from separate body rows.
                unknown=sum(c['status']=='unknown' for c in check['checks'])
                matched[request['id']]=max(matched.get(request['id'],0),1/(1+unknown))
    return sum(matched.values())
