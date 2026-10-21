# 6. 서비스·화면 — 현재 상태

- 작성: 2026-10-16 16:50 KST
- 수정: 2026-10-17 17:45 KST (두 서비스 재시작, 원본 검토 링크 제거, 메모 상세 창·질문 카드, 적재 대화 삭제 API)
- 수정: 2026-10-18 04:45 KST (HTTP 계층 FastAPI 전환: 8885는 FastAPI, 8775는 기존 서버. API 명세 화면)
- 수정: 2026-10-18 23:15 KST (내부 문서·업무 상태·업무 초안 기능. 8885만 새 코드로 재시작, 8775는 15:10 시작본 그대로)
- 수정: 2026-10-21 11:35 KST (코드 정리: HTTP 계층은 FastAPI만, `--server`·`--review-url`·`review_viewer`·`rag_service/static/` 제거. 떠 있는 8775·8897은 정리 전 코드)
- 기준: 실행 중 프로세스 명령줄·시작 시각, `GET /api/summary`·`/api/projects`·`/api/ingestion`, `rag_service/`(`__main__.py`, `notebook_api.py`, `ingestion_api.py`, `web/`), `scripts/start_ra_service.ps1`
- 상위: [현재 서빙 상태](README.md)

## 1. 실행 중인 프로세스

| 포트 | 시작 | 시작 방법 | 차이 |
| :-- | :-- | :-- | :-- |
| **8775** | 2026-10-18 15:10 | `scripts/start_ra_service.ps1` (로그 `data/runs/ra_service_logs/20261018_151001_*`) | 업무 초안 API 없음. 화면 파일은 요청마다 읽으므로 `업무 현황·AI 초안` 버튼이 비활성으로 보인다. 다음 재시작 때 반영 |
| **8897** (운영) | 2026-10-20 18:11 | PR #17 브랜치 작업 폴더(`project_shared_check`)의 `work/ra_service_8897.py`. 시작 스크립트와 같은 인자, 작업 폴더는 메인 checkout(로그 `data/runs/ra_service_logs/20261020_181113_8897.*`) | 이슈 대응 가이드·파이프라인 업무 요약 줄 포함, 파이프라인 `ra-research-rag-4.10.4`. 저장소는 8775와 같은 PostgreSQL·`data/runs`. PR이 merge되고 메인 checkout이 main을 받으면 `scripts/start_ra_service.ps1 -Port 8897`로 바꾼다. 정리 후 코드는 `--server`·`--review-url` 인자를 받지 않으므로, 그 인자를 넘기는 시작 방법(`work/ra_service_8897.py` 등)은 정리 후 코드에 쓸 수 없다 |
| ~~8885~~ | 2026-10-19 04:15 ~ 10-20 내림 | `scripts/start_ra_service.ps1 -Port 8885 -ReviewUrl http://127.0.0.1:8886` | 10-20에 내리고 8897로 운영 전환 |

8897로 옮긴 것(10-20): 확인 서버(임시 파일 저장소)에서 만든 `[시나리오 v5] P17 IV FcRn 이슈 대응` 파이프라인(문서 묶음 재등록)과 남아 있던 가이드 1건, 그 검색 답변 7건과 메모가 가리키는 답변 1건(보고서 폴더로 복사), `제품 사전 조사 프로젝트`와 메모 1개(같은 답변·주장에서 다시 저장).

공통 인자:

```text
--mode ra --llm-model gpt-5.6-luna
--run data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release
--guidelines data/guidelines/ra_guidelines_native_2026-10-12_v2
--review-url http://127.0.0.1:8886 --research-rounds 1 --warmup
--archive-dir data/runs/ra_service_reports --review-dir data/runs/ra_review_records
```

