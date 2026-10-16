# RA 파이프라인·메모·협력사 이슈·자료 적재 창 구현 검토

- 작성: 2026-10-15 15:10 KST
- 최종 수정: 2026-10-15 19:36 KST (일반 개발·허가 흐름과 전 단계 공통 품질·제조 업무 반영)
- 검토 기준: `project_shared`, HEAD `ff4d9a3`. 계획 제안이며 아래 신규 기능을 구현했다는 뜻은 아니다.
- 제품 방향: 사용자가 선호한 기존 버건디·웜그레이 3단 화면을 유지하고, 중앙 파이프라인의 노드 아래에 RAG/직접 메모를 축적한다. 협력사와 쟁점은 별도 이슈 화면 없이 메모 태그로 정리한다.

**최신 사용자 결정 및 구현 상태(아래 초기 검토안보다 우선)**

- 후속 디자인 피드백으로 메인 RA를 번호형 단계 카드와 정렬된 빈 메모 추가 카드로 변경했다. 안내문·단계 제목의 시작선을 일치시키고, 답변/입력 900px·선택 메모 880px로 읽기 너비를 제한했다. 16px 본문과 기존 단계/기능/데이터는 유지한다. 실제 저장 답변·데스크톱·모바일을 렌더링해 정렬과 잘림을 확인했다.

- 오른쪽 이전 단계 선택창은 제거하고 `개발 전략·가교 검토 → 품질·분석 → 비임상 → 임상 1상 → 임상 2상 → 임상 3상 → 허가·외삽`을 하나의 파이프라인으로 복원했다. 선택 표시와 메모 단계 ID가 같으며, 전체 보기의 단계/메모 열은 같은 순서·폭·스크롤을 공유한다. 자료 수집 CSS는 메인 CSS와 별도 파일로 분리했다. 실제 독립 Chrome 렌더링으로 데스크톱 열 정렬과 모바일 화면을 검증했다. 아래 이전 단계 드롭다운 제안은 이 수정으로 대체된다.

- 자료 수집 페이지 예시 디자인도 메인 RA의 실제 `자료 수집·적재` 운영창에 적용했다. 요청 검색·제품별 상태 카드·4단계 진행·기관별 결과 표·문서 목록/출처 필터·원본 확인표/기록을 기존 에이전트 API에 연결했다. 승인 동작은 확인표를 연 세션의 최신 해시 검사와 별도 승인 버튼을 유지한다. 실제 대기 요청을 GET만으로 조회하여 19문서·3기관·1임상시험 표시를 검증했고 수집·등록은 실행하지 않았다.

- 후속 화면 피드백을 반영하여 파이프라인은 `품질·분석 → 비임상 → 임상 1상 → 임상 2상 → 임상 3상 → 허가·외삽`의 프로젝트 자료 검토 흐름으로 수정했다. 아래 일반 개발 흐름 제안은 대체되었다. 전 단계 공통 가로줄은 제거했고, 기존 개발 전략·가교 메모 등은 `이전 단계 메모` 선택기로 보존한다. 중앙 답변·메모를 16px로 키우고 헤더·메뉴·여백을 줄였다.

