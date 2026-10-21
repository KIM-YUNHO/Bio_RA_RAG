"""FastAPI front of the RA service. It replaced the standard-library server on 2026-10-18 with the same URLs, JSON,
statuses and headers (plan: docs/project/FASTAPI_MIGRATION_PLAN_2026-10-18.md; the rules are in http_rules.py and
tests/test_http_contract.py pins them).

The app only receives requests and hands them to the objects the service already has (`jobs`: question jobs, reviews,
notebooks, the ingestion window, the evidence pages). Nothing here searches, answers or reads a database.

Differences from the earlier standard-library server, both deliberate:
  - an exception nobody expected answers 500 `{"error": ...}` (the old server closed the connection without a reply);
  - a method the service does not define (PUT, DELETE ...) answers 405 instead of 501.
"""
from functools import wraps
import json
import logging
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, Request, Response
from psycopg import Error as DatabaseError
from starlette.exceptions import HTTPException as StarletteHTTPException

from rag_service import ingestion_api, notebook_api
from rag_service.http_rules import (DATABASE_ERROR, DEFAULT_CACHE, EVIDENCE_ROUTE, JSON_TYPE, LOCAL_ORIGIN, MISSING, SECURITY_HEADERS,
    STATIC, STATIC_FILES, STATIC_TYPES, UNKNOWN_JOB, body_limit, is_ingestion_path, is_review_path, is_workspace_path, local_origin,
    post_path_known)
from rag_service.reviews import RevisionConflict

log = logging.getLogger('rag_service')
INTERNAL_ERROR = '서버 내부 오류가 발생했습니다.'


class ApiError(Exception):
    """An error with the status and `{"error": message}` body the client is told."""
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def json_response(body, status=200, cache=DEFAULT_CACHE):
    data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
    return Response(data, status_code=status, headers={'content-type': JSON_TYPE, 'cache-control': cache})


def binary_response(body, kind, cache):
    return Response(body, headers={'content-type': kind, 'cache-control': cache})


def failure(exc, method):
    """The response for an exception raised while serving a request; unknown exceptions are logged and answer 500."""
    if isinstance(exc, ApiError):
        return json_response({'error': exc.message}, exc.status)
    if method == 'GET':
        if isinstance(exc, KeyError):
            return json_response({'error': UNKNOWN_JOB}, 404)
        if isinstance(exc, DatabaseError):
            return json_response({'error': DATABASE_ERROR}, 503)
        if isinstance(exc, (ValueError, TypeError, OSError, RuntimeError)):
            return json_response({'error': str(exc)}, 400)
    else:
        if isinstance(exc, (BlockingIOError, RevisionConflict)):
            return json_response({'error': str(exc)}, 409)
        if isinstance(exc, KeyError):
            return json_response({'error': UNKNOWN_JOB}, 404)
        if isinstance(exc, DatabaseError):
            return json_response({'error': DATABASE_ERROR}, 503)
        if isinstance(exc, OSError):
            return json_response({'error': '검토 의견 저장 실패: ' + str(exc)}, 500)
        if isinstance(exc, (ValueError, TypeError)):
            return json_response({'error': str(exc)}, 400)
    log.error('unhandled %s request error', method, exc_info=exc)
    return json_response({'error': INTERNAL_ERROR}, 500)