- 위 인자 중 `--review-url`(그리고 실행 명령줄의 `--server fastapi`)는 정리 전 코드의 인자다. 10-21 정리 후 코드로 다시 띄우면 두 인자는 없다.
- 두 프로세스 모두 main `96894d3`(stable-queries·hide-model-gaps 병합) + `feature/memo-reader-session-delete` 커밋 시점의 코드로 시작했다. 대화 기록은 RA 모드 기본값 `data/runs/ra_conversations`. 질문 보고서·검토 기록·대화 기록 폴더와 PostgreSQL을 **함께 쓴다**.
- `127.0.0.1`에만 바인딩된다. 로그인·권한 없음. RAG 질문은 프로세스마다 한 번에 한 건씩 처리한다.
- 시작 때 BGE-M3·리랭커·BM25를 예열한다(약 26~34초, 모델 API 호출 0).
- **화면에 별도 원본 검토 페이지 링크가 없다**(10-16 제거). 원문 확인은 오른쪽 근거 패널과 메모 상세 창에서 하며 원본 PDF·기관 출처 링크는 남아 있다. 별도 검토 화면(`review_viewer`)과 `--review-url` 인자는 10-21 정리에서 코드에서도 제거했다.
- Python 코드·환경 변수(`.env`)는 재시작해야 반영된다. `rag_service/web/` 화면 파일은 요청 때마다 읽으므로 새로고침으로 반영된다.

새로 띄울 때(포트는 Windows 예약 범위 `netsh interface ipv4 show excludedportrange protocol=tcp`를 피한다):

```powershell
.\scripts\start_ra_service.ps1 -Port <포트>   # 릴리스 생략 시 versions.json의 served_by_ra_service(04)
```

시작 스크립트는 같은 포트의 다른 프로세스를 멈추지 않고, 이미 같은 버전·모델·릴리스가 떠 있으면 그대로 둔다.

## 2. 화면 구성 (`rag_service/web/`)

| 영역 | 내용 | 파일 |
| :-- | :-- | :-- |
| 헤더 | `+ 파이프라인`(이름·원성분만 입력), `자료 수집·적재` 창 열기 | `index.html`, `app.js` |
| 왼쪽 `자료·검색 범위` | 성분 → 제품 → 문서 체크, 기관, 공통 지침, 제품×기관 현황, 보조 문서 포함 옵션. 접이식, 선택 요약 칩 | `inventory.js` |
| 가운데 위 | 개발 파이프라인 단계 카드 7개 + 선택 단계 메모(직접/RAG), 태그 필터, 빈 단계의 메모 추가 카드. RAG 메모 카드는 원 질문을, 직접 메모 카드는 첫 줄을 보여 주고 누르면 메모 상세 창이 열린다 | `pipeline-notes.js` |
| 가운데 아래 | 채팅(멀티턴), 인용 번호 버튼, 인용된 표·그림 카드, 처리 중 표시. 대화 이어가기 안내는 입력창 안에 있다 | `app.js`, `common.js` |
| 오른쪽 | 근거 원문: 페이지 이미지·근거 영역 강조·crop·PDF | `viewer.js`, `evidence-view.js` |
| 메모 상세 창 | 최대 900px. 원 질문·메모 전문·출처·태그. 원문을 누르면 데스크톱은 왼쪽 메모·오른쪽 근거를 나란히, 좁은 화면은 `메모 / 근거 원문` 전환. 오른쪽 근거 패널을 복제하지 않고 옮겨 오며, 창을 닫으면 메인 오른쪽으로 돌려놓는다 | `pipeline-notes.js`, `viewer.js` |
| 질문 검토 | 답변별 검토 상태·의견·미팅 질문 수정, append-only 이력, Markdown 내보내기 | `review-notes.js` |
| 자료 수집 창 | [01 수집·적재](01_COLLECTION_INGESTION.md) 3.2절 | `data-manager.js`, `data-manager.css` |

이전 화면 `rag_service/static/`은 10-21 정리에서 삭제했다. 디자인은 버건디·화이트·웜그레이, 본문 16px, 답변·입력 최대 900px.

## 3. 개발 파이프라인과 메모

