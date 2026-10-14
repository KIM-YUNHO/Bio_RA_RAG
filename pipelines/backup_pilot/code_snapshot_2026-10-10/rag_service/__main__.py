import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Lock
import time
from urllib.parse import urlsplit
from uuid import uuid4

from preprocess.hybrid_config import load_config
from rag.pipeline import RAGPipeline

STATIC = Path(__file__).parent/'static'


class Jobs:
    def __init__(self,pipeline):
        self.pipeline=pipeline;self.lock=Lock();self.jobs={};self.busy=False
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='rag-query')

    def submit(self,payload):
        if not isinstance(payload,dict) or set(payload)-{'question','products','jurisdictions','documents','include_nondefault'}:
            raise ValueError('Invalid question request')
        self.pipeline.validate_request(payload.get('question'),payload.get('products'),payload.get('jurisdictions'),payload.get('documents'),payload.get('include_nondefault',False))
        with self.lock:
            if self.busy:raise BlockingIOError('다른 질문을 처리 중입니다. 완료 후 다시 시도하세요.')
            self.busy=True
            identity=uuid4().hex
            self.jobs[identity]={'id':identity,'state':'running','stage':'queued','message':'질문 처리를 준비하고 있습니다.',
                'created_at':time.time(),'question':payload['question']}
            for old in list(self.jobs)[:-20]:
                if self.jobs[old]['state']!='running':del self.jobs[old]
        self.executor.submit(self.run,identity,payload)
        return {'id':identity,'state':'running'}

    def run(self,identity,payload):
        def progress(**event):
            with self.lock:self.jobs[identity].update(event)
        try:
            report=self.pipeline.ask(payload['question'],payload.get('products'),payload.get('jurisdictions'),payload.get('documents'),progress,include_nondefault=payload.get('include_nondefault',False))
            with self.lock:self.jobs[identity].update(state='completed',stage='completed',message='답변 처리가 끝났습니다.',report=report)
        except Exception as exc:
            with self.lock:self.jobs[identity].update(state='failed',stage='failed',message='질문 처리에 실패했습니다.',error=f'{type(exc).__name__}: {exc}',diagnostics=getattr(self.pipeline,'last_failure',None))
        finally:
            with self.lock:self.busy=False

    def get(self,identity):
        with self.lock:return deepcopy(self.jobs[identity])

    def close(self):self.executor.shutdown(wait=True)


def handler(jobs):
    class Handler(BaseHTTPRequestHandler):
        def send(self,body,kind='application/json; charset=utf-8',status=200):
            if isinstance(body,(dict,list)):body=json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8')
            self.send_response(status)
            for key,value in {'Content-Type':kind,'Content-Length':str(len(body)),'Cache-Control':'no-store',
                'X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}.items():self.send_header(key,value)
            self.end_headers();self.wfile.write(body)

        def local(self):
            allowed=(f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
            return self.headers.get('Host') in allowed and self.headers.get('Origin') in (None,*('http://'+host for host in allowed))

        def do_GET(self):
            if not self.local():return self.send({'error':'Local origin required'},status=403)
            path=urlsplit(self.path).path
            try:
                if path in ('/','/app.js','/style.css'):
                    name={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}[path]
                    kind={'index.html':'text/html; charset=utf-8','app.js':'text/javascript; charset=utf-8','style.css':'text/css; charset=utf-8'}[name]
                    return self.send((STATIC/name).read_bytes(),kind)
                if path=='/api/summary':return self.send(jobs.pipeline.summary())
                if path.startswith('/api/jobs/'):return self.send(jobs.get(path.removeprefix('/api/jobs/')))
                return self.send({'error':'찾을 수 없습니다.'},status=404)
            except KeyError:return self.send({'error':'등록되지 않은 질문입니다.'},status=404)
            except (BrokenPipeError,ConnectionResetError):pass

        def do_POST(self):
            if not self.local():return self.send({'error':'Local origin required'},status=403)
            if self.path!='/api/questions':return self.send({'error':'찾을 수 없습니다.'},status=404)
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send({'error':'JSON required'},status=415)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=16384:raise ValueError('질문 요청 크기를 확인하세요.')
                payload=json.loads(self.rfile.read(length))
                return self.send(jobs.submit(payload),status=202)
            except BlockingIOError as exc:return self.send({'error':str(exc)},status=409)
            except (ValueError,TypeError) as exc:return self.send({'error':str(exc)},status=400)
            except (BrokenPipeError,ConnectionResetError):pass

        def log_message(self,*args):pass
    return Handler


def main():
    parser=argparse.ArgumentParser(description='로컬 RA RAG 메인 서비스')
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--candidate',type=Path)
    parser.add_argument('--config',type=Path,default=Path('configs/preprocess.local.json'))
    parser.add_argument('--review-url',default='http://127.0.0.1:8766')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--retrieval-pages',type=int,default=5,choices=range(1,11))
    args=parser.parse_args()
    if not 1<=args.port<=65535:parser.error('Invalid port')
    pipeline=RAGPipeline(args.run,args.candidate,load_config(args.config),args.review_url,retrieval_pages=args.retrieval_pages)
    jobs=Jobs(pipeline)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),handler(jobs))
    print(f'RA RAG 메인 서비스: http://127.0.0.1:{args.port}/',flush=True)
    print(json.dumps(pipeline.summary(),ensure_ascii=False),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();jobs.close()


if __name__=='__main__':main()
