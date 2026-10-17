"""Three-pane service: inventory counts, evidence page view boundaries and static files."""
import json
from pathlib import Path
import tempfile
from threading import Thread
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request

import fitz

from rag_service.__main__ import Jobs
from rag_service.http_rules import STATIC_FILES
import sys
sys.path.insert(0, str(Path(__file__).parent))
from http_server import Running  # noqa: E402
from rag_service.inventory import build
from rag_service.pages import EvidencePages, quote_segments

ROOT = Path(__file__).resolve().parents[1]
JOB_ID = 'a'*32


def documents():
    return [{'key':'d1','document_id':'EMA:1','version_id':'v1','product_id':'SB17','jurisdiction':'EU','source':'EMA',
             'title':'SB17 EPAR','pages':3,'chunks':10,'eligible_chunks':8,'eligible_with_nondefault':9},
            {'key':'d2','document_id':'FDA:1','version_id':'v2','product_id':'SB17','jurisdiction':'US','source':'FDA',
             'title':'SB17 review','pages':2,'chunks':5,'eligible_chunks':5,'eligible_with_nondefault':5},
            {'key':'g1','document_id':'GL:1','version_id':'v3','product_id':None,'jurisdiction':'EU','source':'EMA',
             'title':'Guideline','pages':4,'chunks':4,'eligible_chunks':4,'eligible_with_nondefault':4,'role':'guideline'}]


