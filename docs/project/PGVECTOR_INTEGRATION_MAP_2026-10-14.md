# PostgreSQL + pgvector 통합 작업 지도

- 작성: 2026-10-14 13:46 KST
- 최종 수정: 2026-10-14 15:30 KST (4-1절: RAG 적재 1~4번 완료. 14:59 수집 코드 경로를 `collection/`으로 갱신. 14:45 코드 4.9.3 반영: 검색 경로에 `rag/ra_statistical_tables.py` 추가, 적재 대상에 데이터 버전 03 추가. 14:05에는 구성 직후 코드 위치 확인)

이 문서는 project_shared에서 **전체 데이터를 PostgreSQL + pgvector로 통합할 때 손대야 할 파일·모듈 위치**를 모은 것이다. 목표 스키마와 단계(P5 RAG·세션 이관)는 이미 [적재 에이전트·DB 계획](../collection/INGESTION_AGENT_DB_PLAN_2026-10-14.md) 4절·13절에 있다. 여기서는 그 계획의 테이블과 현재 코드 위치를 1:1로 잇는다. 이번 구성에서 DB 코드는 **원본 그대로** 두었다.

## 1. 지금 DB가 하는 일과 하지 않는 일

| 구분 | 내용 |
| :-- | :-- |
| 컨테이너 | `ingestion/db/compose.yaml` — PostgreSQL 17.11 + pgvector 0.8.7, `127.0.0.1:5432`, 확장 `vector`·`pg_trgm` |
| 접속 | `ingestion/db.py` `url()`(20행)·`connect()`(24행)·`migrate()`(30행). URL은 `.env`의 `BIOSIM_DATABASE_URL` |
| 새 코드의 접속 지점 | `storage/postgres.py` (위 함수를 다시 내보내기만 함). 새 적재·검색 코드는 여기서 import 한다 |
| 스키마 | `ingestion/db/migrations/001~004`: `registry.*`(제품·허가·이름 사전), `corpus.*`(문서·버전·페이지·시험), `ingest.*`(요청·태스크·에이전트 세션). 벡터 테이블은 아직 없음 |
| DB에 없는 것 | 전처리 산출물, 청크, 임베딩, 검색, 대화·검토 기록, 답변 보고서, API 사용 장부 → 모두 파일 |

## 2. 파일 저장 → DB 테이블 대응 (교체 지점)

목표 테이블 이름은 계획 문서 4절을 따른다.

