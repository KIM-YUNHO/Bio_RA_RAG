"""Main service job lifecycle, concurrency and local-only HTTP boundaries."""
from http.server import ThreadingHTTPServer
import json
from threading import Event, Thread
import time
import unittest
import urllib.error
import urllib.request

from rag_service.__main__ import Jobs,handler


class Pipeline:
    def __init__(self):self.entered=Event();self.release=Event();self.calls=[]
    def summary(self):return {'service':'main','review_url':'http://127.0.0.1:8766'}
    def validate_request(self,question,*args):
        if not isinstance(question,str) or not question:raise ValueError('Question required')
    def ask(self,question,products,jurisdictions,documents,progress,include_nondefault=False):
        self.calls.append(question);self.entered.set();progress(stage='understanding',message='해석 중')
        self.release.wait(5)
        if question=='fail':raise RuntimeError('model unavailable')
        return {'question':question,'results':[]}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.pipeline=Pipeline();self.jobs=Jobs(self.pipeline)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.jobs))
        self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.pipeline.release.set();self.jobs.close();self.server.shutdown();self.server.server_close();self.thread.join()

    def request(self,path,payload=None,headers=None):
        data=json.dumps(payload).encode() if payload is not None else None
        return self.opener.open(urllib.request.Request(self.url+path,data=data,headers=headers or ({'Content-Type':'application/json'} if data else {})))

    def test_async_jobs_progress_complete_and_busy_state(self):
        with self.request('/api/questions',{'question':'hello'}) as reply:
            self.assertEqual(reply.status,202);identity=json.load(reply)['id']
        self.assertTrue(self.pipeline.entered.wait(2))
        with self.request('/api/jobs/'+identity) as reply:self.assertEqual(json.load(reply)['stage'],'understanding')
        with self.assertRaises(urllib.error.HTTPError) as exc:self.request('/api/questions',{'question':'second'})
        self.assertEqual(exc.exception.code,409)
        self.pipeline.release.set()
        for _ in range(100):
            job=self.jobs.get(identity)
            if job['state']=='completed':break
            time.sleep(.01)
        self.assertEqual(job['report']['question'],'hello')

    def test_bad_requests_and_remote_origins_do_not_invoke_models(self):
        for path,payload,headers,status in [('/api/questions',{},None,400),
            ('/api/questions',{'question':'x'},{'Content-Type':'text/plain'},415),
            ('/api/questions',{'question':'x'},{'Content-Type':'application/json','Origin':'https://example.org'},403),
            ('/api/summary',None,{'Host':'example.org'},403),('/../../.env',None,None,404),
            ('/api/jobs/unknown',None,None,404),('/api/answer',{'question':'x'},None,404)]:
            with self.assertRaises(urllib.error.HTTPError) as exc:self.request(path,payload,headers)
            self.assertEqual(exc.exception.code,status)
        self.assertEqual(self.pipeline.calls,[])

    def test_failed_job_releases_slot_and_static_page_is_separate(self):
        self.pipeline.release.set();identity=self.jobs.submit({'question':'fail'})['id']
        for _ in range(100):
            job=self.jobs.get(identity)
            if job['state']=='failed':break
            time.sleep(.01)
        self.assertIn('model unavailable',job['error'])
        with self.request('/') as reply:
            html=reply.read().decode();self.assertIn('question-form',html);self.assertNotIn('qa-dialog',html)
            self.assertIn("script-src 'self'",reply.headers['Content-Security-Policy'])
        self.assertFalse(self.jobs.busy)


if __name__=='__main__':unittest.main()
