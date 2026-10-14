#!/usr/bin/env python3
"""Integration acceptance checks for downloaded snapshots and derived storage."""
import csv
import hashlib
import json
import sqlite3
from pathlib import Path
import fitz

root = Path(__file__).resolve().parents[1]
cache = json.loads((root / 'data/catalog/download_cache.json').read_text(encoding='utf-8'))
for entry in cache.values():
    data = (root / entry['raw_path']).read_bytes()
    assert len(data) == entry['bytes'], entry['url']
    assert hashlib.sha256(data).hexdigest() == entry['sha256'], entry['url']
documents = [json.loads(line) for line in (root / 'data/catalog/documents.jsonl').read_text(encoding='utf-8').split('\n') if line.strip()]
empty_pages = []
# Collection rules (docs/collection/INGESTION_PIPELINE.md): expected counts come from targets.json, never from a fixed number.
jl = lambda name: [json.loads(line) for line in (root / 'data/catalog' / name).read_text(encoding='utf-8').split('\n') if line.strip()] if (root / 'data/catalog' / name).exists() else []
targets = json.loads((root / 'configs/collection/targets.json').read_text(encoding='utf-8'))
kinds = json.loads((root / 'configs/collection/document_kinds.json').read_text(encoding='utf-8'))
target_ids = [t['product_id'] for t in targets['products']]
products = {p['product_id']: p for p in jl('products.jsonl')}
assert sorted(products) == sorted(target_ids), ('products.jsonl does not match targets.json', sorted(products))
assert len({d['document_id'] for d in documents}) == len(documents), 'duplicate document_id in the catalog'
assessments = [d['product_id'] for d in documents if d['role'] == 'reference_assessment']
assert sorted(assessments) == sorted(t['product_id'] for t in targets['products'] if t.get('ema_name')), ('one initial EMA assessment report per EU product', assessments)
REQUIRED = ['document_id', 'version_id', 'product_id', 'ingredient', 'product_kind', 'source', 'role', 'document_type', 'family', 'author', 'tier', 'default_search', 'title', 'url', 'sha256', 'raw_path', 'parsed_path', 'retrieved_at']
for d in documents:
    missing = [k for k in REQUIRED if k not in d]
    assert not missing, (d['document_id'], 'missing fields', missing)
    assert d['family'] != 'unclassified' and d['tier'] in kinds['tiers'], (d['document_id'], 'not in document_kinds.json', d['source'], d['document_type'])
    if d['role'] == 'guideline':
        assert d['product_id'] is None and d['ingredient'] is None, d['document_id']
    else:
        assert d['product_id'] in products and d['ingredient'] == products[d['product_id']]['ingredient'], (d['document_id'], 'product/ingredient')
# One product per development code; every licence (a second EU licence such as Usrenty) belongs to exactly one product.
codes = [''.join(ch for ch in (t.get('development_code') or t['product_id']).lower() if ch.isalnum()) for t in targets['products']]
assert len(set(codes)) == len(codes), ('two products share a development code', codes)
licence_names = [(l['jurisdiction'], l['name']) for t in targets['products'] for l in t.get('licences', [])]
assert len(set(licence_names)) == len({(j, n, t['product_id']) for t in targets['products'] for j, n in [(l['jurisdiction'], l['name']) for l in t.get('licences', [])]}), 'one licence name listed under two products'
for t in targets['products']:
    primary_eu = [l['name'] for l in t.get('licences', []) if l['jurisdiction'] == 'EU' and l.get('primary')]
    assert not t.get('licences') or primary_eu == ([t['ema_name']] if t.get('ema_name') else []), (t['product_id'], 'primary EU licence must be the collected EU name')
# Ingredient -> reference product (originator, kind "reference") -> biosimilars, each pointing at a reference product of its ingredient.
kind_of = {t['product_id']: t.get('kind', 'biosimilar') for t in targets['products']}
for t in targets['products']:
    p = products[t['product_id']]
    assert p.get('ingredient') and p.get('kind') == kind_of[t['product_id']], (t['product_id'], 'ingredient and kind')
    if kind_of[t['product_id']] == 'reference':
        assert not t.get('reference_product_id'), (t['product_id'], 'a reference product points at no other product')
    else:
        ref = t.get('reference_product_id')
        assert ref in kind_of and kind_of[ref] == 'reference' and products[ref]['ingredient'] == p['ingredient'], (t['product_id'], 'reference_product_id must name a reference product of the same ingredient')
        assert p.get('reference_product', {}).get('product_id') == ref, (t['product_id'], 'reference product record')
for d in documents:
    assert d['product_kind'] == (kind_of[d['product_id']] if d['product_id'] else None), (d['document_id'], 'product_kind')
    if t.get('us_brand'):
        assert p.get('us_licensure') and p.get('us_proper_name'), (t['product_id'], 'Purple Book licensure')
