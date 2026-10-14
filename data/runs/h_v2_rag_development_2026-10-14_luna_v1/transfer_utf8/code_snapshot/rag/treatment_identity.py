"""Printed treatment identities; catalog names identify products, never outcomes.

Proper-name reference aliases require the source product and a region printed
in the same header. Sample-count suffixes never change the quoted source.
"""
import json
import re
from functools import lru_cache
from pathlib import Path
from rag.contract_terms import key

VERSION='printed-treatment-identity-1'


@lru_cache(maxsize=1)
def references():
    path=Path(__file__).resolve().parents[1]/'data/catalog/products.jsonl'
    return {p['product_id']:(p.get('reference_product') or {}) for p in
        (json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip())}


def _reference_names(product_id=None):
    refs=[references().get(product_id,{})] if product_id else list(references().values())
    # Without a scoped product, accept proprietary reference names only.
    fields=('name','proper_name') if product_id else ('name',)
    return list(dict.fromkeys((r[k],r['name']) for r in refs for k in fields if r.get(k) and r.get('name')))


def reference_pattern(name):
    region=r'(?:EU|US)'
    prefix=region+r'[-\s]*(?:(?:approved|licensed|sourced)[-\s]+)?'+re.escape(name)
    suffix=re.escape(name)+r'\s*\(\s*'+region+r'\s*\)'
    return r'(?:'+prefix+'|'+suffix+')'


def printed_arm(text,product_id=None):
    from rag.study_registry import products
    plain=re.sub(r'\s*\(\s*N\s*=\s*\d+\s*\)\s*(?:n\s*\(\s*%\s*\))?\s*$','',text,flags=re.I).strip()
    normalized=key(plain).replace('-','')
    matches={pid for pid,p in products().items() if normalized in
        {key(n).replace('-','') for n in [pid,*p['names'],*p.get('ko_names',[])]}}
    if len(matches)==1:return next(iter(matches))
    for name,canonical in _reference_names(product_id):
        if re.fullmatch(reference_pattern(name),plain,re.I):
            region=re.search(r'^(EU|US)\b',plain,re.I) or re.search(r'\(\s*(EU|US)\s*\)$',plain,re.I)
            return region[1].upper()+'-'+canonical
    if re.fullmatch(r'(?:arm|treatment|group)\s+[A-Za-z]|placebo',plain,re.I):return plain
    return None


def mention_pattern(product_id=None):
    from rag.study_registry import products
    names={n for pid,p in products().items() for n in [pid,*p['names']] if len(n)>=3}
    parts=[reference_pattern(name) for name,_ in _reference_names(product_id)]
    parts.extend(re.escape(n) for n in sorted(names,key=len,reverse=True))
    return r'(?<![A-Za-z0-9])(?:'+'|'.join(parts)+r')(?![A-Za-z0-9])'
