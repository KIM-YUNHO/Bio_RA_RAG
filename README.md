# Biosimilar RA 문서 RAG — 공유 기준 프로젝트 (project_shared)

- 작성: 2026-10-14 14:10 KST
- 최종 수정: 2026-10-20 18:40 KST (0절 지금 상태 추가: 운영 8897·코드 4.10.4·pgvector·서비스 기능, 1·4·5절과 변경 이력을 10-20 기준으로 갱신. 이전: 10-16 데이터 버전 표 갱신)

> 지금 서비스가 어떤 데이터·코드·설정으로 도는지는 [docs/current/](docs/current/README.md)에 파트별로 정리했다. 0절은 그 요약이고, 3절 이후의 시작 방법·제약은 10-20에 다시 맞췄다.

우스테키누맙 바이오시밀러 5제품(SB17·ABP654·CTP43·BMAB1200·BAT2206)의 EMA·FDA·Health Canada 규제 문서를 **수집 → 전처리 → 청킹·임베딩 → 근거 인용 RA 답변**까지 처리하는 프로젝트의 공유용 기준본이다. 원본 작업 폴더(`biosimilar_collection`)에서 핵심 코드·데이터·문서만 복사해 다시 구성했다. 무엇을 어디서 가져왔는지는 [이관 기록](docs/project/SHARED_BASELINE_MIGRATION_2026-10-14.md)에 있다.

## 0. 지금 상태 (2026-10-20)

| 항목 | 값 |
| :-- | :-- |
| 운영 서비스 | **http://127.0.0.1:8897/** — main(`010f398`)에서 `scripts/start_ra_service.ps1 -Port 8897 -ReviewUrl http://127.0.0.1:8886`, FastAPI, 코드 `ra-research-rag-4.10.4`. 8775는 10-18 시작본(4.10.3) 그대로, 8885는 10-20에 내림 |
| 색인·검색 | 릴리스 **04**(5제품·111문서·15,682청크, BGE-M3 1024) + 공통 지침. 검색은 **pgvector**(DB `rag.chunks`) + BM25, BGE 리랭커 |
| LLM | `gpt-5.6-luna`(계획·답변·검증·이슈 가이드), `gpt-4o-mini`(수집 창 요청 해석) |
| 업무 DB | PostgreSQL + pgvector, DB `biosim_ra`(파이프라인·메모·수집 요청·카탈로그·청크) |
| 화면 기능 | 근거 인용 RA 질의응답(주장별 검증, 원문 페이지) · 파이프라인 단계 메모 · 내부 문서 기준일별 업무 현황 · **이슈 대응 가이드**(내부 이슈를 공개 심사 자료 RAG로 원인 후보·실험 설계·기관 대응·예상 질의로 정리) · 자료 수집·적재 창 · API 명세 `/api-docs` |
| 데모 | [이슈 대응 v5](docs/demo/issue_guide_v5/README.md)(SB17 FcRn 사례, SB17 문서를 검색에서 빼고 실제 해결과 비교), [업무 현황 v4](docs/demo/internal_pipeline_v4/README.md) |

## 1. 구조

```text
project_shared/
├─ README.md · .env · .env.example · .gitignore · .python-version · pyproject.toml · uv.lock
├─ collection/           수집 진입점: collect · build_indexes · verify · text_layer(PDF 글꼴 복구, 전처리도 사용) · load_reviewed
├─ configs/
│  ├─ collection/        수집 규칙: targets.json · document_kinds.json · guidelines.json · names_ko.json
│  ├─ env/               requirements-paddle.lock.txt (Paddle 작업용 별도 가상환경 고정 목록)
│  └─ preprocess.*.json  전처리 설정
├─ ingestion/            ★ 적재 요청 에이전트 + PostgreSQL(pgvector) 접속·마이그레이션
├─ product_search/       에이전트가 부르는 스테이징 수집(discover → collect → promote)
├─ pipelines/
│  ├─ pipeline_h/        ★ 표준(Main) 전처리 파이프라인 (Docling·TableFormer·PaddleOCR-VL 선별 보강)
│  └─ backup_pilot/      파일럿 기준 파이프라인 동결 사본 (현재 서비스 색인을 만든 코드)
├─ preprocess/           공용 전처리 라이브러리 (pipeline_h·rag가 import)
├─ rag/                  청킹·임베딩·검색·답변 검증 라이브러리
├─ rag_service/          RA 서비스(FastAPI): 질의응답 · 파이프라인 메모 · 내부 문서·업무 현황 · 이슈 대응 가이드 · 수집 창
├─ review_viewer/        전처리·원문 검토 화면
├─ storage/              DB 접속 단일 진입점, 릴리스를 pgvector로 게시(publish_release)
├─ eval/ · gold/ · tests/ · scripts/
├─ data/                 원본·카탈로그 + versions/(01 파일럿 / 02 H v2 / 03 표 회수 / 04 보충 = 현재 서비스 / 05 미등록 후보) × (전처리 / 임베딩)
└─ docs/                 핵심 문서 (docs/README.md가 목차, docs/current/가 현재 서빙 상태, docs/demo/가 시연 시나리오)
```