- 파이프라인은 회사에서 개발하는 제품이다. 생성 항목은 파이프라인 이름(사내 개발 코드·제품명)과 원성분 두 개이며, 기존 승인 제품을 선택하는 체크박스는 제거했다.
- 비교·참고 제품 및 기관은 왼쪽 검색 범위에서 선택한다. 파이프라인을 만들거나 전환해도 검색 선택을 바꾸지 않으며, 파이프라인에 비교 제품을 소속시키지 않는다.
- 단계별 직접/RAG 메모, 태그 필터, 수정 이력·보관, 원문 연결, 접이식 검색 선택, 자료 수집·적재 창을 실제 서비스에 연결했다. 협력사·상태·기한을 갖는 독립 Issue/Partner 모델은 구현 범위에서 제외했다.
- 파이프라인과 메모는 카탈로그와 독립된 PostgreSQL `app` 스키마에 저장한다. 과거 프로젝트 제품 필드는 읽을 때 제외하고, 기존 ID·메모·이력·인용은 보존한다. RAG 메모의 참고 제품은 서버가 근거 문서에서 자동 기록하므로 과거 제품 목록에 제한받지 않는다.
- 단계는 `개발 전략·기관 협의 → 비임상 → 임상시험 신청·개시 준비 → 임상 1상 → 임상 2상 → 임상 3상 → 허가 신청·심사 → 허가 후 관리`의 일반 흐름으로 정리했다. 품질·제조(CMC)는 전 단계 공통 업무로 분리했고, CTD 모듈·절 표기는 모두 제거했다. 제품·허가 경로별 임상 단계 통합·생략 가능성을 안내한다. 기존 단계 ID를 유지하고 읽기 시 단계 목록만 갱신하여 메모·수정 이력은 보존한다. 예전 가교·생략 근거 메모는 개발 전략·기관 협의 단계에 남는다.
- 이후 내용은 **15:12 시점의 초기 검토 기록**이다. 별도 협력사 이슈 모델·프로젝트 제품/기관 기본값 제안과 ‘아직 구현 전’이라는 설명은 위 결정으로 대체되었다.

**기존 검색·대화·원문 확인을 재사용하고, 프로젝트 업무 데이터와 적재 운영 API를 추가하는 것이 적절하다.** 전면 재작성이나 RAG 엔진 교체가 선행 조건은 아니다. 다만 앞서 대화에서 만든 파이프라인 화면은 서비스에 연결되지 않은 설계 예시다.

검토 시점에 `GET http://127.0.0.1:8775/api/summary`와 `:8885/api/summary`는 모두 `ra-research-rag-4.10.1`, `product_index_chunks=15682`, `gpt-5.6-luna`를 반환했다. 설정 파일의 서비스 역할도 릴리스 04를 가리킨다. 신규 질문·수집·전처리·모델 호출·서비스 재시작은 하지 않았다. DB 직접 검색 여부는 summary 응답만으로 확인하지 않았다.

**현재 구현과 새 작업의 경계는 다음과 같다.**

| 기능 | 실제 현재 구현 | 필요한 작업 | 확인 위치 |
|---|---|---|---|
| 실제 서비스 화면 | 자료 범위 / 채팅 / 원문 3단 구성 | 기존 스타일 위에 단계 도크와 메모 추가 | `rag_service/web/index.html:18`, `rag_service/__main__.py:18-21` |
| 좌측 선택 | 성분→제품→문서, 기관, 공통 지침, 제품×기관 현황. 문서 목록은 이미 접힘 지원 | 제품·기관 선택을 접이식으로 변경, 선택 요약·프로젝트 기본값·복원 추가 | `rag_service/web/inventory.js:61`, `rag_service/inventory.py:25` |
| 질문·진행·이력 | 질문 접수, job 상태 폴링, 완료/실패 보고서 파일 보관·복원, 질문 목록 | 프로젝트 및 저장 위치 연결 메타데이터 추가. 검색 범위와 단계 선택은 구분 | `rag_service/__main__.py:26,74,91,149` |
| 멀티턴 | 대화와 턴, job 연결, 후속 질문 맥락 저장 | 프로젝트별 대화 연결과 복원 동선 | `rag_service/conversations.py:18` |
| 원문 보기 | 페이지 이미지·crop·PDF, 영역 좌표 또는 인용문 하이라이트 | 저장 메모에서도 호출. 과거 릴리스 문서 버전 열기 보강 | `rag_service/pages.py:65-116`, `rag_service/web/viewer.js:7` |
| RAG 생성 메모 | 검증된 주장·근거·미확인 항목·미팅 질문을 보고서 안에 생성 | 독립 메모로 저장하는 반영 기능 | `rag/ra_memo.py:10-69` |
| 사용자 검토 | 완료 답변 전체의 검토 상태·의견·미팅 질문 수정, append-only 이력·revision 충돌 검사 | 단계 메모와 업무 이슈는 별도 모델로 추가하고 기존 검토와 연결 | `rag_service/reviews.py:32-80`, `rag_service/web/review-notes.js:29` |
| RA 개발 파이프라인 | 실제 서비스에는 개발 단계 모델·노드·메모 보드 없음 | 프로젝트별 단계 정의·순서·표시·메모 연결 UI/API/DB | `rag_service/web/index.html`, `work/ra-original-style-refined.html` 대조 |
| 협력사 이슈 | 제품 제조사 문자열은 있지만 협력 관계·담당자·요청/회신 이력 없음 | 협력사, 이슈, 담당자, 기한, 다음 조치, 이벤트 이력 | `ingestion/db/migrations/001_base.sql:20`, `rag/ra_memo.py:38` |
| 적재 요청 에이전트 | CLI에서 제품 식별·현황·계획·스테이징·확인표·승인·반영 | 세션/요청 API와 운영창 연결 | `ingestion/agent.py:123-378`, `ingestion/worker.py:43-106` |
| DB·릴리스 | PostgreSQL 카탈로그/레지스트리/적재 기록, RAG 청크·벡터 적재·검증 | 신규 업무 스키마. 필요 시 전처리→릴리스→서비스 적용 조정기 | `storage/rag_loader.py:88,160,239`, `ingestion/db/migrations/001-007` |

