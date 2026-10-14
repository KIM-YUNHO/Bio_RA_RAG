# pipelines — 전처리 파이프라인

- 작성: 2026-10-14 13:46 KST
- 최종 수정: 2026-10-14 13:46 KST (project_shared 최초 구성)

| 폴더 | 역할 | 상태 | 만든 데이터 |
| :-- | :-- | :-- | :-- |
| [`pipeline_h/`](pipeline_h/README.md) | **표준(Main) 전처리 파이프라인** | 2026-10-14 확정([최종 선정](../docs/preprocessing/FINAL_PREPROCESSING_DECISION_2026-10-14.md)), 92문서 v2 완료 | `data/versions/02_pipeline_h_2026-10-14/` |
| [`backup_pilot/`](backup_pilot/README.md) | 파일럿 기준 파이프라인 (백업·참조용) | 동결. 수정·실행 대상 아님 | `data/versions/01_pilot_baseline_2026-10-10/` (현재 RA 서비스 색인) |

## 코드가 어떻게 나뉘어 있나

```text
pipelines/pipeline_h ──import──▶ preprocess/ (공용 전처리 라이브러리: 문서 등록, HTML 렌더, 검증 등)
        │                       rag/        (공용 청킹·임베딩·릴리스 조립: preprocessed_chunks, chunking, incremental_embeddings, release)
        └─ vendor/compare_study_standalone/ (Docling 어댑터 + ra_prep 후처리, sys.path로 로드)

pipelines/backup_pilot/code_snapshot_2026-10-10/  ← 파일럿 당시 preprocess/·rag/·rag_service/… 의 동결 사본 (import 경로에 없음)
```

- `preprocess/`와 `rag/`는 두 파이프라인과 RA 서비스가 함께 쓰는 **현재 라이브러리**라 루트에 둔다. 파일럿 전용 코드가 아니다.
- 파일럿의 정확한 당시 코드는 `backup_pilot/code_snapshot_2026-10-10/`에 따로 동결돼 있으므로, 루트의 `preprocess/`·`rag/`를 고쳐도 파일럿 재현 기록은 바뀌지 않는다.

## 실행 (프로젝트 루트 PowerShell)

```powershell
# 메인 H: 새 run (설정 템플릿은 pipeline_h/config.example.json)
.\.venv\Scripts\python.exe -X utf8 -m pipelines.pipeline_h.run --run pipelines/pipeline_h/runs/<새_run_이름>

# H 규칙 회귀 테스트
.\.venv\Scripts\python.exe -X utf8 -m unittest pipelines.pipeline_h.test_policy pipelines.pipeline_h.test_batch pipelines.pipeline_h.test_html_source
```

새 파일럿식(cascade) 추출은 루트 `preprocess/`의 CLI로 계속 실행할 수 있다(`python -m preprocess workflow ...`, [preprocess/README.md](../preprocess/README.md)). 다만 표준은 H다.
