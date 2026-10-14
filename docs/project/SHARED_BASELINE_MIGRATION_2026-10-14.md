# project_shared 이관 기록

- 작성: 2026-10-14 14:10 KST
- 최종 수정: 2026-10-14 14:59 KST (9절 추가: 루트 정리. 14:45 8절 H v2 개발 평가 반영, 14:10 최초 작성)
- 원본: `C:\Users\Admin\Documents\biosimilar_collection` (변경·이동·삭제 없음, 복사만 함)
- 대상: `C:\Users\Admin\Documents\project_shared`
- 기계 판독 기록: [migration_manifest_2026-10-14.json](migration_manifest_2026-10-14.json) (복사 230건의 원본→대상 경로·파일 수·크기, 경로 수정 파일별 치환 횟수, 해시 대조)

## 1. 요약

| 항목 | 값 |
| :-- | :-- |
| 방식 | `shutil.copy2` 복사(수정 시각 보존). 원본은 읽기만 함 |
| 코드 기준 시각 | 2026-10-14 13:49 KST 복사 → 13:58·14:06 KST 재동기화. 복사 중 다른 세션이 원본을 고쳤다: `rag/ra_pipeline.py`, `rag/ra_research.py`(13:50), `eval/h_v2_*.py` 5개(13:50~14:05, 2개는 신규). 14:06 기준으로 원본과 일치(경로 규칙 적용분 제외) |
| 규모 | 약 4,500 파일 · 3.32GB (대부분 데이터) |
| 데이터 무결성 | 핵심 파일 8개 sha256 일치, 데이터 폴더 전체의 파일 수·바이트 일치 |
| 코드 검증 | compileall 통과, 주요 모듈 241개 import 성공(모두 project_shared 안에서 로드), 테스트 1,133개 통과(skip 4), pipeline_h 테스트 18개 통과 |
| 원본 테스트 기준 | 같은 시각 원본 1,140개 통과. 차이 7개는 아래 4절에서 제외한 테스트 파일 2개 |

## 2. 코드 매핑

| 원본 | project_shared | 비고 |
| :-- | :-- | :-- |
| `pipeline_h/` | `pipelines/pipeline_h/` | **메인 전처리**. 패키지명 `pipelines.pipeline_h`로 변경, 5절 수정 |
| `compare_study_standalone/{compare_study, resources, setup.ps1, requirements-*.txt, download_models.py, config.example.json}` | `pipelines/pipeline_h/vendor/compare_study_standalone/` | pipeline_h가 import 하는 Docling 어댑터·`ra_prep` |
| `compare_study_standalone/output/abp654_design_20261014_v2/`, `…/abp654_15p_20261013_v1/gold.json` | 같은 vendor 아래 `output/` | pipeline_h 회귀 테스트 자료 |
| `pipeline_h/runs/glyph_*_regression/` | `pipelines/pipeline_h/runs/` | 회귀 고정 자료 |
| `data/runs/rag_finalization_2026-10-10_v2/final_code_snapshot/` | `pipelines/backup_pilot/code_snapshot_2026-10-10/` | **파일럿 파이프라인 동결 사본** (`index.json` 해시) |
| `tmp/` 배치 9개 | `pipelines/backup_pilot/batch_scripts/` | 파일럿 릴리스 조립·검증 순서대로 |
| `ingestion/` | `ingestion/` | **적재 요청 에이전트**(agent·plan·worker·tasks·registry·licence_status·sources·contracts) + DB(`db.py`, `db/compose.yaml`, `db/migrations/001~004`). DB 코드는 그대로, `__init__.py`만 `.env` 적용 추가 |
| — | `storage/` (신규) | `storage/postgres.py`: DB 접속 단일 진입점(기존 `ingestion.db` 재수출) |
| `collect.py`, `build_indexes.py`, `verify.py`, `text_layer.py`, `load_reviewed.py`, `targets.json`, `document_kinds.json`, `guidelines.json`, `names_ko.json` | 처음엔 루트 → 9절에서 `collection/`, `configs/collection/`으로 이동 | 수집 진입점·설정 |
| `product_search/` (코드) | `product_search/` | 에이전트가 호출하는 스테이징 수집. `data/`(832MB)는 제외 |
| `preprocess/`, `rag/`, `rag_service/`, `review_viewer/` | 루트 그대로 | 공용 라이브러리·서비스 |
| `configs/`, `eval/`, `gold/`, `tests/`, `scripts/start_ra_service.ps1` | 루트 그대로 | 설정·평가 세트·정답·테스트·서비스 시작 |
| `pyproject.toml`, `uv.lock`, `.python-version`, `requirements*.txt` | 루트 그대로 → 9절에서 `requirements.txt` 삭제, `requirements-paddle.lock.txt`는 `configs/env/` | 환경 정의 |

