# RA 서비스 HTTP 계층 FastAPI 전환 계획

- 작성: 2026-10-18 KST
- 상태: **계획**. 코드는 바꾸지 않았다.
- 기준 코드: `feature/memo-reader-session-delete` `7bcd3b8` (PR #12) — `rag_service/__main__.py`의 `handler()`·`main()`, `rag_service/notebook_api.py`, `rag_service/ingestion_api.py`의 `get`·`post`
- 범위: RA 서비스(8775·8885)의 요청 처리 껍데기. 원본 검토 화면(`review_viewer`, 8886)과 전처리 도구의 서버는 대상이 아니다.

## 1. 목표와 하지 않는 것

목표는 **요청을 받아 기존 함수에 넘기는 부분만** FastAPI로 옮기는 것이다. URL·JSON 형식·상태 코드·보안 헤더를 지금과 같게 유지해서 화면(`rag_service/web/`)과 DOM 검사를 고치지 않는다.

| 얻는 것 | 내용 |
| :-- | :-- |
| API 명세 | `/openapi.json`과 명세 화면이 코드에서 자동으로 만들어진다 |
| 입력 검증 | 요청 본문을 Pydantic 모델로 검사(6단계, 선택) |
| 확장 자리 | 허용 호스트·인증·요청 기록을 미들웨어와 의존성으로 붙일 수 있다 |
| 표준 실행 | `uvicorn`으로 띄우고, 나중에 리버스 프록시 뒤에 두기 쉽다 |

하지 않는 것: 질문 동시 처리(대기열), 사용자 식별·권한, 내부망 바인딩, 여러 워커 실행. FastAPI로 옮겨도 이 문제들은 그대로이며 내부망 단계에서 따로 한다. 워커를 늘리면 프로세스마다 임베딩·리랭커 모델을 다시 올리므로 워커는 1개로 둔다.

## 2. 지금 HTTP 계층이 하는 일 (옮길 목록)

### 2.1 경로

| 메서드 | 경로 | 넘기는 곳 | 응답 |
| :-- | :-- | :-- | :-- |
| GET | `/` 외 화면 파일 10개(`STATIC_FILES`) | `rag_service/web/` 파일 | html·js·css, `no-store` |
| GET | `/favicon.ico` | — | 204, `private, max-age=86400` |
| GET | `/api/summary` | `pipeline.summary()` | JSON (시작 스크립트의 상태 확인에도 쓰임) |
| GET | `/api/inventory` | `jobs.inventory()` | JSON |
| GET | `/api/projects`, `/api/projects/{id}` | `notebook_api.get` | JSON |
| GET | `/api/ingestion`, `/api/ingestion/sessions/{uuid}`, `/api/ingestion/requests/{uuid}[/logs]` | `ingestion_api.get` | JSON |
| GET | `/api/jobs/{id}/evidence/{n}/{eid}` (`?page=`) | `pages.detail` | JSON |
| GET | `…/evidence/{n}/{eid}/page/{p}`, `…/crop`, `…/document.pdf` | `pages.page_png`·`crop_png`·`document_pdf` | PNG·PDF, `private, max-age=3600` |
| GET | `/api/jobs?limit&offset&q` | `jobs.list_records` | JSON |
| GET | `/api/conversations/{id}` | `conversations.get` | JSON |
| GET | `/api/jobs/{id}/review`, `/api/jobs/{id}` | `reviews.get`, `jobs.get` | JSON |
| POST | `/api/questions` | `jobs.submit` | 202 |
| POST | `/api/jobs/{id}/review` | `reviews.append` | JSON |
| POST | `/api/projects`, `/api/projects/{id}/memos`, `/api/projects/{id}/memos/{m}/revisions` | `notebook_api.post` | JSON |
| POST | `/api/ingestion/sessions`, `/api/ingestion/sessions/{uuid}/turns·approve·actions·delete` | `ingestion_api.post` | JSON |

### 2.2 공통 규칙

| 규칙 | 지금 동작 |
| :-- | :-- |
| 출처 검사 | `Host`가 `127.0.0.1:<포트>`·`localhost:<포트>`이고 `Origin`이 없거나 같은 주소일 때만 처리, 아니면 403 `{"error":"Local origin required"}` (GET·POST 모두) |
| 보안 헤더 | 모든 응답에 `X-Content-Type-Options: nosniff`, CSP(`default-src 'self'` …, `frame-ancestors 'none'`), `Cache-Control` 기본 `no-store` |
| POST 본문 | 등록되지 않은 경로는 먼저 404. `Content-Type`이 `application/json`이 아니면 415. 길이 1~16,384바이트(검토·프로젝트는 65,536), 벗어나면 400 |
| 오류 → 상태 코드 (GET) | `KeyError` 404 "등록되지 않은 질문입니다.", `psycopg.Error` 503, `ValueError`·`TypeError`·`OSError`·`RuntimeError` 400, 없는 경로 404 |
| 오류 → 상태 코드 (POST) | `BlockingIOError`·`RevisionConflict` 409, `KeyError` 404, `psycopg.Error` 503, `OSError` 500 "검토 의견 저장 실패: …", `ValueError`·`TypeError` 400 |
| JSON | `ensure_ascii=False`, `allow_nan=False` |
| 기타 | 접속 기록 출력 안 함. `127.0.0.1` 고정 바인딩. 요청마다 스레드(`ThreadingHTTPServer`), 질문 처리는 `Jobs`의 작업 스레드 1개 |

GET과 POST에서 `OSError`의 상태 코드가 다르다(400 대 500). 전환 단계에서는 **그대로 옮기고**, 고칠지는 전환 후 따로 정한다.

## 3. 설계

- 새 파일 `rag_service/app.py`의 `create_app(jobs, port)`가 FastAPI 앱을 만든다. `Jobs`, 저장소, `notebook_api`·`ingestion_api`의 함수는 그대로 쓴다.
- 엔드포인트는 일반 함수(`def`)로 둔다. FastAPI가 스레드 풀에서 돌리므로 지금의 요청별 스레드와 같다. 질문 처리는 계속 `Jobs`의 작업 스레드 1개에서 돈다.
- 경로 변수에 정규식 제약을 걸지 않는다. 형식이 틀린 ID도 지금처럼 기존 함수가 `KeyError`·`ValueError`를 내게 해서 같은 404·400이 나오게 한다.
- 1단계에서는 POST 본문을 원시 바이트로 읽어 지금의 크기 제한과 오류 문구를 그대로 쓴다. Pydantic 요청 모델은 6단계에서 넣는다.
- 미들웨어 두 개: ① 출처 검사(403), ② 보안 헤더 추가(응답에 `Cache-Control`이 없으면 `no-store`).
- 예외 처리기로 2.2의 상태 코드 표를 옮긴다. GET·POST 차이는 처리기 안에서 요청 메서드로 구분한다. FastAPI 기본 입력 오류(422)는 400 `{"error": …}`로 바꾼다.
- 화면 파일은 `StaticFiles` 마운트 대신 지금 목록(`STATIC_FILES`)만 명시적으로 내보낸다. 허용 목록과 `Content-Type`을 그대로 유지하기 위해서다.
- `redirect_slashes=False`로 둔다. FastAPI 기본값은 끝 슬래시가 다르면 307로 돌려보내는데, 지금 서버는 404를 낸다.
- 서버 실행은 `uvicorn.run(app, host="127.0.0.1", port=…, workers=1, access_log=False)`. 종료 때 `jobs.close()`·`pipeline.close()`를 부른다(lifespan).
- 명세 화면: FastAPI 기본 Swagger 화면은 CDN(jsdelivr)에서 JS·CSS를 받는다. 지금 CSP(`'self'`만 허용)에 막히고 폐쇄망에서는 CDN에 닿지 않는다. 그래서 `/openapi.json`만 열어 두고, 명세 화면은 자산을 저장소 안에 두는 방식으로 따로 붙인다(5단계).
- JSON 직렬화 차이: FastAPI(Starlette) 응답은 구분자 공백이 없다(`{"a":1}`). 내용은 같고 화면은 JSON으로 파싱하므로 영향이 없다. 바이트 단위로 비교하는 테스트가 있으면 내용 비교로 바꾼다.

## 4. 단계

각 단계가 끝날 때마다 전체 테스트(`RA_TEST_POSTGRES=1 python -m unittest discover -s tests`)와 DOM 검사 3종을 통과시킨다.

| 단계 | 할 일 | 끝난 상태 |
| :-- | :-- | :-- |
| 0 | 의존성 `fastapi`, `uvicorn` 추가(`pyproject.toml`·`uv.lock`). 폐쇄망 반입용 설치 파일 목록에도 넣는다 | 설치만 됨 |
| 1 | **HTTP 계약 테스트 먼저** (`tests/test_http_contract.py`): 2장의 경로·상태 코드·헤더·오류 문구·본문 제한·출처 검사를 지금 서버로 고정. 서버를 띄우는 부분은 구현을 바꿔 끼울 수 있게 만든다 | 지금 서버가 계약 테스트 통과 |
| 2 | `rag_service/app.py` 구현. `main()`에 `--server stdlib|fastapi`(기본 stdlib) | 두 구현이 같은 계약 테스트 통과 |
| 3 | 테스트 준비 코드 전환: `tests/test_workspace_http.py`의 `fixture()`가 고른 구현을 띄우게 하고, HTTP 테스트 6개 파일·DOM 검사 3종·`eval/ra_today_deployment_verify.py`를 두 구현으로 확인 | 기존 테스트 전부 두 구현에서 통과 |
| 4 | 기본값을 `fastapi`로. 8775·8885를 재시작해 실제 브라우저로 화면·질문·메모·근거 원문·자료 수집 창 확인. `scripts/start_ra_service.ps1`의 상태 확인(`/api/summary`)은 그대로 | 서비스가 FastAPI로 운영 |
| 5 | 명세 공개: `/openapi.json` 저장본을 `docs/`에 두고, 자산을 저장소 안에 둔 명세 화면 추가. `docs/current/06_SERVICE_UI.md`·`rag_service/README.md` 갱신 | API 명세를 화면으로 볼 수 있음 |
| 6 | (선택) Pydantic 요청 모델: 질문, 메모·수정, 적재 턴·승인·버튼·삭제. 지금 손으로 하는 검사와 같은 규칙으로 옮기고 명세에 반영 | 입력 검증이 명세와 일치 |
| 7 | 한동안 운영한 뒤 `--server stdlib`와 기존 `handler()` 제거 | 코드 정리 |

되돌리기: 7단계 전까지는 `--server stdlib`로 언제든 예전 서버로 띄울 수 있다.

## 5. 영향 받는 파일

| 구분 | 파일 |
| :-- | :-- |
| 새로 만듦 | `rag_service/app.py`, `tests/test_http_contract.py` |
| 고침 | `rag_service/__main__.py`(`main()` 서버 선택, 7단계에서 `handler()` 제거), `tests/test_workspace_http.py`(`fixture()`), `pyproject.toml`·`uv.lock`, `docs/current/06_SERVICE_UI.md`, `rag_service/README.md` |
| 두 구현으로 확인만 | `tests/test_rag_service.py`, `test_rag_service_ui.py`, `test_followup.py`, `test_ra_today_service.py`, `test_workspace_postgres.py`, `eval/*_dom_check.js` 3종, `eval/ra_today_deployment_verify.py`, `scripts/start_ra_service.ps1` |
| 바뀌지 않음 | `rag/`, `ingestion/`, `storage/`, `rag_service/web/`, DB |

## 6. 위험과 대응

| 위험 | 대응 |
| :-- | :-- |
| 상태 코드·오류 문구·헤더가 미묘하게 달라짐 | 1단계 계약 테스트를 두 구현에 같이 돌린다 |
| 경로 처리 차이(끝 슬래시 리다이렉트, 입력 오류 422, 쿼리 숫자 변환) | `redirect_slashes=False`, 422 → 400 변환, 쿼리는 지금처럼 문자열로 받아 기존 함수가 변환 |
| 출처 검사가 일부 경로에서 빠짐 | 미들웨어로 모든 경로(명세 경로 포함)에 적용하고 계약 테스트로 확인 |
| 명세 화면이 CDN·CSP에 막힘 | 자산을 저장소 안에 두고 같은 출처에서 내보냄 |
| 새 의존성의 폐쇄망 설치 | `uv.lock` 고정, 설치 파일을 반입 목록에 포함 |
| 성능 | 워커 1개·스레드 풀로 지금과 같은 구조. 질문 처리 시간은 LLM 호출이 대부분이라 영향 없음 |

## 7. 예상 작업량

0~2단계 하루, 3~4단계 반나절, 5~6단계 반나절에서 하루. 7단계는 운영 확인 뒤.

## 8. 보류한 내부망 작업과의 관계

질문 대기열, 사용자 식별·권한, 허용 호스트 설정은 이 전환 뒤에 FastAPI의 미들웨어·의존성으로 붙이면 지금 서버에 붙이는 것보다 간단하다. 전환 자체는 그 작업들의 전제 조건이 아니다.