| 핵심 | 위치 | 설명 |
| :-- | :-- | :-- |
| 적재 요청 에이전트 | `ingestion/agent.py`, `plan.py`, `worker.py`, `tasks.py`, `registry.py`, `licence_status.py` | 사용자가 성분·대조약·바이오시밀러 이름을 말하면 gpt-4o-mini가 요청을 구조화하고, 이름 확인·허가 상태·실행·승인은 코드가 정한다. [계획·진행](docs/collection/INGESTION_AGENT_DB_PLAN_2026-10-14.md) |
| DB | `ingestion/db.py`, `ingestion/db/compose.yaml`, `ingestion/db/migrations/` | PostgreSQL 17 + pgvector 0.8. 현재 카탈로그·레지스트리·요청 기록만 저장 |
| 메인 전처리 | `pipelines/pipeline_h/` | [README](pipelines/pipeline_h/README.md), [최종 선정 근거](docs/preprocessing/FINAL_PREPROCESSING_DECISION_2026-10-14.md) |
| 백업 전처리 | `pipelines/backup_pilot/` | [README](pipelines/backup_pilot/README.md), [파일럿 파이프라인 상세](docs/DATA_PREPROCESSING_PIPELINE.md) |
| 검색·답변 | `rag/`, `rag_service/` | [현재 검색](docs/current/04_RETRIEVAL.md), [생성·검증](docs/current/05_GENERATION_VERIFICATION.md), [RAG 상세(10-14 기준)](docs/rag/RAG_PIPELINE_CHUNK_EMBEDDING_GUIDE_2026-10-14.md) |
| 서비스 화면·API | `rag_service/` (`app.py` FastAPI, `notebook_api.py`, `internal_documents.py`, `issue_guide.py`, `web/`) | [서비스·화면](docs/current/06_SERVICE_UI.md): 실행 중 프로세스, 화면, API 목록, 기록 저장 위치 |
| 수집 | `collection/`, `configs/collection/`, `product_search/` | `python -m collection.collect` → `-m collection.build_indexes` → `-m collection.verify`. [적재 파이프라인](docs/collection/INGESTION_PIPELINE.md), [product_search README](product_search/README.md). 문서에 나오는 `collect.py`, `targets.json` 등은 이 두 폴더에 있다 |
| 기타 폴더 README | [preprocess](preprocess/README.md), [review_viewer](review_viewer/README.md), [storage](storage/README.md), [pipelines](pipelines/README.md), [data](data/README.md) | |

## 2. 데이터 버전

| 버전 | 위치 | 상태 | 청크·벡터 |
| :-- | :-- | :-- | :-- |
| 01 파일럿 기준 | `data/versions/01_pilot_baseline_2026-10-10/` | 이전 서비스 색인. 백업·비교용 | 10,088 × 1024 |
| 02 pipeline_h v2 | `data/versions/02_pipeline_h_2026-10-14/` | 전체 실행·검증 완료. 03·04의 입력 | 11,536 × 1024 |
| 03 v2 표 회수 | `data/versions/03_pipeline_h_v2_table_recovery_2026-10-14/` | 02 + 연속 표 2개 단위 회수. 04의 입력 | 11,536 × 1024 |
| **04 보충 (현재 서비스)** | `data/versions/04_pipeline_h_supplement_2026-10-14/` | 03 + 보충 19문서(보호 해제 9 + CT.gov 프로토콜·SAP 10), 111문서. **2026-10-14 22:28부터 RA 서비스 색인** | 15,682 × 1024 |
| 05 절 제목 재청킹 | `data/versions/05_section_titles_2026-10-15/` | 비교 후 채택하지 않은 후보(`versions.json` 미등록). 서비스 안 함 | 16,725 × 1024 |