## 3. 데이터 매핑

| 원본 | project_shared | 단계 |
| :-- | :-- | :-- |
| `data/{raw, repaired, parsed, catalog, registry, reviewed}`, `collection.sqlite`, `summary.json` | 같은 경로 | 원본·카탈로그 |
| `data/runs/*.summary.json` 등 수집 로그, `model_quota/`, `corpus_update_2026-10-09/corpus_plan.json` | 같은 경로 | 수집 기록·API 장부·승인 범위 |
| `data/rag/supplemental/ra_guidelines_native_2026-10-1{1,2}_v*` | `data/guidelines/` | 공통 지침 스냅샷 |
| `data/rag/releases/rag_finalization_2026-10-10_v2/` | `data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2/` | 파일럿 · 임베딩 완료 |
| 위 릴리스의 구성요소 추출 번들 89개 (`data/preprocessed/…/extraction` 등) | `data/versions/01_pilot_baseline_2026-10-10/preprocessed/<원래 상대 경로>` | 파일럿 · 전처리 완료 |
| `data/runs/rag_finalization_2026-10-10_v2/`(코드 사본 제외), `data/rag/service_release.json` | `…/01_pilot_baseline_2026-10-10/records/` | 파일럿 · 기록 |
| `pipeline_h/runs/ustekinumab_20261014_v2/{extraction, html_dom_preview, dom_page_images}` | `data/versions/02_pipeline_h_2026-10-14/preprocessed/` | 최신 · 전처리 완료 |
| `…/v2/candidate`, `…/v2/release` | `…/02_pipeline_h_2026-10-14/embedded/{candidate, release}` | 최신 · 임베딩 완료 |
| `…/v2/` 최상위 파일·`logs/` | `…/02_pipeline_h_2026-10-14/records/` | 최신 · 기록 |
| 테스트 고정 파일 2개 (`data/runs/rag_updates_2026-10-09/stage1_scope/frozen.json`, `data/runs/ra_today_completion_2026-10-12_v3/C04.json`) | 같은 경로 | 테스트용 |

## 4. 제외한 것

**코드**: `compare_study/`(비교 실험·전용 venv·모델, 7.1GB), `compare_study_code_v2/`, `compare_study_standalone/`의 나머지, `ocr_pipeline/`·`ocr_regions.py`(초기 OCR, 수집에서 OCR은 쓰지 않기로 함 — `collect.py --ocr`는 project_shared에서 동작하지 않음), `evidence/`(10-07 근거 단위 실험), `idmp_spike/`, `tmp/`(일회성 스크립트 443개, 파일럿 배치 9개만 백업), `validation_report/`, `pdfs_by_product/`, `query.py`, `scripts/start_ra_development.ps1`(옮기지 않은 10-11 개발 릴리스를 띄움), `scripts/project_cleanup_inventory.py`.

**테스트**: `tests/test_selective_queue.py`(`tmp.collect_visual_queue` import), `tests/test_corpus_finalization.py`(`tmp/finalize_corpus_update.py`와 그 실행 산출물 필요). 둘 다 보관한 배치 코드를 검사하는 테스트다.

**데이터**: 중간 릴리스 8종, pipeline_h v1(2.8GB), `release_before_provenance_correction`, 파일럿 구성요소별 후보 색인(`data/rag/candidates`), 과거 전처리 실험(`data/preprocessed`의 나머지), `data/ocr`, `data/evidence`, `data/rag/{emb, cache, eval}`, `data/runs`의 평가·서비스 실행 기록, `product_search/data` 스테이징(AVT04·DMB3115, 미승격), `data/runs/ingest_requests`(에이전트 요청 로그, DB와 짝). 목록은 [`data/versions/versions.json`](../../data/versions/versions.json)의 `identified_not_copied`.

**환경**: `.venv`, `.venv-paddle`, `compare_study/.venv-docling`, `models/`(2.2GB), Docling 모델(0.7GB), Hugging Face 캐시의 BGE-M3. [pipeline_h README](../../pipelines/pipeline_h/README.md)와 `config.example.json`에 만드는 방법이 있다.

**문서**: 원본 `docs/`의 176개 파일 중 17개만 옮겼다(6절).

## 5. 경로 수정

복사한 코드(.py, .ps1)에서 바꾼 것은 다음뿐이다. 파일별 치환 횟수는 매니페스트 `rewrites`에 있다.