실제 화면 수정 대상은 `rag_service/web/`이다. `rag_service/static/`는 이전 화면 보존본이며 동시에 고칠 대상이 아니다. `storage/HANDOFF.md`와 실제 코드가 루트 README의 초기 설명보다 최신이다. 초기 README의 “storage는 자리만 있음” 또는 “서비스 색인은 01” 설명을 현재 상태로 사용하면 안 된다.

**같은 이름을 가진 기존 개념과 신규 업무 개념을 구분해야 한다.**

- `rag/ra_pipeline.py`의 pipeline은 질문 계획→검색→생성→검토 실행 과정이다. 화면의 CMC→비임상→임상→허가 개발 단계 모델과 다르다.
- `report.review_memo.issues`의 `T1/T2`는 질문 내 조사 항목이다. `complete/partial/missing`은 답변 충족도이며 업무 이슈의 해결 상태가 아니다.
- `ReviewStore`는 원래 보고서 해시에 묶인 질문 전체 검토 기록이다. 프로젝트·단계에 직접 다는 일반 메모로 바꾸지 않는다. revision 검사·원본 보존 패턴을 재사용한다.
- `registry.products.company`는 제품의 회사 메타데이터다. 실제 협업 관계를 표현할 `ProjectPartner`와 구분한다.

**새 업무 모델은 프로젝트·단계·메모·근거·이슈·협력사·이력으로 구성한다.** 아래 이름은 제안이며 현재 코드에는 없다.

| 신규 모델 | 핵심 내용 |
|---|---|
| Project / ProjectProduct | 프로젝트 ID, 성분, 주 제품·비교 제품, 기관 기본값. 같은 제품을 사용하는 서로 다른 프로젝트를 구별 |
| StageDefinition / ProjectStage | 단계 키·표시명·순서·적용 여부. 시험 구성과 생략 판단을 프로젝트별로 표현 |
| Memo / MemoRevision | 전역 ID, 프로젝트, RAG/직접 작성 구분, 원본 내용·편집본, 작성자·작성일·수정 이력·revision |
| MemoStageLink | 하나의 메모를 여러 단계에 연결. 내용 복사로 연결하지 않음 |
| MemoEvidence | 원 질문과 주장, 복수 인용, 문서 버전·쪽·원문 위치·릴리스 연결 |
| Issue / IssueStageLink | 전역 ID, 원 메모, 관련 단계, 업무 상태, 담당자·협력사·기한·다음 조치·해결 판단 |
| Partner / ProjectPartner | 협력사와 해당 프로젝트에서의 역할 |
| IssueEvent | 요청·회신·검토·추가 요청·해결·재개의 작성자·시각·내용과 근거. 첨부는 후속 확장 가능 |