같은 17문항·GPT-5.6 Luna 개발 평가에서 완전 충족은 01이 8/17, 02가 11/17이다. 03과 코드 4.9.1~4.9.3은 일부 문항의 제한 재평가만 했다([H v2 개발 평가](docs/rag/H_V2_RAG_DEVELOPMENT_EVAL_2026-10-14.md)). 04와 현재 코드의 측정은 [현재 서빙 상태 — 평가](docs/current/07_EVALUATION.md).

각 버전은 `preprocessed/`(전처리 완료) · `embedded/`(청크+BGE-M3 벡터) · `records/`(실행 기록)로 나뉜다. 역할 포인터는 `data/versions/versions.json`. 상세와 벡터 DB 적재 시 읽을 파일은 [data/README.md](data/README.md).

## 3. 시작하기 (Windows PowerShell, 프로젝트 루트)

```powershell
# 1) 파이썬 환경 (Python 3.12, CUDA 12.6용 torch)
uv sync

# 2) 환경 변수: 하나의 .env 에 LLM·DB·데이터 릴리스 설정이 모두 있다
Copy-Item .env.example .env      # 그 다음 OPENAI_API_KEY 입력

# 3) PostgreSQL + pgvector (같은 .env 사용)
docker compose --env-file .env -f ingestion/db/compose.yaml up -d
uv run python -m ingestion.db migrate
uv run python -m ingestion.load_catalog ; uv run python -m ingestion.db_verify

# 4) 테스트
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests
.\.venv\Scripts\python.exe -X utf8 -B -m unittest pipelines.pipeline_h.test_policy pipelines.pipeline_h.test_batch pipelines.pipeline_h.test_html_source

# 5) RA 서비스 (릴리스를 생략하면 versions.json의 served_by_ra_service = 04, 기본 FastAPI).
#    운영은 8897, 8775도 떠 있으니 시험용은 다른 포트를 쓴다. 같은 포트에 같은 버전이 떠 있으면 스크립트는 그대로 둔다
.\scripts\start_ra_service.ps1 -Port 8895 -ReviewUrl http://127.0.0.1:8896
#    (선택) 전처리 검토 화면. RA 화면은 더 이상 이 화면으로 링크하지 않는다
.\.venv\Scripts\python.exe -X utf8 -B -m review_viewer --run data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release --port 8896

# 6) 적재 요청 에이전트
uv run python -m ingestion.agent

# 7) 메인 전처리 새 run (먼저 pipelines/pipeline_h/config.example.json → config.local.json)
.\.venv\Scripts\python.exe -X utf8 -m pipelines.pipeline_h.run --run pipelines/pipeline_h/runs/<새_run_이름>
```

`pipeline_h`의 Docling·Paddle 작업은 별도 가상환경과 로컬 모델이 필요하다(`pipelines/pipeline_h/vendor/compare_study_standalone/setup.ps1`, `download_models.py`). 모델·가상환경은 이 폴더에 포함하지 않았다.

## 4. PostgreSQL + pgvector

10-18에 검색을 pgvector로 전환했다(`.env` `BIOSIM_SEARCH_BACKEND=pgvector`). 청크와 벡터는 DB `rag.chunks`에서 읽고, 새 릴리스는 `storage.publish_release`로 DB에 올린다. 같은 날 이 프로젝트 전용 DB `biosim_ra`로 분리했다. 현재 구성은 [색인](docs/current/03_INDEX_CHUNK_EMBEDDING.md)·[검색](docs/current/04_RETRIEVAL.md), 통합 당시 작업 지도는 [PGVECTOR_INTEGRATION_MAP](docs/project/PGVECTOR_INTEGRATION_MAP_2026-10-14.md)(기록)에 있다.

- 접속: `storage/postgres.py` ← `ingestion/db.py`, 스키마는 `ingestion/db/migrations/`.
- 릴리스 선택: `data/versions/versions.json`(`served_by_ra_service`), DB `rag.active_release`, `.env` `BIOSIM_ACTIVE_RELEASE`.
- 서비스 기록(파일): `data/runs/ra_service_reports`(답변), `ra_review_records`, `ra_conversations`, `ra_work_drafts`, `ra_issue_guides`, `ra_service_logs`.