| 항목 | 현재 |
| :-- | :-- |
| 단계 (ID · 표시명) | `bridging` 개발 전략·가교 검토 → `cmc` 품질·분석적 유사성 → `nonclinical` 비임상 → `phase1` 임상 1상 · PK/PD → `phase2` 임상 2상 · 설계·생략 검토 → `phase3` 임상 3상 · 비교 임상 → `approval` 허가 심사·적응증 외삽 |
| 저장 | PostgreSQL `app` 스키마(`app.workspace_projects`, `app.memo_documents`, 마이그레이션 `008_workspace_memos.sql`). `/api/projects` 응답 `storage: postgres` |
| 지금 데이터 | 파이프라인 1개("ustekinumab RA 검토", 원성분 ustekinumab), 메모 3개 |
| 규칙 | 파이프라인 생성·전환·단계 선택은 검색 범위를 바꾸지 않는다. 비교 제품은 왼쪽 범위에서 고른다. RAG 메모는 저장 당시 질문과 근거 위치를 보존하고 참고 제품은 근거 문서에서 자동 기록한다. 수정은 revision 충돌 검사와 이력으로 남는다 |
| 내부 문서·업무 초안 | 파이프라인에 사내·협력사 문서 묶음을 등록(`internal_document_bundle_v1`). 기준일을 고르면 그날까지의 문서, 업무별 상태판(요청·합의·일정·보고서·QA·승인·제출·기관 판단), 이슈를 본다. 이슈가 있으면 '이슈 대응' 탭에서 AI가 원인 가설과 검색 질문을 만들고, 공개 자료 RAG 검색(1차 + 결과를 본 추가 질문 0~2개, 검색 제외 제품 지정 가능) 뒤 대응 가이드(사실만 쓴 상황 정리, 가능성 순 원인 후보와 각 지지·반대 근거·확인 시험, 실험 설계·기관 대응·예상 질의·다른 제품 사례 탭의 한 줄 항목, 공개 근거 칩)로 정리하며, 정답이 등록된 시나리오는 정답 대조(어휘 1차 분류)를 함께 본다. 단계 카드 아래 한 줄에 업무·이슈 수가 나온다. 데모: [v5 이슈 대응](../demo/issue_guide_v5/README.md), [v4 업무 현황](../demo/internal_pipeline_v4/README.md) |
| 없는 것 | 내부 PDF·메일 원본에서 문서 묶음을 만드는 추출, 협력사·담당자·기한을 갖는 독립 이슈 모델, 다중 사용자 권한 |

단계는 메모 분류이며 필수 시험 목록이나 자동 검색 필터가 아니다. 단계 ID를 유지해 과거 메모·태그·이력은 그대로 읽힌다.

## 4. API

**HTTP 계층:** FastAPI(`rag_service/app.py`) 하나다. 10-21 정리에서 표준 라이브러리 서버(`__main__.py`의 `handler`)와 `--server`·시작 스크립트 `-Server`를 없앴고, 테스트도 모두 FastAPI 앱을 상대로 돈다. 아래는 10-18 전환 기록이다. 전환 당시 `--server fastapi|stdlib`(`rag_service/app.py` 대 `rag_service/__main__.py`의 `handler`). 두 구현은 같은 URL·JSON·상태 코드·보안 헤더를 내며(`rag_service/http_rules.py` 공통 규칙, `tests/test_http_contract.py`가 둘에 같은 검사를 돌린다) 화면은 어느 쪽이든 그대로 쓴다. 실제 데이터 16개 요청을 두 서버에 보내 상태·헤더·본문이 같음을 확인했다(PDF·PNG 포함). 차이는 둘: 예상 못 한 예외에 FastAPI는 500 `{"error"}`(기존은 응답 없이 연결 종료), 정의하지 않은 메서드(PUT 등)에 405(기존 501). 시작 스크립트 `-Server fastapi|stdlib`(기본 **fastapi**, `python -m rag_service`의 `--server`는 기본 stdlib). 실제 질문 한 건(Health Canada Steqeyma 보관 조건)을 8885에서 돌려 접수 202 → 진행 단계 → 24초 만에 완료, 동시 접수 409, 결과 화면(`#job=`) 렌더링까지 확인했고 서버 로그에 처리되지 않은 오류가 없었다. 두 서비스 모두 FastAPI로 떠 있다(10-18 15:09·15:10 재시작부터, 실행 인자 `--server fastapi`로 확인).

