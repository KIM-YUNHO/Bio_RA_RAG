"""Loaded-corpus inventory for the scope pane: ingredient -> products -> agencies -> documents.

Reads targets.json (product structure and licences), the collection catalog
(collected documents) and the running pipeline's document list (searchable
documents). Read only; no model or network calls.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_JURISDICTION = {'EMA': 'EU', 'FDA': 'US', 'Health Canada': 'CA'}
JURISDICTION_LABEL = {'EU': 'EMA', 'US': 'FDA', 'CA': 'Health Canada'}


def _rows(path):
    with open(path, encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _doc_view(doc):
    return {k: doc.get(k) for k in ('key', 'title', 'source', 'jurisdiction', 'pages', 'chunks',
                                    'eligible_chunks', 'eligible_with_nondefault')}


def build(pipeline, root=ROOT):
    root = Path(root)
    targets = json.loads((root/'configs/collection/targets.json').read_text(encoding='utf-8'))
    catalog = _rows(root/'data/catalog/documents.jsonl')
    collected = {}
    for doc in catalog:
        jurisdiction = SOURCE_JURISDICTION.get(doc.get('source'))
        if doc.get('product_id') and jurisdiction:
            collected.setdefault((doc['product_id'], jurisdiction), set()).add(doc['document_id'])
    documents = list(getattr(pipeline, 'documents', None) or [])
    searchable = set(getattr(pipeline, 'products', None) or [])
    jurisdictions = [j for j in ('EU', 'US', 'CA') if j in set(getattr(pipeline, 'agencies', None) or ('EU', 'US', 'CA'))]
    ingredients = {}
    for product in targets['products']:
        pid = product['product_id']
        ingredient = product.get('ingredient') or targets.get('ingredient') or '미상'
        entry = ingredients.setdefault(ingredient, {'name': ingredient, 'products': [], 'reference': []})
        licensed = {l['jurisdiction'] for l in product.get('licences') or []}
        own = [d for d in documents if d.get('product_id') == pid]
        name = product.get('ema_name') or product.get('us_brand') or pid
        names = sorted({l['name'] for l in product.get('licences') or [] if l.get('name')} - {name})
        cells = {}
        for j in jurisdictions:
            indexed = [d for d in own if d.get('jurisdiction') == j]
            count = len(collected.get((pid, j), ()))
            reason = ('' if indexed else '허가 정보 없음(미허가 또는 미확인)' if j not in licensed
                      else '수집 문서 없음' if not count else '수집됨 · 검색 색인 없음')
            cells[j] = {'licensed': j in licensed, 'collected': count, 'indexed': len(indexed),
                        'eligible_chunks': sum(d.get('eligible_chunks') or 0 for d in indexed), 'reason': reason}
        view = {'product_id': pid, 'name': name, 'other_names': names, 'company': product.get('company'),
                'kind': product.get('kind', 'biosimilar'), 'searchable': pid in searchable and bool(own),
                'collected': sum(len(collected.get((pid, j), ())) for j in ('EU', 'US', 'CA')),
                'cells': cells, 'documents': [_doc_view(d) for d in own]}
        if view['kind'] == 'reference':
            view['searchable'] = False
            view['reason'] = '수집됨 · 대조약 검색 색인 준비 전'
            entry['reference'].append(view)
        else:
            if not view['searchable']: view['reason'] = '수집됨 · 검색 색인 없음'
            entry['products'].append(view)
    guidelines = [_doc_view(d) for d in documents if d.get('role') == 'guideline']
    return {'ingredients': list(ingredients.values()),
            'jurisdictions': [{'id': j, 'label': JURISDICTION_LABEL[j]} for j in jurisdictions],
            'guidelines': {'documents': guidelines,
                           'by_jurisdiction': {j: sum(d['jurisdiction'] == j for d in guidelines) for j in jurisdictions}},
            'catalog_documents': len({d['document_id'] for d in catalog}),
            'read_only': True}