업무 상태는 `등록 → 요청 준비 → 협력사 회신대기 → 내부 검토 → 추가 요청 / 해결`을 시작점으로 삼는다. 답변을 찾았거나 회신을 받았다는 이유만으로 해결 처리하지 않는다. 같은 이슈를 여러 단계에서 보여주고, 협력사별 목록도 같은 이슈를 조회한다.

DB 코드는 `storage.postgres`를 진입점으로 사용하고, 스키마는 기존 `ingestion/db/migrations/`의 다음 미사용 번호에 추가한다(검토 시점에는 008부터). 적용된 001~007을 수정하지 않는다. 업무 데이터는 기존 `registry/corpus/rag/ingest`와 구분한 `app` 등의 스키마에 두는 것이 적절하다.

중요한 선행 제약이 있다. `ingestion/load_catalog.py:23-24,91-105`는 `registry.products`, `registry.ingredients`, `corpus.documents`, `corpus.document_versions` 등을 TRUNCATE 후 재적재한다. 신규 업무 테이블이 이 테이블을 무조건 FK로 참조하면 재적재가 막히며, CASCADE로 해결하면 업무 데이터가 손실될 수 있다. 영구 식별 레지스트리를 별도로 두거나 카탈로그 재적재를 upsert 방식으로 바꾸는 전략을 먼저 정해야 한다. 업무 기록을 카탈로그 갱신과 함께 삭제하지 않는다.

**RAG 메모 반영은 기존 답변의 검증된 주장과 원문 연결을 보존하는 작업부터 시작한다.** 첫 버전에서 별도 LLM 재요약을 반드시 추가할 필요는 없다. 기존 주장 선택→단계 선택→사용자 수정→저장을 지원하고, AI 단계 추천은 수정 가능한 보조 기능으로 확장할 수 있다.

- `E29`, `C1`, `T1`은 보고서 내부 ID다. `job_id + result_index + claim_id/evidence_id` 등과 함께 저장한다.
- `results.evidence`의 `document_id/version_id/chunk_id/pdf_page/pdf_page_label/source_refs/source_spans`와 원 `claims.sources`의 `quote/citation_span_id/source_range/offset_basis/unit_id/representation_range`를 가용한 범위에서 보존한다. `source_range`는 PDF 전체 문자 좌표가 아니다.
- `rag/ra_memo.py:26-35`의 간단한 근거 복사본만 쓰면 일부 위치 필드가 빠진다. 원 claim과 evidence에서 저장 패킷을 구성한다.
- 기존 원문 API는 `/api/jobs/{job_id}/evidence/{result_index}/{evidence_id}`와 `/page/{n}`, `/crop`, `/document.pdf`다. 이미지 자체를 메모 DB에 중복 저장할 필요는 없다.
- 현재 `EvidencePages`는 활성 bundle와 지침 snapshot의 문서 경로만 구성한다. 과거 문서 버전 조회 resolver와 원본 보존 정책을 추가해야 릴리스 변경 후에도 메모에서 원문을 열 수 있다.
- 현재 보고서의 `index.directory/chunks_sha256`와 DB `rag.releases`를 연결해 명시적인 릴리스 식별을 남긴다. 클라이언트가 임의 파일 경로를 지정하게 하지 않는다.
- 중복 반영 방지 키, 저장 실패 표시, `expected_revision` 충돌 검사, 새로고침·재시작 복원을 완료 조건에 포함한다.

**화면은 기존 3단 구조를 유지하며 기능을 덧붙인다.**

