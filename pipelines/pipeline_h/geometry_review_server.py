"""Loopback-only viewer for the review and its explicitly listed source files."""
import argparse,json,mimetypes,re
from pathlib import Path
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pipelines.pipeline_h.prepare import read,write

def main(folder):
    folder=Path(folder).resolve();items=read(folder/'review_manifest.json')['items']
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(folder),**kwargs)
        def do_GET(self):
            match=re.fullmatch(r'/source/(\d+)/(pdf|original)',self.path.split('?',1)[0])
            if not match:return super().do_GET()
            index=int(match[1])
            if index>=len(items):return self.send_error(404)
            source=Path(items[index]['source_path'] if match[2]=='pdf' else items[index]['original_path'])
            content=source.read_bytes();self.send_response(200)
            self.send_header('Content-Type',mimetypes.guess_type(source.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length',str(len(content)))
            if match[2]=='original' and source.suffix.lower()!='.pdf':self.send_header('Content-Disposition','attachment')
            self.end_headers();self.wfile.write(content)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    write(folder/'server.json',{'url':f'http://127.0.0.1:{server.server_port}/index.html','bind':'127.0.0.1'})
    server.serve_forever()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',required=True);a=p.parse_args();main(a.folder)