| 데이터 | 지금 저장 위치 | 쓰는 코드 | 읽는 코드 | 목표 |
| :-- | :-- | :-- | :-- | :-- |
| 청크 | `<release>/candidate/chunks.jsonl` | `rag/preprocessed_chunks.py` (pipeline_h는 `pipelines/pipeline_h/chunks.py`에서 호출), `rag/release.py` `compose()` 58행 | `rag/search.py` `load_index()` 48행, `rag/release.py` `ReleaseBundle` 122행 | `rag.chunks` (본문·헤더·메타데이터 jsonb, 키 `document_id, version_id, pdf_page`) |
| 벡터 | `<release>/candidate/emb/bge-m3/doc.npy` + `chunk_ids.json` + `meta.json` | `rag/embed_index.py` 206~208행, `rag/incremental_embeddings.py` `build()` 24행·58행, `rag/release.py` 110~112행, `pipelines/pipeline_h/finish.py` `embed()` 12행 | `rag/search.py` `load_index()` 48행 | `rag.embeddings_bge_m3` (`vector(1024)`, HNSW cosine) |
| 벡터 검색 | 메모리 NumPy 내적 | — | `rag/search.py` `dense_hits()` 79행 ← `rag/pipeline.py` 48행, `rag/ra_retrieval.py` 92행 | pgvector `<=>` 질의 (평가 질문에서 npy 결과와 같아야 함) |
| 통계표 후보 우선 (4.9.2) | 메모리의 청크 목록 | — | `rag/ra_statistical_tables.py` `hits()` ← `rag/ra_retrieval.py` 98행 | 이미 범위가 걸린 후보 청크의 표 헤더(Mean/SD/Median/Range)를 읽으므로, DB 검색으로 바꿔도 같은 후보 행과 `structure` 메타데이터를 넘겨야 한다 |
| BM25 | 요청마다 메모리 SQLite FTS5 | `rag/search.py` `Bm25` 86~93행 | `rag/ra_retrieval.py` 62행 | 그대로 두거나 `tsvector` 전환 (결정 필요) |
| 릴리스 | 폴더 + `release_manifest.json` (절대 경로 포함) | `rag/release.py` `compose()` 58행 | `rag/release.py` `load_bundle()` 167행 ← `rag/pipeline.py` 42행, `review_viewer/__main__.py` 7행 | `rag.releases`(불변) + `rag.active_release` |
| 서비스 릴리스 선택 | `scripts/start_ra_service.ps1` 9~10행의 경로 | — | `rag_service` `--run`·`--guidelines` 인자 | `rag.active_release` 포인터 |
| 공통 지침 색인 | `data/guidelines/<snapshot>/` + `docs/rag/GUIDELINE_STATUS_2026-10-12.json` | `rag/ra_sources.py` | `GuidelineIndex` 96행, 상태 레지스트리 112행 | `rag.releases.kind = 'guideline'` |
| 전처리 산출물 | `data/versions/*/preprocessed/` JSONL 번들 | `pipelines/pipeline_h/export.py`, `preprocess/` | `rag/preprocessed_chunks.py`, `rag/release.py` `Bundle` | `preprocess.*` (계획 4절, P5 이후) |
| 대화 세션 | `--conversation-dir` 아래 대화별 JSON | `rag_service/conversations.py` `ConversationStore` 18행 | 같은 클래스 (`rag_service/__main__.py` 47행에서 생성) | `app.conversations`, `app.turns` |
| 검토 기록 | `--review-dir` 아래 JSON (추가만) | `rag_service/reviews.py` `ReviewStore` 16행 | 같은 클래스 (`rag_service/__main__.py` 31행) | `app.reviews` |
| 답변 보고서 | `data/runs/ra_service_reports/` (`--archive-dir`) | `rag_service/__main__.py` 257행 인자 | 같은 서비스 | `app.jobs` |
| API 사용 장부 | `data/runs/model_quota/<날짜>.json` | `rag/openai_chat.py` 36~37행 | 같은 클래스 | `app.model_calls` (`ingest.model_calls`와 통합 검토) |
| 수집 카탈로그 | `data/catalog/*.jsonl`, `data/collection.sqlite` | `collection/collect.py`, `collection/build_indexes.py` | `collection/verify.py` 85행, `ingestion/db_verify.py` | 이미 `corpus.*`로 미러링 중 (`ingestion/load_catalog.py` `load()` 91행). DB를 원본으로 바꾸는 시점은 계획 P4 이후 |

`qdrant-client`가 `pyproject.toml`에 있지만 어떤 검색 경로에서도 쓰지 않는다. pgvector로 정하면 의존성에서 빼도 된다.

## 3. 먼저 할 일: 데이터 안의 절대 경로

복사한 데이터는 해시 계약 때문에 내용을 고치지 않았다. 그래서 아래 필드에 원본 프로젝트의 절대 경로가 남아 있다([data/README.md](../../data/README.md) 4절).

- `release_manifest.json`의 `candidate`, `components[].run`, `components[].candidate` → `rag/release.py` 126·133·134행이 이 경로를 그대로 연다.
- 청크의 `provenance.parsed_path`, `provenance.preprocessing_run`, `provenance.source_refs[].path`, `study_candidates.anchors[].source_refs[].path`
- 전처리 JSONL의 `source_refs[].path`, `extraction_manifest.json`의 원본·파생본 경로 → `pipelines/pipeline_h/prepare.py` 33행도 파일럿 구성요소의 `extraction_manifest.json`을 이 경로로 연다.

DB에 적재할 때는 **프로젝트 루트 기준 상대 경로**(또는 객체 저장소 키)로 바꿔 넣고, 원래 값은 `provenance_original` 같은 별도 열에 보존한다. 그러면 다른 PC에서도 `ReleaseBundle`·원문 페이지 표시가 동작한다. 청크 본문·메타데이터·벡터는 경로와 무관하므로 적재 자체는 바로 할 수 있다.

## 4. 적재 순서 제안