1. 왼쪽은 기존 inventory API·선택 규칙을 재사용하고 제품·기관 목록을 접는다. 닫혀도 선택 칩과 검색 범위는 보인다. 기존 문서 선택과 보조 문서 포함·지침 포함 옵션도 유지한다.
2. 중앙 상단은 파이프라인, 선택 노드 아래는 메모·이슈, 하단은 기존 채팅으로 구성한다. 전체 보드 모드에서도 우측 원문 패널을 재사용한다.
3. 메모는 자유롭게 작성하고, 대응이 필요할 때 이슈로 전환하며 담당자·협력사·상태 등을 입력한다. 모든 메모에 이슈 필드를 강제하지 않는다.
4. 보고 있는 단계, 실제 RAG 검색 범위, 메모 저장 위치를 별도 상태로 관리한다. 단계 클릭만으로 제품·기관·문서 검색 조건이 조용히 바뀌지 않게 한다.
5. 협력사·상태 필터는 업무 이슈에 적용한다. 검색 제품·기관 필터와 구분한다.
6. 제품 값은 기존 카탈로그 ID를 사용한다. 예시 화면의 `BMA1200`과 실제 `BMAB1200`의 차이도 정리한다. 기관 표시명과 내부 `EU/US/CA` 값도 기존 매핑을 재사용한다.

신규 파일의 예시는 `rag_service/workspaces.py`, `rag_service/issues.py`, `rag_service/web/pipeline.js`, `rag_service/web/issue-board.js`다. 현재 `__main__.py`에 붙이는 라우팅은 작게 유지하고 저장·검증 로직은 모듈로 분리한다.

신규 API의 최소 제안은 다음과 같다. 이 경로들은 아직 구현되지 않았다.

```text
GET/POST /api/projects
GET      /api/projects/{project_id}/pipeline
GET/POST /api/projects/{project_id}/memos
POST     /api/memos/{memo_id}/revisions
POST     /api/memos/{memo_id}/issue
GET      /api/projects/{project_id}/issues
POST     /api/issues/{issue_id}/events
```

업무 이력은 첫 버전부터 서버/DB에 저장한다. 외부 협력사가 직접 접속하는 포털은 별도 범위다. 현재 서버는 localhost 전용이며 사용자 로그인·프로젝트 권한이 없고 RAG 질문은 서버 전체에서 한 건씩 처리한다(`rag_service/__main__.py:44,101,184,283`). 우선 내부 RA 담당자가 회사별 이슈를 기록하는 범위로 구현하고, 직접 공동 접속이 필요하면 인증·프로젝트 참여·조직별 열람·작업 큐를 추가해야 한다.

**storage 에이전트 창은 기존 적재 요청 에이전트를 웹으로 연결하는 형태가 적절하다.** 현재 대화형 본체는 `ingestion.agent.Agent`이며 `storage/`는 DB·릴리스 도구다. 창의 사용자용 이름은 역할이 드러나는 “자료 수집·적재 관리”를 권한다.

기존 좌측 “자료·검색 범위” 제목 옆이나 상단에 `자료 관리` 버튼을 두고, 넓은 별도 창/화면으로 연다. 파이프라인 작업 공간에 상시 네 번째 패널을 추가하지 않는다.

| 운영창 영역 | 재사용할 것 | 추가 구현 |
|---|---|---|
| 요청 대화·세션 | `Agent.handle`, DB sessions/turns/model_calls, `--session` 복원 | 세션 생성·목록·이력·턴 API, 세션별 순서 보장 |
| 제품 식별·수집 계획 | 이름 사전·허가/수집 현황·계획 상태 | 텍스트 답변과 별도로 후보·계획을 구조화해서 표시 |
| 확인표·승인 | 확인표 SHA-256, 본 세션에서 본 확인표와 최신 상태 검사, 반영 직전 재검사 | 확인표 열람과 해시를 포함하는 승인 API·버튼 |
| 작업 현황·실패 상세 | 요청 상태·request_events·작업 폴더 로그 | 요청 목록·상세·실패 단계·로그 조회·세션 재개 |
| 검색 반영 상태 | 릴리스 목록·DB 적재/검증·서비스 요약 | 수집·카탈로그·전처리·임베딩·DB·실제 서비스 반영 상태를 각각 연결 |