studies = {s['nct_id']: s for s in jl('studies.jsonl')}
missing_studies = [n for t in targets['products'] for n in t['study_ids'] if n not in studies]
assert not missing_studies, ('studies in targets.json not collected', missing_studies)
study_results = jl('study_results.jsonl')
assert all(r['nct_id'] in studies for r in study_results)
assert {s['nct_id'] for s in studies.values() if s.get('has_results')} == {r['nct_id'] for r in study_results}, 'posted results without rows, or rows without posted results'
guideline_records = {g['guideline_id']: g for g in jl('guidelines.jsonl')}
seeds = json.loads((root / 'configs/collection/guidelines.json').read_text(encoding='utf-8'))['guidelines']
assert sorted(guideline_records) == sorted(g['id'] for g in seeds), 'guidelines.json and guidelines.jsonl differ'
guideline_warnings = [g for g, r in guideline_records.items() if not r.get('status')]
readable = {r['sha256'] for r in csv.DictReader((root / 'pdfs_by_product/INDEX.csv').open(encoding='utf-8-sig'))}
not_indexed = [d['document_id'] for d in documents if d['sha256'] not in readable]
assert not not_indexed, ('run python -m collection.build_indexes: documents without a readable copy', not_indexed[:5])
for d in documents:
    if d['kind'] == 'pdf':  # web pages are split into sections; there is no page count to compare against
        with fitz.open(root / d['raw_path']) as pdf:
            assert len(pdf) == d['page_count']
    rows = [json.loads(line) for line in (root / d['parsed_path']).read_text(encoding='utf-8').split('\n') if line.strip()]
    assert len(rows) == d['page_count']
    assert [r['pdf_page'] for r in rows] == list(range(1, d['page_count']+1))
    assert all(r['version_id'] == d['sha256'] and r['source_url'] == d['url'] for r in rows)
    # Empty native text is a routing/coverage signal, not evidence of a blank page.
    empty_pages.extend({'document_id':d['document_id'],'pdf_page':r['pdf_page'],'status':'needs_ocr_or_visual_review'} for r in rows if not r['text'].strip())
    if d['role'] == 'reference_assessment':
        assert all(r['text'].strip() for r in rows), d['document_id']
db = sqlite3.connect(root / 'data/collection.sqlite')
assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
assert db.execute('SELECT count(*) FROM products').fetchone()[0] == len(target_ids)
counts = {t: db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ['products','studies','study_results','document_versions','pages','page_search']}
assert counts['pages'] == counts['page_search']
assert counts['document_versions'] == len(documents), 'database holds versions the catalog does not list'
assert counts['study_results'] == len(study_results)
assert counts['pages'] == sum(d['page_count'] for d in documents)
assert db.execute('SELECT count(*) FROM pages p LEFT JOIN document_versions d ON p.document_id=d.document_id AND p.version_id=d.version_id WHERE d.document_id IS NULL').fetchone()[0] == 0
assert db.execute('SELECT count(*) FROM page_search WHERE page_search MATCH ?', ('AUC* AND Cmax',)).fetchone()[0] > 0
ocr = None
if db.execute("SELECT 1 FROM sqlite_master WHERE name='ocr_regions'").fetchone():
    # OCR text is machine-read and unreviewed: it must stay tied to an exact document version and out of page_search.
    ocr_count = db.execute('SELECT count(*) FROM ocr_regions').fetchone()[0]
    assert ocr_count == db.execute('SELECT count(*) FROM ocr_search').fetchone()[0]
    assert db.execute('SELECT count(*) FROM ocr_regions o LEFT JOIN document_versions d ON o.document_id=d.document_id AND o.version_id=d.version_id WHERE d.document_id IS NULL').fetchone()[0] == 0
    read = {(r[0], r[1]) for r in db.execute("SELECT document_id,pdf_page FROM ocr_regions WHERE region=0 AND processing_status!='ocr_no_text_found'")}
    for page in empty_pages:
        if (page['document_id'], page['pdf_page']) in read:
            page['status'] = 'ocr_text_unreviewed'
    ocr = {'regions': ocr_count, 'by_status': dict(db.execute('SELECT processing_status,count(*) FROM ocr_regions GROUP BY 1').fetchall()), 'empty_pages_with_ocr_text': len(read)}
print(json.dumps({'snapshot_hashes_checked':len(cache), 'pdf_documents_checked':len(documents), 'database_counts':counts, 'ocr':ocr, 'products':target_ids, 'documents_by_product':{p: sum(d['product_id'] == p for d in documents) for p in target_ids + [None]}, 'study_result_rows':len(study_results), 'guidelines_without_status':guideline_warnings, 'same_file_documents':[sorted(d['document_id'] for d in documents if d['sha256'] == h) for h in sorted({d['sha256'] for d in documents}) if sum(x['sha256'] == h for x in documents) > 1], 'status':'passed_with_coverage_warnings', 'native_text_empty_pages':empty_pages, 'native_text_empty_page_count':len(empty_pages), 'not_checked':['all table cells','clinical completeness','clinical correctness of automatic extraction','OCR text accuracy and table structure','embeddings or LLM answers']},ensure_ascii=False,indent=2))
