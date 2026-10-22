# 현재 서빙 상태 — 파트별 정리

- 작성: 2026-10-16 16:50 KST
- 수정: 2026-10-16 17:55 KST (`feature/stable-queries` 병합 기준: 파이프라인 4.10.3, 계획기 사실 검색어. 병합 후 8885·8775 재시작 전까지 서비스는 4.10.2로 돈다)
- 수정: 2026-10-17 17:45 KST (8775·8885 재시작으로 4.10.3·답변 본문 정리(PR #9) 반영, `BIOSIM_ACTIVE_RELEASE` 04, 원본 검토 페이지 링크 제거, 적재 대화 삭제)
- 수정: 2026-10-18 00:40 KST (이 프로젝트 전용 DB `biosim_ra`로 분리, 적재 계획·등록의 대조약·빈 칸 검사)
- 수정: 2026-10-18 01:30 KST (검색을 pgvector로 전환, 새 릴리스는 `storage.publish_release`로 DB에 올림. 8775·8885 01:25 재시작)
- 수정: 2026-10-21 11:35 KST (코드 정리 브랜치 `chore/code-cleanup`: 검토 화면과 `--review-url`, 표준 라이브러리 서버와 `--server` 제거. 떠 있는 서비스는 그 전 코드로 돈다)
- 수정: 2026-10-22 09:57 KST (main `73e7565d`로 8775·8897 재시작: 정리 후 코드, `rag/` 하위 폴더 구조, 파이프라인 삭제, 마이그레이션 010)
- 수정: 2026-10-22 10:20 KST (8775 내림. 서비스는 8897 하나)
- 기준: 실행 중 서비스의 `GET /api/summary`·`/api/ingestion`·`/api/projects`(8885·8775), 프로세스 명령줄, `data/versions/versions.json`, DB `rag.active_release`, 작업 폴더 코드(main `96894d3` + `feature/memo-reader-session-delete` 화면·적재 커밋)
- 이 폴더는 **지금 떠 있는 서비스가 어떤 데이터·코드·설정으로 답하는가**만 적는다. 실험 경위와 수치의 근거는 각 파일 끝의 원 기록을 따른다. 서빙 구성(릴리스·기본값·모델·포트)이 바뀌면 이 폴더를 먼저 고친다.

## 1. 한눈에 보기

| 항목 | 현재 값 |
| :-- | :-- |
| 서비스 주소 | **http://127.0.0.1:8897/** 하나 (10-22 09:56 `scripts/start_ra_service.ps1`로 시작, main `73e7565d`. 시작 스크립트 기본 포트가 8897). 8775는 10-22 10:20에 내렸다 |
| 파이프라인 코드 | `ra-research-rag-4.10.3` (`feature/stable-queries` 병합 후). 두 서비스 모두 4.10.3을 보고한다 |
| 계획기 검색어 | 답할 사실(facet)마다 영어 검색어 1개, 질문이 붙인 적응증·시험 번호 등 한정어 포함 (`ra-research-tasks-8-fact-queries`) |
| 제품 색인 | 릴리스 **04** `04_pipeline_h_supplement_2026-10-14` — 5제품·**111문서·15,682청크** × 1024차원 |
| 공통 지침 | `ra_guidelines_native_2026-10-12_v2` — 저장 21문서·236청크, 검색 20문서·209청크(FDA 2015 지침 제외) |
| 메인 LLM | `gpt-5.6-luna` (OpenAI, reasoning `none`) — 계획·생성·내부 검토, 질문당 최대 3회 |
| 보조 LLM | `gpt-4o-mini` — 자료 수집 창의 자연어 요청 해석만 |
| 임베딩 | 로컬 `BAAI/bge-m3` dense (1024, L2 정규화, 내적) |
| 1차 검색 | 작업 × 제품 × 기관 그룹마다 dense 40 + BM25 40 → RRF |
| 리랭커 | 로컬 `BAAI/bge-reranker-v2-m3`, 질의 `fquery_unit`, 하이브리드와 RRF 결합, 후보 풀 40 |
| 그룹당 근거 | 8개 (`--retrieval-pages` 기본값) |
| 검색 백엔드 | **pgvector** (`.env` `BIOSIM_SEARCH_BACKEND=pgvector`, 10-18). 청크는 DB `rag.chunks`에서 읽고, 그룹별 dense 검색은 pgvector 질의(필터 안 전수 정확 검색, 동점은 순번 순). 저장된 계획 검색 비교에서 파일 방식과 결과 JSON이 같았다. BM25는 프로세스 안 SQLite FTS5. 서비스 요약 `vector_store`로 확인 |
| 업무 데이터 | PostgreSQL(이 PC 55432) **DB `biosim_ra`**(10-18 원래 프로젝트용 `biosim`에서 분리) — 파이프라인·단계 메모(`app`), 수집 요청·세션(`ingest`), 카탈로그(`corpus`·`registry`), 청크·벡터 사본(`rag`) |
| 원본 검토 화면 | 없음. 화면 링크는 10-16에, 검토 화면 코드(`review_viewer`)와 `--review-url` 인자는 10-21 정리에서 제거했다. 원문 확인은 오른쪽 근거 패널과 메모 상세 창에서 한다 |
| 수집 기준 릴리스 | `.env`의 `BIOSIM_ACTIVE_RELEASE` = 04 (10-17 변경). 수집 에이전트가 04의 111문서를 "검색 가능"으로 본다 |

## 2. 파트별 문서

| 파트 | 문서 | 담는 내용 |
| :-- | :-- | :-- |
| 1. 수집·적재 | [01_COLLECTION_INGESTION.md](01_COLLECTION_INGESTION.md) | 카탈로그 범위, CLI 수집, 적재 요청 에이전트·수집 창, 연결 안 된 구간 |
| 2. 전처리 | [02_PREPROCESSING.md](02_PREPROCESSING.md) | 표준 H 구성, 서비스 색인 04의 전처리 계보, 미서빙 후보 05 |
| 3. 색인 (청킹·임베딩·저장) | [03_INDEX_CHUNK_EMBEDDING.md](03_INDEX_CHUNK_EMBEDDING.md) | 릴리스 04 구성·기본 검색 범위, 청킹·임베딩 설정, 파일·DB 저장, 공통 지침 색인 |
| 4. 질문 계획·검색 | [04_RETRIEVAL.md](04_RETRIEVAL.md) | 계획기, 범위, 검색 그룹, 하이브리드, 리랭커, 공통 지침 검색, 기본값·스위치 |
| 5. 문맥·생성·검증 | [05_GENERATION_VERIFICATION.md](05_GENERATION_VERIFICATION.md) | 근거 패킹, 입력 예산, 프롬프트 모듈·체크리스트, LLM 호출, 검사·완료 판정 |
| 6. 서비스·화면 | [06_SERVICE_UI.md](06_SERVICE_UI.md) | 프로세스·시작 방법, 3단 화면, 파이프라인 메모, 수집 창, 기록 저장 위치 |
| 7. 평가와 진행 중 작업 | [07_EVALUATION.md](07_EVALUATION.md) | 현재 서빙 구성의 측정치, 평가 도구, main에 반영되지 않은 실험 브랜치 |

## 3. 기존 문서 중 현재와 다르게 적힌 곳

과거 기록은 고치지 않는다는 문서 규칙에 따라 아래 문서는 그대로 두었다. 현재 상태는 이 폴더를 우선한다.

| 문서 | 적힌 내용 | 현재 |
| :-- | :-- | :-- |
| [data/README.md](../../data/README.md) §1·§2 | 01 파일럿이 RA 서비스 색인, 03이 최신 | 서비스·최신 모두 **04** (`versions.json` `served_by_ra_service`, DB `rag.active_release` 동일) |
| [rag_service/README.md](../../rag_service/README.md) 하단 `실행`·`검색과 답변`·`API 성능평가` | 01 릴리스, 검색 페이지 수 5, 기본 생성 Ollama, CT.gov 제외 | 04, 그룹당 8개, Luna, CT.gov 프로토콜·SAP는 계획기가 해당 역할을 고를 때만 검색 |
| [RAG 파이프라인 가이드](../rag/RAG_PIPELINE_CHUNK_EMBEDDING_GUIDE_2026-10-14.md) §1·§7.4·§9.2·§13 | 4.9.0, 10,088청크, 리랭커 미연결, 생성 envelope 16,384, H v1 검증 실패 | 4.10.3, 15,682청크, 리랭커 `fquery_unit`+RRF, 서비스 기본 `--context-tokens` 65,536, H v2 완료 후 03→04 |
| [storage/HANDOFF.md](../../storage/HANDOFF.md) §3·§4 (16시 기준 본문) | `served_by_ra_service` → 01, "파일럿 01을 서빙" | 같은 문서 맨 위 23시 갱신이 맞다: 04 |
| [INGESTION_PIPELINE.md](../collection/INGESTION_PIPELINE.md) §8 | 하류 색인 92문서, CT.gov·평가 보류 9문서 제외 | 04에 보류 해제 9문서 + CT.gov 프로토콜·SAP 10문서 포함(2026-10-14 사용자 결정) |
| [RA_QUERY_HANDLING_LOG.md](../RA_QUERY_HANDLING_LOG.md) 앞부분 개념 정리 | 리랭커 질의 `ask_unit`이 "지금 서비스" | 11:38 항목 이후 `fquery_unit`이 서비스 기본값 |

## 4. 다시 확인하는 방법 (읽기 전용)

```powershell
# 서비스 요약: 코드 버전·릴리스·모델·전략 (답변 생성 호출 없음)
Invoke-RestMethod http://127.0.0.1:8897/api/summary |
    Select-Object pipeline_version, run_name, product_index_chunks, chunks_sha256, answer_model,
        query_strategy, retrieval_strategy, answer_strategy, retrieval_pages, guideline_chunks

# 실행 중인 서비스 프로세스와 인자
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object CommandLine -match 'rag_service' |
    Select-Object ProcessId, CommandLine

# 검색 기본값 (리랭커·역할 가산·기본 포함 문서 역할)
Select-String -Path rag/ra/ra_retrieval.py -Pattern '^DEFAULT_PROFILE' -Context 0,2

# 데이터 역할 포인터
Get-Content data/versions/versions.json -Encoding utf8 | ConvertFrom-Json | Select-Object -ExpandProperty roles
```

서비스는 Python 코드를 시작할 때 읽는다. main에 새 기본값을 병합해도 재시작 전에는 서비스에 반영되지 않는다. 화면 파일(`rag_service/web/`)은 새로고침만으로 반영된다.