현재 연결 범위는 다음과 같다.

```text
기존 에이전트:
요청 → 이름 확인 → 계획 → 스테이징 수집 → 확인표 → 승인
     → 프로젝트 자료 반영 → 카탈로그 DB/이름 사전 갱신 → done

별도 도구로 존재하며 통합 연결이 필요한 뒤 단계:
H 전처리 → 청킹·임베딩 → 새 릴리스 검증 → DB 적재·검증
         → 서비스 적용 → 현재 서비스의 검색 범위·원문 확인
```

`done`은 수집·카탈로그 갱신 완료다. 검색 가능 완료가 아니다(`ingestion/worker.py:80-106`). 전처리 실행기는 `pipelines/pipeline_h/run.py`, DB 릴리스 적재기는 `storage/rag_loader.py`에 있지만 이를 적재 요청과 끝까지 연결하는 조정기는 없다. 초기 창은 기존 수집 범위를 정확하게 노출하고, 전체 연결은 후속 단계로 구현한다. 자동 적용되지 않는 단계는 미연결/대기 사유를 표시한다.

최소 운영 API 제안:

```text
GET/POST /api/ingestion/sessions
GET      /api/ingestion/sessions/{session_id}
POST     /api/ingestion/sessions/{session_id}/turns
GET      /api/ingestion/requests
GET      /api/ingestion/requests/{request_id}
GET      /api/ingestion/requests/{request_id}/events
GET      /api/ingestion/requests/{request_id}/logs
POST     /api/ingestion/requests/{request_id}/approve
GET      /api/storage/releases
GET      /api/storage/readiness
```

서버 어댑터의 예시는 `rag_service/ingestion_api.py`, 화면은 `rag_service/web/data-manager.js`다. 기존 `Agent`·worker·계약 검증을 호출하고 범용 셸/터미널 실행 API를 만들지 않는다. RAG 질문 대화와 적재 에이전트 대화는 세션·동작 범위를 구별한다.

운영창의 버튼은 실제 지원 범위에 맞춰야 한다.

- 자연어 “취소”는 대화 상태만 초기화하며 실행 중 worker를 멈추지 않는다(`ingestion/agent.py:175-177`). 실제 작업 취소는 PID/heartbeat·취소 요청·안전한 중단 지점 구현 후 노출한다.
- worker의 subprocess 로그는 종료 뒤 기록한다(`ingestion/worker.py:31-35`). 첫 버전은 거친 단계 상태를 표시하고, 문서별 진행·실시간 로그는 이벤트/스트리밍 보강 후 제공한다. 임의 퍼센트를 만들지 않는다.
- spawn은 PID를 DB에 남기지 않는다(`ingestion/agent.py:371-378`). 세션 턴 직렬화·중복 시작 방지·실행 소유자·장애 감지·실패 단계 재개를 추가한다. 첫 연결은 동시 적재를 제한한다.
- “다시 수집”과 “지침 추가”는 현재 실행 대신 수동 명령을 안내하는 분기다(`ingestion/agent.py:232-241`). 신규 제품 적재와 같은 수준으로 지원한다고 표시하지 않는다.
- 계획의 수집 관할은 제품에 존재하는 EU/US/CA 이름에서 정한다(`ingestion/plan.py:67-71`). 화면에서 기관을 선택해 실제 수집을 제한하려면 backend 계획 생성도 변경해야 한다.
- DB에 `rag.active_release`가 있어도 검색 서비스는 시작 때 적재한 bundle/벡터를 계속 사용할 수 있다. 버전 포인터 변경과 실제 서비스 적용을 별도 확인한다. 현재 DB 모드도 벡터를 DB에서 읽은 후 메모리 검색하는 경로다(`rag/search.py:48-56`, `storage/rag_index.py:60`). pgvector 직접 질의 전환은 이 화면의 선행 조건이 아니다.

