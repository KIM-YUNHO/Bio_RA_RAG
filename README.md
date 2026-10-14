# Biosimilar RA 문서 RAG — 공유 기준 프로젝트 (project_shared)

- 작성: 2026-10-14 14:10 KST
- 최종 수정: 2026-10-14 14:59 KST (루트 정리: 수집 스크립트 → `collection/`, 규칙 파일 → `configs/collection/`. 14:45 H v2 개발 평가 반영, 14:10 최초 구성)

우스테키누맙 바이오시밀러 5제품(SB17·ABP654·CTP43·BMAB1200·BAT2206)의 EMA·FDA·Health Canada 규제 문서를 **수집 → 전처리 → 청킹·임베딩 → 근거 인용 RA 답변**까지 처리하는 프로젝트의 공유용 기준본이다. 원본 작업 폴더(`biosimilar_collection`)에서 핵심 코드·데이터·문서만 복사해 다시 구성했다. 무엇을 어디서 가져왔는지는 [이관 기록](docs/project/SHARED_BASELINE_MIGRATION_2026-10-14.md)에 있다.

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
├─ rag_service/          RA 질의응답 서비스 (3분할 화면)
├─ review_viewer/        전처리·원문 검토 화면
├─ storage/              DB 접속 단일 진입점 — pgvector 통합 코드가 들어갈 자리
├─ eval/ · gold/ · tests/ · scripts/
├─ data/                 원본·카탈로그 + versions/(01 파일럿 기준 / 02 H v2 / 03 H v2 표 회수 후보) × (전처리 / 임베딩)
└─ docs/                 핵심 문서 (docs/README.md가 목차)
```

| 핵심 | 위치 | 설명 |
| :-- | :-- | :-- |
| 적재 요청 에이전트 | `ingestion/agent.py`, `plan.py`, `worker.py`, `tasks.py`, `registry.py`, `licence_status.py` | 사용자가 성분·대조약·바이오시밀러 이름을 말하면 gpt-4o-mini가 요청을 구조화하고, 이름 확인·허가 상태·실행·승인은 코드가 정한다. [계획·진행](docs/collection/INGESTION_AGENT_DB_PLAN_2026-10-14.md) |
| DB | `ingestion/db.py`, `ingestion/db/compose.yaml`, `ingestion/db/migrations/` | PostgreSQL 17 + pgvector 0.8. 현재 카탈로그·레지스트리·요청 기록만 저장 |
| 메인 전처리 | `pipelines/pipeline_h/` | [README](pipelines/pipeline_h/README.md), [최종 선정 근거](docs/preprocessing/FINAL_PREPROCESSING_DECISION_2026-10-14.md) |
| 백업 전처리 | `pipelines/backup_pilot/` | [README](pipelines/backup_pilot/README.md), [파일럿 파이프라인 상세](docs/DATA_PREPROCESSING_PIPELINE.md) |
| 검색·답변 | `rag/`, `rag_service/` | [RAG 상세](docs/rag/RAG_PIPELINE_CHUNK_EMBEDDING_GUIDE_2026-10-14.md), [서비스 README](rag_service/README.md) |
| 수집 | `collection/`, `configs/collection/`, `product_search/` | `python -m collection.collect` → `-m collection.build_indexes` → `-m collection.verify`. [적재 파이프라인](docs/collection/INGESTION_PIPELINE.md), [product_search README](product_search/README.md). 문서에 나오는 `collect.py`, `targets.json` 등은 이 두 폴더에 있다 |
| 기타 폴더 README | [preprocess](preprocess/README.md), [review_viewer](review_viewer/README.md), [storage](storage/README.md), [pipelines](pipelines/README.md), [data](data/README.md) | |

## 2. 데이터 버전

| 버전 | 위치 | 상태 | 청크·벡터 |
| :-- | :-- | :-- | :-- |
| 01 파일럿 기준 | `data/versions/01_pilot_baseline_2026-10-10/` | 현재 RA 서비스 색인 | 10,088 × 1024 |
| 02 pipeline_h v2 | `data/versions/02_pipeline_h_2026-10-14/` | 전체 실행·검증 완료, 서비스 미전환 | 11,536 × 1024 |
| 03 v2 표 회수 후보 (최신) | `data/versions/03_pipeline_h_v2_table_recovery_2026-10-14/` | 02 + 연속 표 2개 단위 회수, 별도 검토 후보 | 11,536 × 1024 |

같은 17문항·GPT-5.6 Luna 개발 평가에서 완전 충족은 01이 8/17, 02가 11/17이다. 03과 코드 4.9.1~4.9.3은 일부 문항의 제한 재평가만 했다([H v2 개발 평가](docs/rag/H_V2_RAG_DEVELOPMENT_EVAL_2026-10-14.md)).

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

# 5) RA 서비스 (파일럿 기준 데이터). 다른 서비스가 쓰는 포트는 피한다
.\scripts\start_ra_service.ps1 -Port 8895 -ReviewUrl http://127.0.0.1:8896
.\.venv\Scripts\python.exe -X utf8 -B -m review_viewer --run data/versions/02_pipeline_h_2026-10-14/embedded/release --port 8896

# 6) 적재 요청 에이전트
uv run python -m ingestion.agent

# 7) 메인 전처리 새 run (먼저 pipelines/pipeline_h/config.example.json → config.local.json)
.\.venv\Scripts\python.exe -X utf8 -m pipelines.pipeline_h.run --run pipelines/pipeline_h/runs/<새_run_이름>
```

`pipeline_h`의 Docling·Paddle 작업은 별도 가상환경과 로컬 모델이 필요하다(`pipelines/pipeline_h/vendor/compare_study_standalone/setup.ps1`, `download_models.py`). 모델·가상환경은 이 폴더에 포함하지 않았다.

## 4. PostgreSQL + pgvector 통합 시 볼 곳

[통합 작업 지도](docs/project/PGVECTOR_INTEGRATION_MAP_2026-10-14.md)에 파일·행 단위로 정리했다. 요점:

- 접속: `storage/postgres.py`(새 코드의 진입점) ← `ingestion/db.py`. 스키마 추가는 `ingestion/db/migrations/005_*.sql`.
- 벡터·청크: 지금은 `<release>/candidate/emb/bge-m3/doc.npy`와 `chunks.jsonl`. 쓰기 `rag/embed_index.py`·`rag/incremental_embeddings.py`·`rag/release.py`, 읽기·검색 `rag/search.py`(`load_index`, `dense_hits`, `Bm25`) ← `rag/pipeline.py`, `rag/ra_retrieval.py`.
- 릴리스 선택: `rag/release.py`(`load_bundle`), `scripts/start_ra_service.ps1`, `ingestion/registry.py`(`BIOSIM_ACTIVE_RELEASE`).
- 세션·검토·장부: `rag_service/conversations.py`, `rag_service/reviews.py`, `rag/openai_chat.py`.
- 먼저 할 일: 데이터 안에 남은 원본 절대 경로를 상대 경로로 바꿔 적재(아래 5절).

## 5. 알아둘 제약

- 데이터는 해시 계약 때문에 바이트 그대로 복사했다. 릴리스 매니페스트와 출처 필드에 **원본 프로젝트의 절대 경로**가 남아 있어, 다른 PC에서는 파일 기반 서비스·검토 화면의 릴리스 로드가 실패한다. 청크·벡터 적재에는 영향이 없다([data/README.md](data/README.md) 4절).
- 이 PC에서는 원본 프로젝트와 **같은 Postgres 컨테이너**(compose 프로젝트 `biosim`)를 쓰고, 원본 RA 서비스가 8875를 쓰고 있다.
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