| 바꾼 것 | 적용 범위 |
| :-- | :-- |
| `pipeline_h.` (import, `-m` 실행 대상, `patch()` 대상, 프로세스 확인 문자열) → `pipelines.pipeline_h.` | pipeline_h 27개 파일, `eval/h_v2_native_candidate.py` |
| `ROOT/'pipeline_h/…'` → `ROOT/'pipelines/pipeline_h/…'`, `ROOT = parents[1]` → `parents[2]` | pipeline_h 안에서 루트를 계산하는 곳 |
| `ROOT/'compare_study_standalone…'` → `ROOT/'pipelines/pipeline_h/vendor/compare_study_standalone…'` | `docling_worker.py`, `regression.py`, `glyph_fixture.py`, `test_policy.py`, `metadata_tools.py` |
| `data/rag/releases/rag_finalization_2026-10-10_v2` → `data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2` | `pipeline_h/prepare.py`·`finish.py`, `ingestion/registry.py`, `scripts/start_ra_service.ps1`, `rag/audit_contract_stage1.py`, `rag/eval_contract_stage2.py`, eval 6개 |
| `data/rag/supplemental` → `data/guidelines` | 시작 스크립트, eval, 테스트 2개 |
| `pipeline_h/runs/ustekinumab_20261014_v2/release` → `data/versions/02_pipeline_h_2026-10-14/embedded/release` | `eval/h_v2_development_eval.py` |
| `data/rag/service_release.json` → `data/versions/01_pilot_baseline_2026-10-10/records/service_release.json` | `pipeline_h/prepare.py`(보호 파일 목록) |
| `config.local.json`의 `docling.trigram_path` → vendor 위치 | pipeline_h 실행 설정 |

추가로 쓴 코드: `pipelines/__init__.py`, `storage/__init__.py`, `storage/postgres.py`, `ingestion/__init__.py`의 `.env` 적용 3줄. 릴리스 폴더 이름(`rag_finalization_2026-10-10_v2`, `release`)은 서비스가 `run_name`으로 보고하므로 바꾸지 않았다.

## 6. 문서

옮긴 문서(`docs/` 하위 구조 유지):

- 원칙·전체 구조: `PIPELINE_PRINCIPLES.md`, `DATA_PREPROCESSING_PIPELINE.md`(파일럿 파이프라인 상세)
- 적재: `collection/INGESTION_PIPELINE.md`, `collection/INGESTION_AGENT_DB_PLAN_2026-10-14.md`
- 전처리: `preprocessing/FINAL_PREPROCESSING_DECISION_2026-10-14.md`(+ json 2개), `preprocessing/PIPELINE_COMPLETION_2026-10-10.md`, `preprocessing/TEXT_LAYER_REPAIR.md`
- RAG: `rag/RAG_PIPELINE_CHUNK_EMBEDDING_GUIDE_2026-10-14.md`, `rag/UI_3PANE_AND_RA_FEATURES_PLAN_2026-10-13.md`, `rag/FINALIZATION_API_EVAL_2026-10-10.md`, `rag/GUIDELINE_STATUS_2026-10-12.md`(+ json — `rag/ra_sources.py`가 실행 중 읽는 파일)
- 프로젝트·배경: `project/BIOSIMILAR_WORKFLOW_AND_POC_REVIEW_2026-10-04.md`, `project/LIMITATIONS_AND_NEXT_STEPS_2026-10-12.md`, `research/RELATED_RESEARCH_REVIEW_2026-10-06.md`

코드 폴더에서 모은 문서: `pipeline_h/REPAIR_DESIGN.md`, `pipeline_h/WORK_LOG.md`, v2 `FINAL_REPORT.md` → `docs/preprocessing/pipeline_h/`. 각 코드 폴더의 `README.md`(preprocess, rag_service, review_viewer, product_search, pipeline_h)는 그 폴더에 두고 루트 README에서 연결했다.

새로 쓴 문서: 루트 `README.md`, `docs/README.md`, 이 문서, [PGVECTOR_INTEGRATION_MAP_2026-10-14.md](PGVECTOR_INTEGRATION_MAP_2026-10-14.md), `data/README.md`, `pipelines/README.md`, `pipelines/backup_pilot/README.md`, `pipelines/pipeline_h/vendor/README.md`, `storage/README.md`.