1. `ingestion/db/migrations/005_rag.sql` — `rag.releases`, `rag.release_documents`, `rag.chunks`, `rag.embeddings_bge_m3`(`vector(1024)`), `rag.active_release`. 마이그레이션은 기존 `migrate()`가 이름 순으로 적용한다(파일만 추가).
2. `storage/rag_loader.py`(신규) — 릴리스 폴더 하나를 읽어 적재. 입력 계약: `chunks.jsonl` i번째 줄 = `chunk_ids.json` i번째 = `doc.npy` i번째 행. `meta.json`의 모델·차원·`chunks_sha256`을 `rag.releases`에 기록. 접속은 `storage.postgres.connect()`.
3. 세 버전 적재: `data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2`(10,088), `data/versions/02_pipeline_h_2026-10-14/embedded/release`(11,536), `data/versions/03_pipeline_h_v2_table_recovery_2026-10-14/embedded/release_final`(11,536, 02와 2청크만 다름 — 같은 `header + "\n" + text`의 벡터는 공유 가능). `data/versions/versions.json`의 `roles`가 어느 쪽이 파일럿·최신·서비스인지 알려 준다.
4. 일치 검사 — 계획 P5 기준: 평가 질문에서 npy 내적 결과와 pgvector 정확 검색 결과가 같아야 한다. 기존 `ingestion/db_verify.py`의 파일↔DB 비교 방식을 따른다.
5. 검색 전환 — `rag/search.py` `load_index()`/`dense_hits()`를 DB 구현으로 바꿀 수 있게 인터페이스를 나누고, `rag/pipeline.py` 48행과 `rag/ra_retrieval.py` 92행이 그 인터페이스를 부르게 한다. 운영 서비스 전환은 사용자 승인 후(계획 P5 "경로 전환은 사용자 승인").
6. 세션·검토·장부 — `ConversationStore`, `ReviewStore`, `openai_chat` 장부를 같은 메서드 이름의 DB 구현으로 교체한다.

## 4-1. 진행 기록 (2026-10-14 15:30 KST): 4절 1~4번 완료

- **1번** `ingestion/db/migrations/005_rag.sql` 작성·적용. `rag.releases`, `chunks`, `release_documents`, `embeddings_bge_m3`(릴리스별 파티션), `active_release`. 릴리스 불변 트리거.
- **2번** `storage/rag_loader.py`. 릴리스 하나를 한 트랜잭션으로 적재한다. 적재 전에 `chunks.jsonl` 해시, `chunk_ids.json` 순서, 벡터 모양·정규화를 검사한다.
- **3번** 적재: 01(10,088청크)과 03(11,536청크). 02는 적재하지 않았다(03과 2청크 차이, 필요하면 `--version`).
- **4번** `verify` 통과: 청크 행 전체가 `chunks.jsonl`과 같고, 벡터는 `doc.npy`와 비트 단위로 같다. 정확 검색은 numpy와 300/300 질의 동일.
- **계획과 달라진 점**: 계획 4.2절 4번은 HNSW(코사인)였으나 **내적**으로 했다. 벡터 노름이 0.9998~1.0005라 코사인은 앱(`rag/search.py`의 `vec @ query`)과 top-10이 6~8% 질의에서 달랐다. 내적에서는 같다.
- **벡터 공유 안 함**: 02·03이 11,534벡터를 공유하지만 릴리스마다 행을 따로 둔다(파티션 단위 HNSW를 단순하게 유지).
- **남은 일**: 5번(검색 전환)과 6번(세션·검토·장부), 3절의 경로 정리. 인수는 [storage/HANDOFF.md](../../storage/HANDOFF.md).

## 5. 환경 변수

`.env`의 `[2] DATABASE` 절에 현재 쓰는 `BIOSIM_DATABASE_URL`, `BIOSIM_DB_PASSWORD`, `BIOSIM_DB_PORT`가 있고, `[2-b]`에 통합 때 쓸 예약 변수(`BIOSIM_VECTOR_SCHEMA`, `BIOSIM_EMBEDDING_MODEL`, `BIOSIM_EMBEDDING_DIM`, `BIOSIM_VECTOR_DISTANCE`, `BIOSIM_VECTOR_RELEASE`, `BIOSIM_SESSION_STORE`)가 주석으로 있다. 예약 변수는 아직 어떤 코드도 읽지 않는다. 쓰기 시작할 때 주석을 풀고 `.env.example`도 같이 바꾼다.

`ingestion/db.py`는 환경 변수만 읽으므로 `ingestion/__init__.py`가 루트 `.env`를 `rag/llm.py`의 `load_env()`로 먼저 적용한다(셸에 이미 있는 값이 우선). Docker는 `docker compose --env-file .env -f ingestion/db/compose.yaml up -d`로 같은 파일을 쓴다.