def guarded(method):
    """Turn what a handler raises into the response the client gets (the status table kept from the earlier server)."""
    def wrap(handler):
        @wraps(handler)
        def run(*args, **kwargs):
            try:
                return handler(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - every failure must become an answer
                return failure(exc, method)
        return run
    return wrap


DISCARD_LIMIT = 1024 * 1024


async def discard(receive):
    """Read and drop what the client is still sending (up to 1 MB). A server that answers a refused POST and closes with the
    body unread makes Windows reset the connection, and the client sees a network error instead of the answer."""
    total = 0
    while total <= DISCARD_LIMIT:
        message = await receive()
        if message['type'] != 'http.request':
            break
        total += len(message.get('body', b''))
        if not message.get('more_body'):
            break


class Boundary:
    """Same-origin rule and response headers for every response, including errors the router itself produces."""
    def __init__(self, app, port):
        self.app, self.port = app, port

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = {k.decode('latin-1').lower(): v.decode('latin-1') for k, v in scope['headers']}

        async def finish(message):
            if message['type'] == 'http.response.start':
                present = {k.lower() for k, _ in message['headers']}
                extra = {name.lower(): value for name, value in SECURITY_HEADERS.items()}
                if b'cache-control' not in present:
                    extra['cache-control'] = DEFAULT_CACHE
                message['headers'] = [*message['headers'], *((k.encode('latin-1'), v.encode('latin-1')) for k, v in extra.items())]
            await send(message)

        if not local_origin(headers.get('host'), headers.get('origin'), self.port):
            await discard(receive)
            return await json_response({'error': LOCAL_ORIGIN}, 403)(scope, receive, finish)
        await self.app(scope, receive, finish)


async def json_payload(request: Request):
    """The POST gate, in the earlier server's order: known path, JSON content type, size, parse."""
    path = request.url.path
    try:
        if not post_path_known(path):
            raise ApiError(404, MISSING)
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise ApiError(415, 'JSON required')
        try:
            length = int(request.headers.get('content-length', '0'))
            if not 0 < length <= body_limit(path):
                raise ValueError('요청 크기를 확인하세요.')
            return json.loads(await request.body())
        except ValueError as exc:
            raise ApiError(400, str(exc)) from exc
    except ApiError:
        if not request._stream_consumed:
            await discard(request.receive)
        raise


def create_app(jobs, port):
    """The service over `jobs` for the given port (the port is part of the same-origin rule)."""
    app = FastAPI(title='RA 근거 검색 서비스', description='바이오시밀러 RA 근거 검색·메모·자료 적재 API (로컬 전용)',
                  docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    app.state.jobs = jobs
    app.add_middleware(Boundary, port=port)

    @app.exception_handler(ApiError)
    async def api_error(request, exc):
        return failure(exc, request.method)

    @app.exception_handler(StarletteHTTPException)
    async def not_served(request, exc):
        # Unknown paths and a known path with the wrong method are "not found" for the screen, as before.
        if exc.status_code in (404, 405) and request.method in ('GET', 'POST'):
            return json_response({'error': MISSING}, 404)
        return json_response({'error': str(exc.detail)}, exc.status_code)

    def get(path, summary=None, show=True):
        def register(handler):
            app.add_api_route(path, guarded('GET')(handler), methods=['GET'], summary=summary, include_in_schema=show,
                              response_model=None)
            return handler
        return register

    def post(path, summary=None, show=True):
        def register(handler):
            app.add_api_route(path, guarded('POST')(handler), methods=['POST'], summary=summary, include_in_schema=show,
                              response_model=None)
            return handler
        return register

    # ---- screen -------------------------------------------------------------------------------------------
    for url, name in STATIC_FILES.items():
        def page(name=name):
            return Response((STATIC / name).read_bytes(), headers={'content-type': STATIC_TYPES[Path(name).suffix], 'cache-control': DEFAULT_CACHE})
        get(url, show=False)(page)

    @get('/openapi.json', show=False)
    def openapi():
        return json_response(app.openapi())  # same JSON type as every other answer

    # The API reference page (only this app has /openapi.json); its files come from the repository, not a CDN.
    for url, name in {'/api-docs': 'api-docs.html', '/api-docs.js': 'api-docs.js', '/api-docs.css': 'api-docs.css'}.items():
        def doc_page(name=name):
            kind = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8'}[Path(name).suffix]
            return Response((STATIC / name).read_bytes(), headers={'content-type': kind, 'cache-control': DEFAULT_CACHE})
        get(url, show=False)(doc_page)

    @get('/favicon.ico', show=False)
    def favicon():
        return Response(b'', status_code=204, headers={'content-type': 'image/x-icon', 'cache-control': 'private, max-age=86400'})

    # ---- service state ------------------------------------------------------------------------------------
    @get('/api/summary', '서비스 요약: 코드 버전·릴리스·모델·전략 (답변 생성 없음)')
    def summary():
        return json_response(jobs.pipeline.summary())

    @get('/api/inventory', '왼쪽 범위 선택용 성분·제품·기관·문서 목록')
    def inventory():
        return json_response(jobs.inventory())

    # ---- notebooks and the ingestion window: their adapters match the path themselves --------------------------
    def through(adapter):
        def handler(request: Request):
            result = adapter.get(jobs, request.url.path)
            if result is None:
                raise ApiError(404, MISSING)
            return json_response(result)
        return handler

    for url, summary_text in (('/api/projects', '파이프라인 목록과 단계'), ('/api/projects/{project_id}', '파이프라인 한 건과 메모'),
                              ('/api/projects/{project_id}/briefs/{task}', '저장 항목을 업무별로 모은 내부 검토용 보기'),
                              ('/api/projects/{project_id}/documents', '등록한 내부 문서(버전·상태·공유 범위·본문 단위)'),
                              ('/api/projects/{project_id}/workboard/{as_of}', '기준일의 업무 상태·내부 자료 (원문 이벤트에서 계산)'),
                              ('/api/projects/{project_id}/work-drafts', '업무 초안 목록'),
                              ('/api/projects/{project_id}/work-drafts/{draft_id}', '업무 초안 한 건: 입력·답변·검사'),
                              ('/api/projects/{project_id}/issues', '등록한 이슈(사내 근거 단위·공개 자료 질문)'),
                              ('/api/projects/{project_id}/issue-guides', '이슈 대응 가이드 목록'),
                              ('/api/projects/{project_id}/issue-guides/{guide_id}', '이슈 대응 가이드 한 건: 검색 답변·가이드·정답 대조')):
        get(url, summary_text)(through(notebook_api))
    for url, summary_text in (('/api/ingestion', '자료 수집 창: 대화·요청·릴리스 목록'),
                              ('/api/ingestion/sessions/{session_id}', '적재 대화 한 건'),
                              ('/api/ingestion/requests/{request_id}', '적재 요청 한 건과 이벤트'),
                              ('/api/ingestion/requests/{request_id}/logs', '적재 요청의 작업 로그')):
        get(url, summary_text)(through(ingestion_api))

    # ---- question jobs, evidence and conversations ----------------------------------------------------------
    def job_records(request: Request):
        path = request.url.path
        route = EVIDENCE_ROUTE.fullmatch(path)
        if route:
            identity, result_index, eid = route[1], int(route[2]), route[3]
            job = jobs.get(identity)
            if route[4]:
                return binary_response(jobs.pages.page_png(job, result_index, eid, int(route[5])), 'image/png', 'private, max-age=3600')
            if route[6]:
                return binary_response(jobs.pages.crop_png(job, result_index, eid), 'image/png', 'private, max-age=3600')
            if route[7]:
                return binary_response(jobs.pages.document_pdf(job, result_index, eid), 'application/pdf', 'private, max-age=3600')
            page = parse_qs(request.url.query).get('page', [''])[0]
            if page and not page.isdigit():
                raise ValueError('페이지 범위를 확인하세요.')
            return json_response(jobs.pages.detail(job, result_index, eid, int(page) if page else None))
        if is_review_path(path):
            return json_response(jobs.reviews.get(jobs.get(path[len('/api/jobs/'):-len('/review')])))
        return json_response(jobs.get(path.removeprefix('/api/jobs/')))

    @get('/api/jobs', '질문 목록 (limit·offset·q)')
    def job_list(request: Request):
        params = parse_qs(request.url.query)
        return json_response(jobs.list_records(int(params.get('limit', ['20'])[0]), int(params.get('offset', ['0'])[0]), params.get('q', [''])[0]))

    for url, summary_text in (('/api/jobs/{job_id}', '질문 한 건의 상태·결과'), ('/api/jobs/{job_id}/review', '답변별 검토 기록'),
                              ('/api/jobs/{job_id}/evidence/{result_index}/{evidence_id}', '근거 상세 (?page=)'),
                              ('/api/jobs/{job_id}/evidence/{result_index}/{evidence_id}/page/{page}', '근거 원문 페이지 이미지'),
                              ('/api/jobs/{job_id}/evidence/{result_index}/{evidence_id}/crop', '근거 영역 이미지'),
                              ('/api/jobs/{job_id}/evidence/{result_index}/{evidence_id}/document.pdf', '근거 원본 PDF')):
        get(url, summary_text)(job_records)
    get('/api/jobs/{rest:path}', show=False)(job_records)  # any other path below /api/jobs/ is read as a job id, as before

    @get('/api/conversations/{rest:path}', '대화 기록')
    def conversation(rest: str):
        return json_response(jobs.conversations.get(rest))

    # ---- writes -----------------------------------------------------------------------------------------------
    def write(request: Request, payload=Depends(json_payload)):
        path = request.url.path
        if is_workspace_path(path):
            return json_response(notebook_api.post(jobs, path, payload))
        if is_ingestion_path(path):
            return json_response(ingestion_api.post(jobs, path, payload))
        if is_review_path(path):
            return json_response(jobs.reviews.append(jobs.get(path[len('/api/jobs/'):-len('/review')]), payload))
        return json_response(jobs.submit(payload), 202)

    for url, summary_text in (('/api/questions', '질문 접수 (모델 호출 발생)'), ('/api/jobs/{job_id}/review', '답변 검토 기록 추가'),
                              ('/api/projects', '파이프라인 만들기'), ('/api/projects/{project_id}/memos', '메모 저장'),
                              ('/api/projects/{project_id}/memos/{memo_id}/revisions', '메모 수정'),
                              ('/api/projects/{project_id}/memos/{memo_id}/structure', '원 보고서에서 기존 메모 항목 복원'),
                              ('/api/projects/{project_id}/documents', '내부 문서 등록 (같은 버전은 바꿀 수 없음)'),
                              ('/api/projects/{project_id}/work-drafts', '업무 초안 작성 접수 (모델 호출 1회)'),
                              ('/api/projects/{project_id}/issue-guides', '이슈 대응 가이드 작성 접수 (모델 3회 + 공개 자료 검색)'),
                              ('/api/projects/{project_id}/issue-guides/{guide_id}/delete', '이슈 대응 가이드 삭제 (만드는 중이면 거절)'),
                              ('/api/ingestion/sessions', '새 적재 대화'),
                              ('/api/ingestion/sessions/{session_id}/turns', '적재 대화 한 턴'),
                              ('/api/ingestion/sessions/{session_id}/approve', '확인표 승인'),
                              ('/api/ingestion/sessions/{session_id}/actions', '후보 선택·수집 시작 버튼'),
                              ('/api/ingestion/sessions/{session_id}/delete', '적재 대화 삭제')):
        post(url, summary_text)(write)
    for url in ('/api/projects/{rest:path}', '/api/ingestion/{rest:path}', '/api/jobs/{rest:path}'):
        post(url, show=False)(write)  # every other path of these areas is answered by the area's adapter
    return app