class InventoryTests(unittest.TestCase):
    def test_structure_reasons_and_catalog_counts(self):
        pipeline = SimpleNamespace(documents=documents(), products=['SB17'], agencies=['CA','EU','US'])
        data = build(pipeline)
        ingredient = data['ingredients'][0]
        self.assertEqual(ingredient['name'], 'ustekinumab')
        self.assertEqual([p['product_id'] for p in ingredient['reference']], ['CNTO1275'])
        self.assertFalse(ingredient['reference'][0]['searchable'])
        products = {p['product_id']: p for p in ingredient['products']}
        self.assertTrue(products['SB17']['searchable'])
        self.assertFalse(products['ABP654']['searchable'])
        self.assertEqual(products['SB17']['cells']['EU']['indexed'], 1)
        self.assertEqual(products['SB17']['cells']['CA']['reason'], '수집됨 · 검색 색인 없음')
        self.assertFalse(products['BAT2206']['cells']['CA']['licensed'])
        self.assertIn('허가 정보 없음', products['BAT2206']['cells']['CA']['reason'])
        catalog = [json.loads(l) for l in (ROOT/'data/catalog/documents.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
        expected = len({d['document_id'] for d in catalog if d.get('product_id')=='SB17' and d.get('source')=='EMA'})
        self.assertEqual(products['SB17']['cells']['EU']['collected'], expected)
        self.assertEqual(data['guidelines']['by_jurisdiction'], {'EU':1,'US':0,'CA':0})
        self.assertEqual([d['key'] for d in products['SB17']['documents']], ['d1','d2'])


def make_pdf(path):
    pdf = fitz.open()
    for n in range(3):
        page = pdf.new_page(width=600, height=800)
        page.insert_text((72, 100), f'Page {n+1} heading text', fontsize=12)
        page.insert_text((72, 300), 'The reference medicinal product must be authorised in the EEA.', fontsize=11)
    pdf.save(path)


def job(state='completed'):
    evidence = {
        'E1': {'document_id':'EMA:1','version_id':'v1','pdf_page':2,'title':'SB17 EPAR','source':'EMA','product_id':'SB17',
               'source_refs':[{'pdf_page':2,'bbox_pdf':[60,280,540,320]}],'structure':{'kind':'table_candidate'},'text':'<table></table>'},
        'E2': {'document_id':'EMA:1','version_id':'v1','pdf_page':1,'title':'SB17 EPAR','source_refs':[{'pdf_page':1}],
               'structure':{'kind':'text'},'text':'x'},
        'E3': {'document_id':'EMA:9','version_id':'zz','pdf_page':1,'title':'Unregistered','source_refs':[]}}
    claims = [{'sources':[{'evidence':'E2','quote':'The reference medicinal product must be authorised in the EEA.'}]}]
    return {'id':JOB_ID,'state':state,'question':'q','report':{'results':[{'evidence':evidence,'claims':claims}]}}


class PagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pdf = str(Path(self.tmp.name)/'doc.pdf'); make_pdf(self.pdf)
        bundle = SimpleNamespace(docs={'d1':{'document_id':'EMA:1','version_id':'v1','raw_path':self.pdf}})
        self.pages = EvidencePages(SimpleNamespace(bundle=bundle, guidelines=None))

    def tearDown(self): self.tmp.cleanup()

    def test_region_quote_and_boundaries(self):
        detail = self.pages.detail(job(), 0, 'E1')
        self.assertEqual((detail['page'], detail['page_count'], detail['highlight_method']), (2, 3, 'region'))
        self.assertEqual(detail['boxes'], [[0.1, 0.35, 0.9, 0.4]])
        self.assertTrue(detail['has_crop'])
        quoted = self.pages.detail(job(), 0, 'E2')
        self.assertEqual(quoted['highlight_method'], 'quote'); self.assertTrue(quoted['boxes'])
        self.assertEqual(self.pages.detail(job(), 0, 'E2', page=3)['highlight_method'], 'none')
        self.assertTrue(self.pages.page_png(job(), 0, 'E1', 3).startswith(b'\x89PNG'))
        self.assertTrue(self.pages.crop_png(job(), 0, 'E1').startswith(b'\x89PNG'))
        with self.assertRaises(KeyError): self.pages.crop_png(job(), 0, 'E2')
        with self.assertRaises(ValueError): self.pages.detail(job(), 0, 'E1', page=4)
        for args in [(job(), 0, 'E3'), (job(), 1, 'E1'), (job(), 0, 'E9'), (job('failed'), 0, 'E1')]:
            with self.assertRaises(KeyError): self.pages.detail(*args)

    def test_quote_segments_skip_markup_and_short_lines(self):
        self.assertEqual(quote_segments(['short\n<td>4882</td> long enough cell text\nA long enough sentence here.']),
                         ['A long enough sentence here.'])


class Pipeline:
    def __init__(self, pdf):
        self.documents = documents(); self.products = ['SB17']; self.agencies = ['EU','US']
        self.bundle = SimpleNamespace(docs={'d1':{'document_id':'EMA:1','version_id':'v1','raw_path':pdf}}); self.guidelines = None
    def summary(self): return {'mode':'fixture'}


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); pdf = str(Path(self.tmp.name)/'doc.pdf'); make_pdf(pdf)
        archive = Path(self.tmp.name)/'archive'; archive.mkdir()
        (archive/(JOB_ID+'.json')).write_text(json.dumps(job()), encoding='utf-8')
        self.jobs = Jobs(Pipeline(pdf), archive)
        self.server = Running(self.jobs)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.jobs.close(); self.server.shutdown(); self.tmp.cleanup()

    def get(self, path):
        return self.opener.open(f'http://127.0.0.1:{self.server.server_port}'+path)

    def test_routes_types_and_errors(self):
        for path in STATIC_FILES:
            with self.get(path) as reply:
                self.assertEqual(reply.status, 200)
                self.assertIn('javascript' if path.endswith('.js') else 'css' if path.endswith('.css') else 'html', reply.headers['Content-Type'])
        with self.get('/api/inventory') as reply: self.assertEqual(json.load(reply)['ingredients'][0]['name'], 'ustekinumab')
        base = f'/api/jobs/{JOB_ID}/evidence/0/E1'
        with self.get(base) as reply: self.assertEqual(json.load(reply)['highlight_method'], 'region')
        with self.get(base+'?page=3') as reply: self.assertEqual(json.load(reply)['page'], 3)
        with self.get(base+'/page/1') as reply: self.assertEqual(reply.headers['Content-Type'], 'image/png')
        with self.get(base+'/crop') as reply: self.assertEqual(reply.headers['Content-Type'], 'image/png')
        with self.get(base+'/document.pdf') as reply: self.assertTrue(reply.read().startswith(b'%PDF'))
        for path, status in [(base+'?page=x',400),(base+'?page=9',400),(base+'/page/9',404),
                             (f'/api/jobs/{JOB_ID}/evidence/0/E3',404),(f'/api/jobs/{JOB_ID}/evidence/0/..%2F..%2F.env',404),
                             (f'/api/jobs/{"b"*32}/evidence/0/E1',404),('/viewer.js/../.env',404)]:
            with self.assertRaises(urllib.error.HTTPError) as error: self.get(path)
            self.assertEqual(error.exception.code, status, path)


if __name__ == '__main__': unittest.main()
