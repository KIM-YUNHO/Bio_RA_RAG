# DB 인수 안내 — PostgreSQL + pgvector

- 작성: 2026-10-14 15:25 KST
- 최종 수정: 2026-10-14 23:25 KST (릴리스 04 덤프·데이터 묶음, 경로 이식. 이전: 16:50 웹 화면 DB 연결)
- 대상: 이 DB를 받아 각자 로컬에서 RAG 검색·웹 화면을 띄울 팀원

## 2026-10-21: 이 PC의 10-14 덤프·데이터 묶음 삭제

`storage/handoff/`의 `biosim_20261014_r04.dump`, `biosim_data_20261014_r04.zip`과 `temp/`의 이전 덤프를 지웠다. 덤프에 들었던 릴리스 01·03·04(청크 10,088·11,536·15,682)는 지금 DB `biosim_ra`의 `rag.releases`에 모두 있다. 다시 넘길 일이 생기면 `biosim_ra`에서 새로 `pg_dump`하고, 웹 화면용 묶음은 `uv run python -m storage.serving_data --zip <경로>`로 만든다. `scripts/setup_local.ps1`은 아직 DB 이름을 `biosim`으로 가정한다(10-18 분리 전 기준).

## 2026-10-14 23시 갱신: 릴리스 04 (서비스 색인)

릴리스 04 = 03 + 보충 19문서(이전에 평가 보호로 빠졌던 Usymro·Yesintek EPAR 등 9문서, ClinicalTrials.gov 프로토콜·SAP 10문서). 111문서·15,682청크. `versions.json`의 `served_by_ra_service`가 04이고, `start_ra_service.ps1`은 인자가 없으면 이 버전으로 뜬다. 코드는 PR #4(`ra-research-rag-4.10.1`) 이후 기준이다.