## 5. 알아둘 제약

- 데이터는 해시 계약 때문에 바이트 그대로 복사했다. 릴리스 매니페스트와 출처 필드에 **원본 프로젝트의 절대 경로**가 남아 있어, 다른 PC에서는 파일 기반 서비스·검토 화면의 릴리스 로드가 실패한다. 청크·벡터 적재에는 영향이 없다([data/README.md](data/README.md) 4절).
- 이 PC에서는 원본 프로젝트와 **같은 Postgres 컨테이너**(compose 프로젝트 `biosim`, 포트 55432)를 쓰되, DB는 이 프로젝트 전용 `biosim_ra`다(10-18 분리).
- 데이터 안의 원본 절대 경로는 읽을 때 `rag/portable_paths.py`가 이 폴더의 사본으로 바꾼다. 그래서 코드와 `data/`가 같은 폴더에 있어야 한다(git 작업 폴더를 따로 만들면 데이터가 없어 서비스·일부 테스트가 돌지 않는다).
- `collection.collect --ocr`는 OCR 코드(`ocr_pipeline/`)를 옮기지 않아 동작하지 않는다. 수집 단계에서는 OCR을 쓰지 않는다.
- `collection.verify`는 `collection.build_indexes`가 만드는 `pdfs_by_product/INDEX.csv`(원본 PDF를 제품별로 복사한 열람용 폴더, 약 0.5GB, git 제외)를 읽는다. 이 폴더는 옮기지 않았으므로 `build_indexes`를 먼저 실행한다.

## 6. 변경 이력

| 시각 (KST) | 내용 |
| :-- | :-- |
| 2026-10-14 13:49 | 원본 `biosimilar_collection`에서 코드·데이터·문서 복사, 경로 수정 (해시·파일 수 대조 통과) |
| 2026-10-14 13:58 | 다른 세션이 13:50에 고친 `rag/ra_pipeline.py`, `rag/ra_research.py` 재동기화, 누락 모듈·테스트 고정 파일 4개 추가 |
| 2026-10-14 14:06 | 원본에서 계속 수정 중인 `eval/h_v2_*.py` 5개 재동기화 (원본과 14:06 기준 일치) |
| 2026-10-14 14:10 | 통합 `.env`/`.env.example`, `storage/`, README·이관 기록·pgvector 통합 지도 작성. 테스트 1,133개·pipeline_h 18개 통과 |
| 2026-10-14 14:45 | 원본의 H v2 개발 평가·개선 반영: 코드 `ra-research-rag-4.9.3`(`rag/` 4개 수정, `rag/ra_statistical_tables.py` 신규), eval 도구·질문 7개, 테스트 2개, 데이터 버전 03, 평가 기록 `data/runs/h_v2_rag_development_2026-10-14_luna_v1/`, 문서 `docs/rag/H_V2_RAG_DEVELOPMENT_EVAL_2026-10-14.md` |
| 2026-10-14 14:59 | 루트 정리: 수집 스크립트 5개 → `collection/` 패키지, 규칙 JSON 4개 → `configs/collection/`, Paddle 잠금 목록 → `configs/env/`, `pyproject.toml`과 같은 `requirements.txt` 삭제. 경로·import를 참조하던 코드·설정 29개 파일 수정, 테스트 1,143개·pipeline_h 18개 통과 |
| 2026-10-16~17 | 데이터 버전 04를 서비스 색인으로 문서화, `docs/current/` 신설. 계획기 사실 검색어(4.10.3), 답변 본문 정리, 적재 대화 삭제 |
| 2026-10-18 | 전용 DB `biosim_ra` 분리, 검색 pgvector 전환, HTTP 계층 FastAPI 전환(PR #13, `/api-docs`) |
| 2026-10-19 | 내부 문서·기준일별 업무 현황(PR #14), 지침만 검색하는 계획의 제품 문서 대체 검색 `guideline_fallback`(PR #16) |
| 2026-10-20 | 이슈 대응 가이드·파이프라인 업무 요약 줄·RA 주장 검증 출력 예산(4.10.4, PR #17). 운영을 8897로 옮기고 8885를 내림 |
