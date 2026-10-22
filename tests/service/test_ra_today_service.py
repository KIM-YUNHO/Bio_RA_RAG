import json
from pathlib import Path
import tempfile
import unittest
import urllib.request
import urllib.error
from rag_service.__main__ import Jobs
from tests.support.http_server import Running  # noqa: E402
from rag.ra.ra_sources import GuidelineIndex, permitted

ROOT=Path(__file__).resolve().parents[2]

class Pipeline:
    def summary(self): return {'mode':'fixture'}

class TodayServiceTests(unittest.TestCase):
    def test_paginated_archived_search_and_review_http_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            for n in range(25):
                identity=f'{n:032x}'
                job={'id':identity,'state':'completed','created_at':n,'question':'근거 '+str(n),
                     'report':{'question':'근거 '+str(n),'results':[{'status':'partial'}],
                        'review_memo':{'meeting_questions':[{'issue_id':'I1','question_ko':'원본'}]}}}
                (Path(directory)/(identity+'.json')).write_text(json.dumps(job),encoding='utf-8')
            jobs=Jobs(Pipeline(),directory)
            server=Running(jobs)
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def call(path,payload=None):
                request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,
                    data=json.dumps(payload).encode() if payload is not None else None,
                    headers={'Content-Type':'application/json'})
                with opener.open(request) as response:return json.load(response)
            try:
                first=call('/api/jobs?limit=20');second=call('/api/jobs?offset=20&limit=20')
                self.assertEqual(first['total'],25);self.assertEqual(len(second['jobs']),5)
                self.assertFalse({r['id'] for r in first['jobs']} & {r['id'] for r in second['jobs']})
                self.assertEqual(call('/api/jobs?q=24')['total'],1)
                identity=f'{0:032x}';original=jobs.get(identity)
                self.assertEqual(call('/api/jobs/'+identity+'/review')['revisions'],[])
                payload={'expected_revision':0,'status':'needs_work','note':'재검토',
                         'meeting_questions':[{'issue_id':'I1','question_ko':'수정 질문'}]}
                self.assertEqual(len(call('/api/jobs/'+identity+'/review',payload)['revisions']),1)
                with self.assertRaises(urllib.error.HTTPError) as error:call('/api/jobs/'+identity+'/review',payload)
                self.assertEqual(error.exception.code,409)
                self.assertEqual(jobs.get(identity),original)
            finally:
                jobs.close();server.shutdown()
            restarted=Jobs(Pipeline(),directory)
            try:self.assertEqual(restarted.reviews.get(restarted.get(identity))['revisions'][0]['note'],'재검토')
            finally:restarted.close()

    def test_guideline_withdrawal_and_stored_draft_never_enter_search(self):
        for name in ['ra_guidelines_native_2026-10-11_v1','ra_guidelines_native_2026-10-12_v2']:
            index=GuidelineIndex(ROOT/'data/guidelines'/name)
            try:
                self.assertIn('GL:FDA-SCIENTIFIC-CONSIDERATIONS-2015',index.excluded_documents)
                self.assertTrue(all(c['document_id']!='GL:FDA-SCIENTIFIC-CONSIDERATIONS-2015'
                    for _,c in index.search(['scientific considerations biosimilarity'],['US'],k=200)))
                reflection=[c for c in index.chunks if c['document_id']=='GL:EMA-TAILORED-CLINICAL-RP']
                self.assertEqual(permitted(reflection[0]['document_metadata']),name.endswith('v2'))
                if name.endswith('v2'):self.assertIn('16 March 2026',reflection[0]['text'])
            finally:index.close()

if __name__=='__main__':unittest.main()