제외한 문서(159개 파일): 날짜별 진행·평가 기록(rag 74, preprocessing 39, evaluation 14), 다른 문서로 합쳐진 이전본(`docs/temp` 11, `docs/collection/temp` 7), 옮기지 않은 코드의 문서(evidence 6), 정리 계획·인벤토리(`PROJECT_CLEANUP_PLAN_2026-10-13.md`, `cleanup/`), 이전 프레임워크 요약(`FRAMEWORK.md`), 방향 검토 기록(project 4), 원본 `docs/README.md`(새로 씀). 폴더 밖 문서 중 비교 실험·tmp·validation_report의 md도 옮기지 않았다.

옮긴 문서 안에 제외한 문서·데이터로 가는 상대 링크가 약 110개 남아 있다. 문서 내용은 고치지 않았고, `docs/` 구조가 같으므로 **같은 상대 경로를 원본 프로젝트에서 열면** 된다.

## 7. 알려진 제약

1. **데이터 속 절대 경로** (2026-10-14 16:50 해결: 열 때 이 프로젝트의 사본으로 바꾼다. [통합 지도](PGVECTOR_INTEGRATION_MAP_2026-10-14.md) 4-1절. 아래는 해결 전 기록) — 릴리스 매니페스트와 청크·전처리 행의 출처 필드가 원본 프로젝트 경로를 가리킨다. 이 PC에서는 원본이 있으므로 서비스·검토 화면이 동작하고, 다른 PC에서는 `ReleaseBundle` 로드가 실패한다. 벡터 적재에는 영향 없음. [통합 지도](PGVECTOR_INTEGRATION_MAP_2026-10-14.md) 3절.
2. **`pipelines/pipeline_h/prepare.py`의 새 run 준비**도 파일럿 구성요소의 `extraction_manifest.json`을 그 절대 경로로 연다. 같은 제약이다.
3. **모델·가상환경 미포함** — `config.local.json`은 이 PC의 원본 위치 환경·모델을 가리킨다. 다른 PC는 `config.example.json`으로 새로 만든다.
4. **같은 PC의 공유 자원** — 원본 RA 서비스가 8875에서 실행 중이다. project_shared 서비스는 다른 포트로 띄운다(`-Port`). 두 프로젝트의 `compose.yaml`은 같은 프로젝트 이름(`biosim`)이라 **같은 Postgres 컨테이너·볼륨을 쓴다**.
5. **원본이 계속 바뀐다** — 다른 세션이 원본의 `rag/`와 `eval/h_v2_*`(H v2 개발 평가)를 고치고 있었다. 이 사본은 14:06 KST 상태이며, 그 작업의 결과는 8절에서 14:39 KST에 반영했다.

## 8. 후속 반영: H v2 개발 평가·개선 (2026-10-14 14:39 KST)

원본의 [H v2 개발 평가·개선](../rag/H_V2_RAG_DEVELOPMENT_EVAL_2026-10-14.md)(14:23 작성)과 그 작업이 바꾼 코드·데이터를 같은 규칙으로 옮겼다. 복사 기록은 매니페스트 `post_build`의 마지막 항목에 있다.

| 구분 | 반영 내용 |
| :-- | :-- |
| 코드 (4.9.0 → **4.9.3**) | `rag/ra_research.py`(이미 13:58 반영: 보충 검색도 읽기 범위 준수), `rag/ra_pipeline.py`, `rag/ra_quantities.py`, `rag/ra_retrieval.py`, `rag/ra_review.py` 갱신, `rag/ra_statistical_tables.py` 신규(요청 통계량-표 헤더 검색) |
| 평가 도구 | `eval/h_v2_development_eval.py`, `h_v2_development_report.py`, `h_v2_review_packets.py` 갱신, `h_v2_saved_response_replay.py` 신규, 고정 질문·검토 노트 JSON 3개 신규 |
| 테스트 | `tests/test_ra_proposed_quantities.py`, `tests/test_ra_statistical_table_retrieval.py` 신규 |
| 데이터 버전 03 | `…/native_candidate_v2/{extraction, candidate_final, release_final}` → `data/versions/03_pipeline_h_v2_table_recovery_2026-10-14/{preprocessed/extraction, embedded/candidate_final, embedded/release_final}`, 후보 구성·감사 JSON → `records/`. `versions.json`의 `latest`를 03으로 바꾸고 02는 `latest_full_pipeline_run`으로 둠 |
| 평가 기록 | `data/runs/h_v2_rag_development_2026-10-14_luna_v1/` (문서 링크 유지를 위해 원래 경로, 03으로 옮긴 큰 폴더와 폐기된 `native_candidate/`·보정 전 `candidate/`·`release/` 제외), 중단된 mini 실행의 `INTERRUPTED_BY_MODEL_CORRECTION.json` |
| 문서 | 새 평가 문서 복사(경로 규칙 적용, `release_final` 경로는 03 위치로), RAG 상세 문서의 "최신 결과는 이 문서 참고" 안내 1줄 반영 |

