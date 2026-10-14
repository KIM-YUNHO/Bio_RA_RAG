import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from rag.release import load_bundle as Bundle

STATIC=Path(__file__).parent/'static'


def handler(bundle):
    class Handler(BaseHTTPRequestHandler):
        def send(self,body,kind='application/json; charset=utf-8',status=200):
            if isinstance(body,(dict,list)):body=json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store' if kind.startswith('application/json') else 'private, max-age=3600')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers();self.wfile.write(body)

        def do_GET(self):
            host=self.headers.get('Host','')
            if host not in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'):
                return self.send({'error':'Local host required'},status=403)
            route=urlsplit(self.path);path=unquote(route.path);args=parse_qs(route.query)
            try:
                if path in ('/','/app.js','/style.css'):
                    name={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}[path]
                    kind={'index.html':'text/html; charset=utf-8','app.js':'text/javascript; charset=utf-8','style.css':'text/css; charset=utf-8'}[name]
                    return self.send((STATIC/name).read_bytes(),kind)
                if path=='/api/summary':return self.send(bundle.summary())
                if path=='/api/pages':return self.send(bundle.page_list(args.get('doc',[''])[0],args.get('q',[''])[0],args.get('kind',['all'])[0]))
                if path.startswith('/api/page/'):return self.send(bundle.page_detail(path.removeprefix('/api/page/')))
                if path.startswith('/api/asset/'):return self.send(bundle.asset_detail(path.removeprefix('/api/asset/')))
                if path.startswith('/page-image/'):return self.send(bundle.render(path.removeprefix('/page-image/')),'image/png')
                if path.startswith('/image/'):
                    image=bundle.images[path.removeprefix('/image/')]
                    kind={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp'}[image.suffix.lower()]
                    return self.send(image.read_bytes(),kind)
                if path.startswith('/source/') and path.endswith('.pdf'):
                    document=bundle.docs[path.removeprefix('/source/').removesuffix('.pdf')]
                    source=Path(document.get('rendered_path') or document['raw_path'])
                    return self.pdf(source)
                return self.send({'error':'찾을 수 없습니다.'},status=404)
            except (BrokenPipeError,ConnectionResetError):pass
            except KeyError:return self.send({'error':'등록되지 않은 문서 또는 영역입니다.'},status=404)
            except (ValueError,OSError) as exc:return self.send({'error':str(exc)},status=400)

        def pdf(self,source):
            size=source.stat().st_size;start,end=0,size-1;partial=False
            request=self.headers.get('Range')
            if request:
                import re
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',request)
                if not match or not any(match.groups()):return self.send({'error':'Invalid range'},status=416)
                if match[1]:start=int(match[1]);end=min(int(match[2]),size-1) if match[2] else size-1
                else:start=max(0,size-int(match[2]))
                if start> end or start>=size:return self.send({'error':'Invalid range'},status=416)
                partial=True
            self.send_response(206 if partial else 200)
            self.send_header('Content-Type','application/pdf');self.send_header('Accept-Ranges','bytes')
            self.send_header('Content-Length',str(end-start+1));self.send_header('X-Content-Type-Options','nosniff')
            if partial:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            with source.open('rb') as stream:
                stream.seek(start);remaining=end-start+1
                while remaining:
                    block=stream.read(min(remaining,131072))
                    if not block:break
                    self.wfile.write(block);remaining-=len(block)

        def log_message(self,format,*args):
            pass
    return Handler


def main():
    parser=argparse.ArgumentParser(description='로컬 전처리 결과 시각 검토 — 읽기 전용, 모델 호출 없음')
    parser.add_argument('--run',type=Path,required=True,help='완료된 workflow 또는 extraction/labels 폴더')
    parser.add_argument('--candidate',type=Path,help='선택: 대응하는 청크 후보 폴더')
    parser.add_argument('--port',type=int,default=8766)
    args=parser.parse_args()
    if not 1<=args.port<=65535:parser.error('Invalid port')
    bundle=Bundle(args.run,args.candidate)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),handler(bundle))
    print(f'문서 검토 페이지: http://127.0.0.1:{server.server_port}',flush=True)
    print(json.dumps(bundle.summary(),ensure_ascii=False),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
