# 1. 수집·적재 — 현재 상태

- 작성: 2026-10-16 16:50 KST
- 기준: `GET /api/ingestion`(8885), `data/catalog/documents.jsonl`, DB `corpus`·`ingest` 스키마, `ingestion/`·`collection/`·`product_search/` 코드
- 상위: [현재 서빙 상태](README.md)

## 1. 역할과 경계

공식 기관(EMA·FDA·Health Canada·ClinicalTrials.gov·PMDA)의 공개 문서를 받아 `data/raw/`·`data/parsed/`·`data/catalog/`에 쌓는다. **수집·등록이 끝나도 검색에는 들어가지 않는다.** 서비스 색인에 넣으려면 전처리 → 청킹·임베딩 → 릴리스 → 서비스 재시작을 따로 거친다([02 전처리](02_PREPROCESSING.md), [03 색인](03_INDEX_CHUNK_EMBEDDING.md)).

## 2. 카탈로그 범위

| 항목 | 현재 값 |
| :-- | :-- |
| 문서 | 228건 (`data/catalog/documents.jsonl` = DB `corpus.documents`) |
| 마지막 전체 수집 | 2026-10-08 03:52 KST. 이후 카탈로그 행 수 변화 없음 |
| 성분·제품 | ustekinumab 1개 성분, 대조약 Stelara(`CNTO1275`) + 바이오시밀러 5개(SB17·ABP654·CTP43·BMAB1200·BAT2206) |
| 지침 | 21문서 (EMA 6, FDA 7, HC 8) |
| 시험 | 11건, 게시 결과 46행 |

카탈로그 중 서비스 색인에 들어간 범위는 **5개 바이오시밀러의 EMA·FDA·HC 문서 + CT.gov 프로토콜·SAP**(111문서)다. Stelara 88건과 PMDA 문서는 수집만 됐고 색인에 없다. 제품별 색인 수는 [03 색인](03_INDEX_CHUNK_EMBEDDING.md) 2절.

## 3. 수집 경로

### 3.1 CLI (등록 제품 재수집)

```powershell
uv run python -m collection.collect          # 목록 → 문서 → 파싱 → 카탈로그 → SQLite
uv run python -m collection.build_indexes    # pdfs_by_product/ (verify 전에 필요)
uv run python -m collection.verify
uv run python -m ingestion.load_catalog      # 카탈로그 JSONL → DB corpus·registry (TRUNCATE 후 재적재)
```

규칙 파일은 `configs/collection/`(`targets.json`, `document_kinds.json`, `guidelines.json`, `names_ko.json`). 수집 단계에는 OCR이 없다(`--ocr`은 OCR 코드를 옮기지 않아 동작하지 않는다).

### 3.2 적재 요청 에이전트 (화면 `자료 수집·적재` 창)

| 구성 | 위치 |
| :-- | :-- |
| 화면 | `rag_service/web/data-manager.js`, `data-manager.css` (메인 RA 상단 버튼으로 연다) |
| 서버 API | `rag_service/ingestion_api.py` — `/api/ingestion`, `/api/ingestion/sessions[/<id>/turns|actions|approve]`, `/api/ingestion/requests/<id>[/logs]` |
| 에이전트 본체 | `ingestion/agent.py`(대화 상태), `plan.py`(수집 계획), `worker.py`(실행), `registry.py`(이름 사전·허가 상태), `licence_status.py` |
| 스테이징 수집 | `product_search/` (discover → collect_products → promote) |
| 저장 | PostgreSQL `ingest` 스키마(세션·턴·모델 호출·요청·이벤트·승인·태스크) |

흐름: 자연어 요청 → 이름 확인(공식 목록에서 만든 이름 사전) → 허가 현황 표시 → 수집 계획 → 스테이징 수집 → 확인표 → **사용자 승인** → 프로젝트 자료 반영(`promote --apply`) → 카탈로그·이름 사전 DB 갱신 → `done`.

- LLM은 자연어 요청을 구조화하는 데만 쓴다(`gpt-4o-mini`). 대상 확인·허가 상태·실행 여부는 코드와 Pydantic 계약이 정한다.
- 화면의 후보 선택·수집 시작 버튼은 `/actions`로 직접 실행하며 모델을 부르지 않는다.
- 수집 시작은 화면에서 본 계획 해시, 등록 승인은 확인표 해시와 요청 상태를 다시 확인한다.
- 화면은 5초마다 GET으로 상태를 갱신한다(모델 호출·새 턴 없음).

### 3.3 지금 DB에 있는 것

| 항목 | 값 (2026-10-16 16:45 조회) |
| :-- | :-- |
| 에이전트 세션 | 8개 (Ocrevus 현황 조회, Pyzchiva·Wezenla 현황 등) |
| 적재 요청 | 1건 — Imuldosa(DMB3115) `new_product`, **`awaiting_approval`**, 2026-10-14 04:21 KST 생성. 승인하지 않은 상태로 유지 중 |
| `capabilities` | `new_product` 가능 / `refresh`·`new_guideline`·`worker_cancel`·`automatic_search_publish` 미지원 |
| DB 릴리스 목록 | 01·03·04 적재됨 (자세히는 [03 색인](03_INDEX_CHUNK_EMBEDDING.md) 5절) |

## 4. 연결되지 않은 구간과 주의

| 항목 | 현재 |
| :-- | :-- |
| 등록 → 검색 반영 | 자동 연결 없음. `done`은 카탈로그 갱신 완료이며 검색 가능 완료가 아니다 |
| 재수집·지침 추가 | 화면에서 실행하지 않고 수동 명령을 안내한다 |
| 실행 중 작업 취소 | 없음. 자연어 "취소"는 대화 상태만 초기화한다 |
| 진행 로그 | worker 하위 프로세스 로그는 종료 뒤 기록된다. 실시간 진행률 없음 |
| 검색 가능 판단의 기준 릴리스 | `ingestion/registry.py`의 `searchable_documents()`는 `BIOSIM_ACTIVE_RELEASE`의 `chunks.jsonl`에 있는 문서를 "검색 가능"으로 본다. `.env` 값이 **01**이라, 04에서 추가된 19문서(보호 해제 9 + CT.gov 프로토콜·SAP 10)는 실제로 검색되는데도 "검색 안 됨"으로 판단된다 |
| DB 포트 | 이 PC는 `.env`의 55432를 쓴다. 원본 프로젝트와 compose 프로젝트 이름(`biosim`)이 같아 컨테이너·볼륨을 공유한다 |
| 자동 검색의 빈틈 | 캐나다 전용 제품명, PMDA는 discover가 찾지 못한다. 확인표에 "not found"로 나온다 |
| 주기 실행 | 없음. 사람이 실행할 때만 갱신 |

## 5. 원 기록

- [수집 적재 파이프라인 총정리](../collection/INGESTION_PIPELINE.md) — 단계·저장 위치·식별자·검증 규칙
- [적재 요청 에이전트·DB 계획과 구현 기록](../collection/INGESTION_AGENT_DB_PLAN_2026-10-14.md) — 13절 구현 기록, 맨 위 2026-10-15 17:32 화면 연결
- [product_search README](../../product_search/README.md)
