# v4: 내부 자료·업무 상태·업무 초안 (앱 기능)

**설명용으로는 [00 한 장 요약](00_OVERVIEW.md), 과정은 [01 리뷰](01_REVIEW_WALKTHROUGH.md)를 읽는다.** v2·v3의 격리 실행기를 앱 기능으로 옮기고, 문서 본문이 정답을 미리 말하지 않는 새 시나리오로 실행·보완했다.

| 파일 | 용도 |
|---|---|
| [00 한 장 요약](00_OVERVIEW.md) | 기능·핵심 아이디어·화면 캡처·시연 시나리오·한계·용어 |
| [01 리뷰](01_REVIEW_WALKTHROUGH.md) | 무엇을 만들었고, 기준일별 결과, 보완 이력, 남은 한계 |
| [02 내부 문서 묶음](02_INTERNAL_DOCUMENTS.json) | 문서 20건·단위 31개·업무 이벤트·기준일 4개. `POST /api/projects/{id}/documents`로 등록하는 형식(`internal_document_bundle_v1`) |
| [03 리뷰 기준·사후 공개](03_REVEAL_AND_REVIEW_CRITERIA.md) | 실제 사례에서 가져온 것과 합성한 것, 기준일별 리뷰 기준. **모델 입력 제외** |
| [04 자동 생성 초안](04_GENERATED_DRAFTS.md) | 최종 코드(6회차)의 모델 출력 원문 T1~T4와 참고용 T2P |
| [05 실행·리뷰 기록](05_RUN_REVIEW.json) | 6회차 22건 + 서비스 검증 2건의 입력 수·토큰·자동 검사·루브릭·리뷰어 판정 |

## 코드

| 파일 | 역할 |
|---|---|
| `rag_service/internal_documents.py` | 문서 묶음 검증·등록(버전 불변, 같은 내용 재등록은 무시), 기준일 조회, 적용 버전 판단, 공유 범위 필터, 상태판, 진행 현황 목록(`work_items`: 상태 하나·요약·다음 할 일·이력·결과·의견) |
| `rag_service/memo_workflow.py` | 원문 이벤트 → 업무별 상태. 날짜 기준, 일정 축, 이전 값 보존. 의견은 상태를 바꾸지 못하고, QA 승인·제출·기관 판단은 해당 기록에서만 나옴 |
| `rag_service/work_assist.py` | 초안 입력 선택(기준일·공유 범위 → 업무별 우선 자료·BM25 → 같은 문서 절·상태 근거), 모델 입력·출력 스키마, 상태 요약, 자동 검사 |
| `rag_service/work_drafts.py` | 초안 실행(1회 호출·동시 1건·재시도 없음)과 기록(`data/runs/ra_work_drafts/`) |
| `rag_service/web/work-assist.js` | `업무 현황·AI 초안` 대화창(진행 현황·문서·AI 초안) |
| `eval/seed_internal_scenario.py` | 시나리오를 파이프라인으로 등록(`--postgres`면 앱 DB, 기본은 `work/`) |
| `eval/internal_scenario_run.py` | 기준일별 실행과 리뷰용 루브릭(`--live`일 때만 모델 호출) |
| `eval/work_assist_ui_check.py`, `eval/work_assist_dom_check.js` | 화면 DOM 검사(고정 서버·가짜 모델) |

## API

| 경로 | 용도 |
|---|---|
| `GET/POST /api/projects/{id}/documents` | 등록 문서 목록 / 문서 묶음 등록(최대 512 KB) |
| `GET /api/projects/{id}/workboard/{YYYY-MM-DD}` | 기준일의 상태판·문서·숨긴 자료 수·시나리오 기준일 |
| `GET/POST /api/projects/{id}/work-drafts` | 초안 목록 / 작성 접수(`task`, `question`, `as_of`, `audience`) |
| `GET /api/projects/{id}/work-drafts/{draft_id}` | 초안 한 건: 입력 단위·상태·답변·검사 |

## 실행

```powershell
$env:PYTHONPATH='.;tests'; $env:RA_TEST_POSTGRES='1'
.\.venv\Scripts\python.exe -X utf8 -B -m unittest tests.test_internal_work tests.test_memo_workflow tests.test_http_contract tests.test_workspace_postgres
.\.venv\Scripts\python.exe -X utf8 -B eval\work_assist_ui_check.py fastapi
.\.venv\Scripts\python.exe -X utf8 -B -m eval.seed_internal_scenario --postgres          # 앱 DB에 시나리오 파이프라인 추가
.\.venv\Scripts\python.exe -X utf8 -B -m eval.internal_scenario_run --out work/internal_v4/<새 폴더> --live --only T1 T2 T3 T4   # 모델 4회
```

**서비스 반영 상태(2026-10-19 04:15):** 8885는 새 코드로 재시작했다. 8775는 재시작하지 않았다(권한 분류기 거부). 8775는 화면 파일만 새것이어서 `업무 현황·AI 초안` 버튼이 비활성으로 보이고, 기존 기능은 그대로다. 다음 재시작 때 반영된다.
