"""Rules of the RA service's HTTP layer that the standard-library server (rag_service/__main__.py) and the FastAPI app
(rag_service/app.py) both apply. tests/test_http_contract.py checks that they answer the same."""
from pathlib import Path
import re

STATIC = Path(__file__).parent/'web'
STATIC_FILES = {'/':'index.html','/style.css':'style.css','/common.js':'common.js','/evidence-view.js':'evidence-view.js',
    '/inventory.js':'inventory.js','/viewer.js':'viewer.js','/review-notes.js':'review-notes.js','/app.js':'app.js',
    '/pipeline-notes.js':'pipeline-notes.js','/data-manager.js':'data-manager.js','/data-manager.css':'data-manager.css',
    '/work-assist.js':'work-assist.js'}
STATIC_TYPES = {'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8'}
EVIDENCE_ROUTE = re.compile(r'/api/jobs/([0-9a-f]{32})/evidence/(\d{1,3})/([A-Za-z0-9_-]{1,32})(?:/(page)/(\d{1,5})|/(crop)|/(document\.pdf))?')

JSON_TYPE = 'application/json; charset=utf-8'
DEFAULT_CACHE = 'no-store'
SECURITY_HEADERS = {'X-Content-Type-Options':'nosniff',
    'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}

MISSING = '찾을 수 없습니다.'
UNKNOWN_JOB = '등록되지 않은 질문입니다.'
DATABASE_ERROR = 'DB 연결 또는 스키마를 확인하세요. Docker 실행과 ingestion.db migrate가 필요합니다.'
LOCAL_ORIGIN = 'Local origin required'


def local_origin(host, origin, port):
    """Only this machine's address and port may talk to the service (and no foreign Origin)."""
    allowed = (f'127.0.0.1:{port}', f'localhost:{port}')
    return host in allowed and origin in (None, *('http://'+name for name in allowed))


def is_review_path(path):
    return path.startswith('/api/jobs/') and path.endswith('/review')


def is_workspace_path(path):
    return path == '/api/projects' or path.startswith('/api/projects/')


def is_ingestion_path(path):
    return path.startswith('/api/ingestion/')


def post_path_known(path):
    return path == '/api/questions' or is_review_path(path) or is_workspace_path(path) or is_ingestion_path(path)


def body_limit(path):
    """Largest accepted JSON body for a POST path, in bytes."""
    if is_workspace_path(path) and path.endswith('/documents'):
        return 524288  # an internal document bundle
    return 65536 if is_review_path(path) or is_workspace_path(path) else 16384