| 받을 것 | 둘 곳 | 비고 |
| :-- | :-- | :-- |
| 코드 | `git pull` (PR #4 병합 후) | 릴리스 경로 이식 수정 포함 |
| `biosim_20261014_r04.dump` (+ `.sha256`) | `storage\handoff\` | 약 207MB. 릴리스 01·03·04. 가장 최근 덤프를 자동 선택 |
| `biosim_data_20261014_r04.zip` | 프로젝트 루트 | 웹 화면용. `-DataZip` 으로 경로 지정 가능 |

```powershell
# 처음 받는 팀원
.\scripts\setup_local.ps1 -DataZip .\biosim_data_20261014_r04.zip
# 이전 덤프(biosim_20261014.dump)를 이미 복원한 팀원: 기존 DB를 지우고 다시 복원
.\scripts\setup_local.ps1 -ForceRestore -DataZip .\biosim_data_20261014_r04.zip
```

확인: `select release_id, n_chunks, n_documents from rag.releases` → `01…|10088|92`, `03…|11536|92`, `04_pipeline_h_supplement_2026_10_14|15682|111`. 웹 화면 `/api/summary`의 `product_index_chunks`가 15682.

릴리스 04의 매니페스트에는 만든 PC의 절대 경로(`…\project_shared\data\…`, `…\project_shared\pipelines\…`)가 들어 있다. `rag/portable_paths.py`가 읽을 때 받은 사람의 프로젝트 루트로 바꾸므로 폴더 이름·위치가 달라도 된다(파일은 수정하지 않는다).

## 빠른 시작 (팀원용, 2026-10-14 16시 기준 — 위 갱신을 우선)

받을 것 2~3개를 아래 위치에 두고 스크립트를 한 번 실행한다. 이미 끝난 단계는 건너뛰므로 여러 번 실행해도 된다.

| 받을 것 | 둘 곳 | 필요한 경우 |
| :-- | :-- | :-- |
| 코드 | `git pull` | 항상 |
| `biosim_20261014.dump` (+ `.sha256`) | `storage\handoff\` | 항상 |
| `biosim_data.zip` | 프로젝트 루트 | 웹 화면을 띄울 때만 |

```powershell
.\scripts\setup_local.ps1                    # DB 복원 + 환경 + 데이터 + 모델 + 검사
.\scripts\setup_local.ps1 -SkipData -SkipModel   # DB만 쓸 때
.\scripts\setup_local.ps1 -CheckOnly         # 아무것도 바꾸지 않고 무엇이 빠졌는지만 본다
.\scripts\setup_local.ps1 -Start             # 준비가 끝나면 웹 화면까지 시작
```

- 준비물: Docker Desktop(실행 중), `uv`. 웹 화면의 질문 답변에는 `.env`의 `OPENAI_API_KEY`가 필요하다(스크립트가 `.env`를 만들어 주고, 키는 직접 넣는다).
- 스크립트가 하는 일: `.env` 준비 → DB 컨테이너 기동·덤프 해시 확인·복원 → `uv sync` → zip 풀기·필요한 파일 점검 → BGE-M3 모델 내려받기 → DB와 파일이 같은지 검사.
- 5432 포트가 사용 중이면 `-DbPort 5433`처럼 지정한다.
- 아래 1~6절은 스크립트가 하는 일을 손으로 하는 방법과 배경이다.

## 1. 결론

**팀원이 마이그레이션을 다시 돌릴 필요는 없다.** 스키마(001~007)와 데이터는 덤프에 들어 있고, 받는 쪽은 덤프를 복원하면 된다.

다만 **덤프만으로 웹 화면이 뜨지는 않는다.** 화면은 DB의 청크·벡터를 읽지만, 쪽 이미지·원문 표시·표/그림 문맥은 여전히 파일(전처리 산출물과 **원본 PDF**)에서 읽는다. 3절의 데이터가 같이 있어야 한다.

| 하고 싶은 일 | 필요한 것 |
| :-- | :-- |
| DB만 쓴다(SQL, 벡터 검색, 분석) | 덤프 |
| 웹 화면을 각자 로컬에서 띄운다 | 덤프 + 데이터 묶음(3절, 약 2.6GB) + OpenAI 키 + BGE-M3 모델 |

## 2. DB 덤프 복원

덤프: `storage/handoff/biosim_20261014.dump` (약 120MB, `pg_dump -Fc`), 해시: 같은 이름 `.sha256`. **git에는 올리지 않는다**(공유 저장소로 전달). Docker와 이 저장소가 필요하다.

```powershell
# 1) 해시 확인 (.sha256 파일의 값과 같아야 한다)
Get-FileHash storage/handoff/biosim_20261014.dump -Algorithm SHA256

# 2) 빈 DB 띄우기 (PostgreSQL 17 + pgvector 0.8, 127.0.0.1:5432)
Copy-Item .env.example .env       # 처음 한 번
docker compose --env-file .env -f ingestion/db/compose.yaml up -d

# 3) 복원 (HNSW 인덱스를 다시 만드느라 수 분)
docker cp storage/handoff/biosim_20261014.dump biosim-db-1:/var/tmp/r.dump
docker exec biosim-db-1 pg_restore -U biosim -d biosim --no-owner --exit-on-error /var/tmp/r.dump
docker exec biosim-db-1 rm /var/tmp/r.dump

# 4) 확인: 두 릴리스(10,088 / 11,536청크)가 보여야 한다
docker exec biosim-db-1 psql -U biosim -d biosim -c "select release_id, n_chunks, n_documents from rag.releases"
```

- 복원 직후 `public.schema_migrations`에 001~007이 있으므로 `python -m ingestion.db migrate`는 "nothing new"다.
- 각자의 로컬 DB는 서로 독립적이다. 한 사람이 바꾼 내용은 다른 사람 DB에 반영되지 않는다.
- 5432 포트가 이미 쓰이면 `.env`의 `BIOSIM_DB_PORT`와 `BIOSIM_DATABASE_URL`의 포트를 같이 바꾼다. Windows가 부팅마다 포트 범위를 예약할 수 있으니 `netsh interface ipv4 show excludedportrange protocol=tcp`로 확인한다.

## 3. 웹 화면을 로컬에서 띄우기

### 준비물

| 항목 | 내용 | 크기 |
| :-- | :-- | --: |
| 코드 | 이 저장소 + `uv sync` | |
| `.env` | `.env.example`을 복사하고 `OPENAI_API_KEY` 입력(질문마다 gpt-5.6-luna를 호출한다) | |
| BGE-M3 모델 | 질의 임베딩용. 서비스는 **오프라인으로만** 읽는다(`HF_HUB_OFFLINE=1`). 미리 한 번 받아 둔다: `uv run python -c "from sentence_transformers import SentenceTransformer as S; S('BAAI/bge-m3')"` (`~/.cache/huggingface`) | 약 2.3GB |
| **데이터 묶음** | 아래 표. 프로젝트 루트 기준 같은 상대 경로에 풀어 둔다 | 약 2.6GB |

**데이터 묶음** (`uv run python -m storage.serving_data`가 릴리스 매니페스트에서 계산한 값. 파일럿 + H 최신):

| 경로 | 내용 | 크기 |
| :-- | :-- | --: |
| `data/raw/` (두 릴리스가 쓰는 112개) | **원본 PDF·HTML. 필수.** 번들을 열 때 모든 문서의 해시를 검사하고, 오른쪽 원문 패널이 여기서 쪽 이미지를 그린다 | 183MB |
| `data/versions/01_…/embedded/` | 파일럿 릴리스 + 구성요소 후보 89개 | 711MB |
| `data/versions/01_…/preprocessed/` | 파일럿 전처리 번들 + 렌더 PDF | 430MB |
| `data/versions/03_…/embedded/` | H 최신 릴리스 + 후보 | 828MB |
| `data/versions/03_…/preprocessed/` | H 최신 전처리 번들 | 458MB |
| `data/versions/02_…/preprocessed/` | H 최신이 참조하는 HTML 렌더 10개 | 3MB |
| `data/catalog/`, `data/guidelines/`, `data/parsed/`, `data/reviewed/`, `data/runs/ra_guideline_refresh_2026-10-12_v1/`, `data/summary.json`, `data/versions/versions.json` | 카탈로그·지침·파싱본 | 약 6MB |

- 파일럿만 볼 거면 01 항목만, 최신만 볼 거면 03 항목(+02 3MB, 03이 참조하는 01의 렌더 PDF 일부 포함)만 있으면 된다. 정확한 목록은 `--release pilot|latest --list`로 뽑는다.
- 묶음을 만들려면: `uv run python -m storage.serving_data --zip D:\share\biosim_data.zip`. 받는 쪽은 프로젝트 루트에 풀면 된다.
- 절대 경로는 필요 없다. `rag/portable_paths.py`가 원본 프로젝트의 경로를 이 프로젝트의 같은 파일로 바꿔 읽는다.

### 실행

```powershell
$env:BIOSIM_SEARCH_BACKEND = 'postgres'     # 청크·벡터를 DB에서 읽는다(안 켜면 파일에서 읽는다)
.\scripts\start_ra_service.ps1              # http://127.0.0.1:8775/  (파일럿 01을 서빙)
.\.venv\Scripts\python.exe -X utf8 -B -m review_viewer --run data\versions\01_pilot_baseline_2026-10-10\embedded\rag_finalization_2026-10-10_v2 --port 8766
```

- H 최신(03)을 띄우려면 `python -m rag_service`를 직접 실행하고 `--run`에 `data\versions\03_pipeline_h_v2_table_recovery_2026-10-14\embedded\release_final`을 준다(`rag_service/README.md`의 실행 절).
- 서버는 `127.0.0.1`에만 바인딩된다. 같은 PC의 브라우저에서만 열린다.

### 확인한 것 (2026-10-14, 이 PC)

- **코드와 위 데이터 묶음만 둔 빈 폴더**에서 두 릴리스 모두 `RAPipeline`이 DB 백엔드로 구성되고, BGE-M3 예열이 통과했으며(공급자 호출 0), 원문 쪽 이미지가 그려졌다. 그동안 원본 프로젝트(`biosimilar_collection`) 경로를 연 파일은 **0개**다(파이썬 감사 훅). 이 PC에는 원본이 아직 있으므로 "원본을 열지 않았다"가 "묶음이 충분하다"의 근거다.
- DB에서 읽은 청크·벡터·메타·`dense_hits` 결과가 파일에서 읽은 것과 같다(청크는 키 순서까지, 벡터는 비트 단위).
- 전체 테스트 1,172개 통과.
- **하지 않은 것**: 원본 프로젝트가 아예 없는 실제 팀원 PC에서의 기동, 질문 한 건을 끝까지 답변 생성(OpenAI 호출)하는 것. 처음 띄울 때 막히는 곳이 있으면 알려 달라.

## 4. DB 안의 것

| 스키마 | 내용 | 규모 |
| :-- | :-- | :-- |
| `rag` | 릴리스 2개의 청크와 bge-m3 벡터. **`01_pilot_baseline_2026_10_10`**(파일럿, 10,088청크·92문서), **`03_pipeline_h_v2_table_recovery_2026_10_14`**(H 최신, 11,536청크·92문서) | 411MB |
| `corpus` | 수집 카탈로그: 문서 228, 쪽 10,632(본문), 시험 11, 게시 결과 46, 지침 14 | 88MB |
| `registry` | 제품 6, 허가 18, 이름 사전 약 6천 행, 허가 상태 스냅샷 | 7MB |
| `ingest` | 수집 요청·태스크·실행 기록 | 5MB |

`rag.active_release`: `pilot` → 01, `latest` → 03, `served_by_ra_service` → 01(`data/versions/versions.json`의 역할 그대로).

**들어 있지 않은 것**
- 원본 PDF·전처리 산출물, 쪽 화면용 파일(`chunk_contexts`, `source_units`, `visual_*`): 파일이다(3절).
- 02 릴리스: 03과 2청크만 다르다. 필요하면 `python -m storage.rag_loader load --version 02_pipeline_h_2026-10-14`.
- 대화·검토 기록·답변 보고서·API 사용 장부: 아직 파일이다.
- **에이전트 대화 기록은 덤프에서 뺐다**: `ingest.agent_sessions`, `agent_turns`, `model_calls`의 데이터(테이블 구조는 있음). 사용자 문장과 호출 내역이라 전달 대상이 아니라고 보았다.

## 5. 알아야 할 것

1. **같은 PC의 컨테이너 이름.** 원본 프로젝트(`biosimilar_collection`)와 `compose.yaml`의 프로젝트 이름(`biosim`)이 같아 컨테이너 `biosim-db-1`과 볼륨 `biosim_pgdata`를 **공유한다**(이 PC에서만의 일). 다른 PC에서 새로 띄우면 빈 DB다.
2. **검색 거리는 내적이다. 코사인(`<=>`)을 쓰면 안 된다.** `rag/search.py`는 `vec @ query`(내적)로 순위를 매긴다. bge-m3 벡터는 fp16이라 노름이 0.9998~1.0005로 퍼져 있어, 코사인으로 바꾸면 top-10 집합이 질의의 6~8%에서 달라졌다(2026-10-14 측정). 인덱스는 `vector_ip_ops`, 질의는 `<#>`(음의 내적, 작을수록 가깝다)다. 이 설정에서 정확 검색은 numpy와 300/300 질의 동일했다.
3. **지금 서비스의 검색은 "DB에서 읽어 메모리에서 계산"이다.** `BIOSIM_SEARCH_BACKEND=postgres`이면 `load_index`가 청크와 벡터를 DB에서 한 번 읽어 numpy 행렬로 둔다(약 2초). 그 뒤의 `dense_hits`와 BM25는 지금 코드 그대로 메모리에서 한다. pgvector `<#>` 질의로 직접 검색하는 경로는 DB에 준비돼 있지만(HNSW 포함) 서비스 코드는 아직 쓰지 않는다. 질의를 DB로 보내는 구현은 별도 작업이다.
4. **HNSW는 근사다.** top-10 재현율 약 0.99(`ef_search=100`). 평가·일치 검사는 정확 검색(`SET enable_indexscan = off`)으로 한다.
5. **릴리스는 불변이다.** `rag.chunks`·`rag.embeddings_bge_m3`는 UPDATE를 트리거로 막는다. 바뀐 릴리스는 새 `release_id`로 적재하고 `rag.active_release`가 가리키게 한다. 같은 릴리스를 `load`로 다시 돌리면 해시가 같을 때 아무 일도 하지 않고, 다르면 오류다(`--replace`로만 지우고 다시 적재).
6. **벡터는 릴리스마다 파티션이다.** 한 릴리스를 직접 질의하려면 `WHERE release_id = '...'`를 반드시 건다.
7. **`rag.chunks.metadata`(json)에 청크의 나머지 필드가 전부 있다.** 열로 꺼낸 것은 검색 필터에 쓰는 필드뿐이다. 행은 `storage.rag_loader.rebuild(row, keys)`로 `chunks.jsonl` 행과 키 순서까지 같게 복원된다(`jsonb`는 키 순서를 바꿔서 `json`으로 두었다).
8. **`provenance` 안의 경로는 원본 프로젝트의 절대 경로 그대로다**(`C:\Users\Admin\Documents\biosimilar_collection\...`). 데이터는 해시 계약 때문에 고치지 않았다. 코드는 이 경로를 `rag.portable_paths.resolve()`로 이 프로젝트의 사본으로 바꿔 연다. 새 코드가 `provenance`·매니페스트의 경로를 열 때도 이 함수를 거쳐야 한다.
9. **`corpus`와의 연결은 외래키가 아니다.** `rag.chunks`의 `(document_id, version_id)`는 `corpus.document_versions`를 가리키지만 제약은 걸지 않았다. `ingestion.load_catalog`가 `corpus`를 비우고 다시 채우는데(TRUNCATE), 외래키가 있으면 그것이 막힌다. 대신 `verify`가 존재를 검사한다.
10. **`corpus`·`registry`는 JSONL 카탈로그의 사본이다.** 새로 수집(`collection.collect`, `promote`)하면 `ingestion.load_catalog`를 다시 돌려야 한다.
11. **BM25는 DB로 옮기지 않았다.** 계획 문서 4.2절 5번의 결정 그대로다.

## 6. 만든 것

| 파일 | 역할 |
| :-- | :-- |
| `ingestion/db/migrations/005_rag.sql` ~ `007_…` | `rag` 스키마: 릴리스·청크·벡터(릴리스별 파티션, HNSW 내적)·`active_release`·불변 트리거 / 청크 키 순서 / 청크 원문 보존(json) |
| `storage/rag_loader.py` | `load`(릴리스 한 개 = 한 트랜잭션), `verify`, `list` |
| `storage/rag_index.py` | `rag.search.load_index`와 같은 `(chunks, vectors, meta)`를 DB에서 읽는다 |
| `rag/portable_paths.py` | 원본 프로젝트의 절대 경로 → 이 프로젝트의 사본 경로 |
| `storage/serving_data.py` | 웹 화면에 필요한 데이터 파일 목록·크기·zip |
| `storage/copy_serving_files.py` | 첫 이관에서 빠진 파일(파일럿 구성요소 후보 89개 360MB, 렌더 PDF 6개, 지침 원본·파싱본 2개)을 원본에서 읽기만 해서 복사 |
| `storage/handoff/biosim_20261014.dump` | 전달용 덤프 |

고친 기존 파일: `rag/search.py`(`load_index`에 DB 분기), `rag/release.py`·`review_viewer/data.py`·`rag/ra_sources.py`·`rag_service/pages.py`(저장된 경로를 열기 전에 `resolve()`). 원본 프로젝트 경로에서도 동작은 같다(해시 검사가 그대로 있다).