**API 명세:** FastAPI 서버에서 `/api-docs`(화면, 외부 CDN 없음)와 `/openapi.json`. 저장본 `docs/project/api/openapi_2026-10-18.json`. 전환 계획·단계: [FASTAPI_MIGRATION_PLAN](../project/FASTAPI_MIGRATION_PLAN_2026-10-18.md).

| 경로 | 용도 |
| :-- | :-- |
| `GET /api/summary` | 코드 버전·릴리스·모델·전략·문서 목록 (답변 생성 없음) |
| `GET /api/inventory` | 왼쪽 범위 선택용 문서·제품·기관 목록 |
| `POST /api/questions` | 질문 접수 (LLM 호출 발생) |
| `GET /api/jobs`, `GET /api/jobs/<id>` | 질문 목록, 상태·결과 (`#job=<id>`로 재시작 후에도 복원) |
| `GET/POST /api/jobs/<id>/review` | 질문 검토 기록 조회·추가 |
| `GET /api/jobs/<id>/evidence/<결과>/<근거>[/page/<n>|/crop|/document.pdf]` | 근거 원문 (클라이언트는 파일 경로를 보내지 않음) |
| `GET /api/conversations/<id>` | 대화 기록 |
| `/api/projects[/<id>[/memos[/<memo>/revisions]]]` | 파이프라인·메모 |
| `GET/POST /api/projects/<id>/documents` | 내부 문서 묶음 조회·등록(버전 불변) |
| `GET /api/projects/<id>/workboard/<YYYY-MM-DD>` | 기준일의 업무 상태(원문 이벤트에서 계산)·문서 |
| `GET/POST /api/projects/<id>/work-drafts[/<draft>]` | 업무 초안 목록·작성(모델 1회)·조회 (화면 탭은 '이슈 대응'으로 바뀜, API·기록은 유지) |
| `GET /api/projects/<id>/issues` | 등록한 이슈(정답 제외) |
| `GET/POST /api/projects/<id>/issue-guides[/<guide>]` | 이슈 대응 가이드 목록·작성(모델 3회: 검색 계획·추가 질문·정리 + 공개 자료 검색 4~7회)·조회 |
| `POST /api/projects/<id>/issue-guides/<guide>/delete` | 이슈 대응 가이드 삭제(만드는 중이면 거절, 검색 답변 기록은 남음) |
| `/api/ingestion[/sessions…|/requests…]` | 자료 수집 창 (`POST /api/ingestion/sessions/<id>/delete` 대화 삭제 포함) |

## 5. 기록 저장 위치

| 위치 | 내용 |
| :-- | :-- |
| `data/runs/ra_service_reports/` | 완료·실패 질문 보고서 JSON |
| `data/runs/ra_review_records/` | 질문 검토 상태·의견·수정 이력 |
| `data/runs/ra_conversations/` | 대화·후속 질문 문맥 |
| `data/runs/ra_work_drafts/` | 업무 초안 기록(입력 단위·상태·답변·검사)과 호출 기록 |
| `data/runs/ra_issue_guides/` | 이슈 가이드 기록(검색 범위·질문별 답변·인용 쪽·가이드·검사·정답 대조)과 호출 기록 |
| `data/runs/ra_pipeline_requests/` | 제공자 요청·응답 기록 |
| `data/runs/ra_service_logs/` | 시작 스크립트로 띄운 프로세스의 stdout·stderr·process.json |
| `data/runs/model_quota/` | 이 프로젝트의 API 호출·사용량 일별 장부 |
| PostgreSQL `app`, `ingest` | 파이프라인·메모, 수집 세션·요청 |

## 6. 원 기록

- [rag_service/README.md](../../rag_service/README.md) — 맨 위 날짜별 화면 변경 기록(최신이 위). 하단 `실행`·`검색과 답변`·`API 성능평가` 절은 10-12 이전 상태
- [RA 워크스페이스 구현 검토](../project/RA_WORKSPACE_IMPLEMENTATION_PLAN_2026-10-15.md) — 맨 위 "최신 사용자 결정 및 구현 상태" 우선
- [3분할 화면·RA 기능 계획](../rag/UI_3PANE_AND_RA_FEATURES_PLAN_2026-10-13.md)