검증: 03 핵심 파일 5개 sha256 일치, 03 릴리스가 `load_bundle`·`load_index`로 열림(11,536벡터·92문서), 코드 드리프트 0(의도한 `pipeline_h` README·설정 수정 제외), 테스트 1,143개 통과(skip 4, 원본 기록 1,150개에서 제외 파일 2개분 차이), pipeline_h 18개 통과. 03도 매니페스트에 원본 절대 경로가 있다(7절 1번과 같은 제약). 원본 서비스는 여전히 4.9.0/파일럿 색인으로 실행 중이며 이번 반영은 서비스와 무관하다.

## 9. 후속 정리: 루트 파일 (2026-10-14 14:59 KST)

루트에 흩어져 있던 18개 파일을 7개로 줄였다. 남은 것은 도구가 루트에 있어야 찾는 파일뿐이다: `README.md`, `.env`, `.env.example`, `.gitignore`, `.python-version`, `pyproject.toml`, `uv.lock`.

| 이전 위치 | 새 위치 | 비고 |
| :-- | :-- | :-- |
| `collect.py`, `build_indexes.py`, `verify.py`, `text_layer.py`, `load_reviewed.py` | `collection/` (패키지) | 실행은 `python -m collection.<모듈>`. 각 파일의 프로젝트 루트 계산을 `parents[1]`로 변경 |
| `targets.json`, `document_kinds.json`, `guidelines.json`, `names_ko.json` | `configs/collection/` | `names_ko.json`은 아직 읽는 코드가 없는 식약처 한국어 이름 시드 |
| `requirements-paddle.lock.txt` | `configs/env/` | Paddle 작업용 별도 가상환경(`.venv-paddle`)의 고정 목록 |
| `requirements.txt` | 삭제 | 8개 고정 버전이 `pyproject.toml`과 같음을 확인한 뒤 삭제(원본 프로젝트에는 남아 있음) |

같이 고친 참조(코드·설정 29개 파일):
- import: `collect` → `collection.collect`(ingestion/tasks, product_search 4개, 테스트 2개), `text_layer` → `collection.text_layer`(preprocess 5개, `pipelines/pipeline_h/export.py`, `preprocess/inventory.py`의 동적 import, 테스트 2개의 `patch` 대상), `load_reviewed` → `collection.load_reviewed`(테스트 1개).
- 규칙 파일 경로: `collection/collect.py`·`build_indexes.py`·`verify.py`, `ingestion/contracts.py`·`tasks.py`, `product_search/discover.py`·`promote.py`·`coverage.py`, `rag_service/inventory.py`(3분할 화면의 자료 목록), 테스트 2개.
- 코드 지문: `preprocess/workflow.py`, `preprocess/paddle_adapter.py`가 해시하는 `text_layer.py` 경로.
- 실행 명령: `product_search/promote.py`의 후속 실행을 `-m collection.build_indexes`, `-m collection.verify`로, `ingestion/agent.py`의 안내 문구.
- 문서: 실행 명령(`python collect.py` 등)과 파일 링크를 새 위치로 바꿨다. 본문의 `targets.json` 같은 파일 이름은 그대로 두었다.

바꾸지 않은 것: 데이터에 저장되는 출처 라벨(`source: "targets.json"` 등)은 값이므로 그대로 둔다. `collect.ROOT`(데이터 루트, 스테이징이 `product_search/`로 바꿈)와 `collect.PROJECT`(프로젝트 루트)의 의미도 같다.

검증: 모듈 244개 import, `collection.collect --help`, 규칙 파일 로더(`load_targets` 6제품, `load_guidelines` 14건), 스테이징 import 후 `collect.ROOT` 전환 확인, 테스트 1,143개·pipeline_h 18개 통과. `collection.verify`는 규칙 파일 검사까지 통과한 뒤 `pdfs_by_product/INDEX.csv`가 없어 멈췄다. 이 폴더는 `build_indexes`가 원본 PDF를 제품별로 복사해 만드는 열람용 폴더(원본 515MB)로, 처음 이관 때부터 옮기지 않았다(`.gitignore`에 추가).

이 정리로 `preprocess/` 8개 파일과 `pipelines/pipeline_h/export.py`가 원본과 한 줄씩(import 또는 해시 대상 경로) 달라졌다. 같은 코드의 해시로 캐시를 찾는 경로(전처리 재사용)는 원본 프로젝트의 캐시와 맞지 않는다.