**권장 구현 순서는 작은 화면 변경과 업무 데이터 구축을 먼저 진행하고, 적재 창은 독립적으로 병행하는 것이다.**

| 순서 | 작업 묶음 | 완료 기준 |
|---|---|---|
| 1 | 좌측 제품·기관 접기, 선택 요약, 실제 UI 정리 | 같은 선택이 기존 질문 payload에 그대로 전달됨. 문서/지침/보조자료 선택 회귀 없음 |
| 2 | 프로젝트·단계·메모 DB/API + 과거 문서 버전 resolver | 직접 메모를 단계에 저장·수정하고 새로고침·서버 재시작 후 복원. 기존 버전 근거도 열림 |
| 3 | 답변→메모 반영 + 복수 근거·복수 단계 | 검증된 원 주장과 문서 버전·위치 보존, 중복 저장 방지, 원문 연결 |
| 4 | 메모→이슈·협력사·요청/회신/해결 이력 | 같은 이슈가 단계별/회사별로 일관되게 보임. 회신 후 내부 검토와 해결을 구별 |
| 병행 A | 적재 에이전트 최소 운영창 | 세션 복원→제품 확인→계획→스테이징→확인표→승인→카탈로그 완료를 실제 코드와 연결. 오류·미지원 기능을 정확하게 표시 |
| 후속 B | 적재→전처리→임베딩→릴리스→서비스 조정기 | 새 자료가 실제 검색·원문 보기까지 반영됨. 실패 단계 재개와 릴리스 불변성 확인 |
| 후속 C | 직접 공동 접속·고급 운영 | 로그인/프로젝트 권한, 동시 편집, 작업 큐, 실제 취소·재시도, 첨부·알림 등 필요한 범위를 선택 |

1~4의 첫 검증 시나리오는 “프로젝트 열기 → 단계 아래 직접 메모 → 기존 답변 근거 반영 → 원문 열기 → 이슈 전환 → 협력사 요청/회신/검토 → 재접속 복원”이다. 병행 A는 별도 사본에서 확인표 승인부터 실제 자료·DB 반영까지 검증한다. 검토한 에이전트 구현 기록(`docs/collection/INGESTION_AGENT_DB_PLAN_2026-10-14.md:493`)에서는 실제 promote 실행 검증을 하지 않았다고 구분한다. 이번 점검에서도 실행하지 않았으므로 코드 존재만으로 운영 완료라 보지 않는다.

**이번 검증은 기존 기반의 확인이며 신규 기능의 완료 검증은 아니다.** 다음 기존 테스트를 임시 fixture/로컬 시험 서버로 실행해 총 17개 통과했다. 수집·전처리·모델 호출과 실제 브라우저 검증은 수행하지 않았다.

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests -p "test_rag_service*.py"   # 7개 통과
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests -p "test_ra_review_store.py" # 4개 통과
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests -p "test_followup.py"        # 6개 통과
```

재사용할 추가 회귀 자료는 `tests/test_ra_memo.py`, `tests/test_ra_service_archive.py`, `tests/test_ra_spans.py`, `tests/test_ingestion_agent.py`다. 신규 구현 시에는 단계 메모 영속성, 릴리스 전환 후 원문 열기, 중복 pin, revision 충돌, 동일 이슈 다중 단계 연결, 카탈로그 재적재 후 업무 기록 보존, 적재 승인 해시 변경 거부를 각각 검증한다.

주요 근거: `rag_service/__main__.py`, `rag_service/web/`, `rag_service/pages.py`, `rag_service/reviews.py`, `rag/ra_memo.py`, `rag/ra_spans.py`, `ingestion/agent.py`, `ingestion/worker.py`, `ingestion/plan.py`, `ingestion/load_catalog.py`, `ingestion/db/migrations/`, `storage/HANDOFF.md`, `storage/rag_loader.py`, `data/versions/versions.json`.
