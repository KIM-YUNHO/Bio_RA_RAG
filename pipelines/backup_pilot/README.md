# backup_pilot — 파일럿 기준 파이프라인 (동결 백업)

- 작성: 2026-10-14 13:46 KST
- 최종 수정: 2026-10-14 13:46 KST (project_shared 최초 구성)

현재 RA 서비스가 쓰는 색인 `rag_finalization_2026-10-10_v2`(92문서·4,160쪽·10,088벡터)를 만든 코드다. **메인 파이프라인은 [`../pipeline_h`](../pipeline_h/README.md)** 이고, 이 폴더는 비교·재현 근거로만 보관한다.

## 내용

| 경로 | 무엇 | 원본 위치 |
| :-- | :-- | :-- |
| `code_snapshot_2026-10-10/` | 릴리스 확정 시점(2026-10-10 15:03 KST 전후)의 `preprocess/`, `rag/`, `rag_service/`, `review_viewer/`, `eval/`, `tests/` 동결 사본. `index.json`에 파일별 sha256 | `data/runs/rag_finalization_2026-10-10_v2/final_code_snapshot/` |
| `batch_scripts/` | 승인 92문서를 보완·최종화하고 릴리스를 조립·검증한 배치 9개 (실행 순서는 아래) | `tmp/` |

배치 실행 순서(원본 기록 기준):

1. `build_corpus_plan.py` — 승인 범위(`data/runs/corpus_update_2026-10-09/corpus_plan.json`) 고정
2. `run_light_region_batch.py` — 일반 OCR(PP-OCR) 영역 보완
3. `complete_preprocessing_corpus.py` → `finalize_preprocessing_corpus.py` — 표 구조 복구·대기 큐 종결·청킹·임베딩
4. `build_rag_context_update.py` — 제목·절·표 문맥 갱신
5. `build_rag_finalization.py` → `build_rag_finalization_v2.py` → `finalize_rag_finalization.py` — 최종 릴리스 조립
6. `verify_rag_finalization_v6.py` — 릴리스 검증

## 쓰는 법과 제약

- **수정하지 않는다.** 스냅샷은 `index.json`의 해시와 일치해야 재현 근거가 된다.
- **import 경로에 없다.** 루트 `preprocess/`·`rag/`와 이름이 같지만 `sys.path`에 올라가지 않으므로 서로 섞이지 않는다. 테스트(`python -m unittest discover -s tests`)도 이 폴더를 수집하지 않는다.
- `batch_scripts/`는 원본 프로젝트의 중간 산출물(`data/preprocessed/corpus_update_2026-10-09/...` 등)을 입력으로 고정한 기록용 코드다. project_shared에는 그 중간 산출물 전체가 없으므로 그대로 재실행할 수 없다. 결과물(구성요소 추출 번들 89개와 최종 릴리스)은 `data/versions/01_pilot_baseline_2026-10-10/`에 있다.
- 파이프라인 전체 설명: [docs/DATA_PREPROCESSING_PIPELINE.md](../../docs/DATA_PREPROCESSING_PIPELINE.md), 완료 기록: [docs/preprocessing/PIPELINE_COMPLETION_2026-10-10.md](../../docs/preprocessing/PIPELINE_COMPLETION_2026-10-10.md), [docs/rag/FINALIZATION_API_EVAL_2026-10-10.md](../../docs/rag/FINALIZATION_API_EVAL_2026-10-10.md).
