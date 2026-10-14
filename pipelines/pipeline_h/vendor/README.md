# vendor — pipeline_h가 가져다 쓰는 외부 코드

- 작성: 2026-10-14 13:46 KST

`compare_study_standalone/`은 원본 프로젝트의 같은 이름 폴더(전처리 3안 비교 실험)에서 **pipeline_h 실행에 필요한 부분만** 복사한 것이다.

| 경로 | 쓰는 곳 |
| :-- | :-- |
| `compare_study/` (`settings.py`, `bench/adapters/docling.py`, `bench/common.py`, `bench/vendor/ra_prep/`) | `docling_worker.py`, `regression.py`가 `sys.path`에 올려 import, `metadata_tools.py`가 `ra_prep/structure.py`를 파일 경로로 로드 |
| `resources/trigram_en.json` | Docling 텍스트 품질 점검 (`config.*.json`의 `docling.trigram_path`) |
| `setup.ps1`, `requirements-*.txt` | `.venv-paddle`, `.venv-docling` 실행 환경 생성 |
| `download_models.py`, `config.example.json` | Docling·Paddle 모델 내려받기 |
| `output/abp654_design_20261014_v2/`, `output/abp654_15p_20261013_v1/gold.json` | `test_policy.py`, `regression.py`, `glyph_fixture.py`의 고정 회귀 자료 |

비교 실험 자체의 데이터·결과(원본 `compare_study/` 7.1GB, `compare_study_standalone/output`의 나머지)는 옮기지 않았다.
