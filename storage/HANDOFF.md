# DB 인수 안내 — PostgreSQL + pgvector

- 작성: 2026-10-14 15:25 KST
- 대상: 이 DB를 받아 RAG 검색·서비스를 DB로 옮길 팀원

## 1. 결론

**팀원이 마이그레이션을 다시 돌릴 필요는 없다.** 스키마(001~005)와 데이터는 이미 들어 있고, 그대로 덤프로 만들어 두었다. 받는 쪽은 **덤프를 복원**하면 같은 DB가 된다. 코드(`rag/`, `rag_service/`)를 DB로 읽게 바꾸는 일만 남아 있다([통합 지도](../docs/project/PGVECTOR_INTEGRATION_MAP_2026-10-14.md) 4절 5~6번).

## 2. 안에 들어 있는 것 (2026-10-14 15:17 KST 기준)

| 스키마 | 내용 | 규모 |
| :-- | :-- | :-- |
| `rag` | 릴리스 2개의 청크와 bge-m3 벡터. **`01_pilot_baseline_2026_10_10`**(파일럿, 10,088청크·92문서), **`03_pipeline_h_v2_table_recovery_2026_10_14`**(H 최신, 11,536청크·92문서) | 411MB |
| `corpus` | 수집 카탈로그: 문서 228, 문서 버전 228, 쪽 10,632(본문), 시험 11, 게시 결과 46, 지침 14 | 88MB |
| `registry` | 제품 6, 허가 18, 이름 사전 약 6천 행, 허가 상태 스냅샷 | 7MB |
| `ingest` | 수집 요청·태스크·실행 기록 | 5MB |

`rag.active_release`: `pilot` → 01, `latest` → 03, `served_by_ra_service` → 01(`data/versions/versions.json`의 역할 그대로. 운영 서비스는 아직 파일을 읽는다).

**들어 있지 않은 것**
- 원본 PDF·HTML, 전처리 산출물, `chunk_contexts`·`source_units`·`visual_*`(쪽 화면·근거 표시용): 파일로 남아 있다. DB에는 `corpus.raw_objects.raw_path`(프로젝트 루트 기준 상대 경로)만 있다.
- 02 릴리스(`02_pipeline_h_2026-10-14`): 03과 2청크만 다르다. 필요하면 `python -m storage.rag_loader load --version 02_pipeline_h_2026-10-14`.
- 대화·검토 기록·답변 보고서·API 사용 장부: 아직 파일이다(통합 지도 2절).
- 덤프에서 **에이전트 대화 기록은 뺐다**: `ingest.agent_sessions`, `ingest.agent_turns`, `ingest.model_calls`의 데이터(테이블 구조는 있음). 사용자 문장과 호출 내역이라 전달 대상이 아니라고 보았다. 필요하면 말해 달라.

## 3. 복원 (받는 쪽)

덤프: `storage/handoff/biosim_20261014.dump` (120MB, `pg_dump -Fc`), 해시: 같은 이름 `.sha256`. **git에는 올리지 않는다**(공유 저장소로 전달).

필요한 것: Docker, 이 저장소. PostgreSQL 17 + pgvector 0.8 이미지는 `ingestion/db/compose.yaml`이 정한다.

```powershell
# 1) 해시 확인
Get-FileHash storage/handoff/biosim_20261014.dump -Algorithm SHA256   # .sha256 파일과 같아야 한다

# 2) 빈 DB 띄우기 (이미 biosim 볼륨이 있으면 먼저 확인할 것: 아래 4절)
docker compose --env-file .env -f ingestion/db/compose.yaml up -d

# 3) 복원
docker cp storage/handoff/biosim_20261014.dump biosim-db-1:/var/tmp/r.dump
docker exec biosim-db-1 pg_restore -U biosim -d biosim --no-owner --exit-on-error /var/tmp/r.dump
docker exec biosim-db-1 rm /var/tmp/r.dump

# 4) 확인
docker exec biosim-db-1 psql -U biosim -d biosim -c "select release_id, n_chunks, n_documents from rag.releases"
```

복원 직후 `public.schema_migrations`에 001~005가 들어 있으므로 `python -m ingestion.db migrate`는 "nothing new"가 된다. 복원 시간은 HNSW 인덱스를 다시 만드느라 수 분 걸린다.

데이터 폴더(`data/versions/...`)까지 있으면 파일과 DB가 같은지 검사할 수 있다:

```powershell
uv run python -m storage.rag_loader verify    # 청크 행 전체 = chunks.jsonl, 벡터 = doc.npy(비트 단위), 정확 검색 = numpy
```

### 덤프 없이 처음부터 만들기

`data/` 전체가 있을 때만 된다(약 3GB, git 밖).

```powershell
docker compose --env-file .env -f ingestion/db/compose.yaml up -d
uv run python -m ingestion.db migrate               # 001~005
uv run python -m ingestion.load_catalog             # corpus·registry (JSONL이 원본)
uv run python -m storage.rag_loader load            # rag: pilot + latest (약 40초)
uv run python -m storage.rag_loader verify
```

## 4. 받는 쪽이 알아야 할 것

1. **같은 PC의 컨테이너 이름.** 원본 프로젝트(`biosimilar_collection`)와 `compose.yaml`의 프로젝트 이름(`biosim`)이 같아 컨테이너 `biosim-db-1`과 볼륨 `biosim_pgdata`를 **공유한다**. 이 PC에서는 `rag` 스키마가 그 DB에 같이 들어 있다. 다른 PC에서 새로 띄우면 빈 DB다. 5432는 Windows가 예약할 수 있으니 `netsh interface ipv4 show excludedportrange protocol=tcp`로 확인하고, 다르게 쓰려면 `.env`의 `BIOSIM_DB_PORT`를 바꾼다. 같은 PC에 다른 pgvector 컨테이너(`biosimilar_ra_project-db-1`, 5434)가 있다. 서로 다른 DB다.
2. **검색 거리는 내적이다. 코사인(`<=>`)을 쓰면 안 된다.** `rag/search.py`는 `vec @ query`(내적)로 순위를 매긴다. bge-m3 벡터는 fp16이라 노름이 0.9998~1.0005로 퍼져 있어, 코사인으로 바꾸면 top-10 집합이 질의의 6~8%에서 달라졌다(2026-10-14 측정). 그래서 인덱스는 `vector_ip_ops`, 질의는 `<#>`(음의 내적, 작을수록 가깝다)다. 이 설정에서 정확 검색은 numpy와 300/300 질의 동일했다.
3. **HNSW는 근사다.** top-10 재현율 약 0.99(`ef_search=100`). 평가·일치 검사는 정확 검색(`SET enable_indexscan = off`)으로 한다. 지금 규모(약 1만 청크)에서는 정확 검색도 충분히 빠르다.
4. **릴리스는 불변이다.** `rag.chunks`·`rag.embeddings_bge_m3`는 UPDATE를 트리거로 막는다. 바뀐 릴리스는 새 `release_id`로 적재하고 `rag.active_release`가 가리키게 한다. 같은 릴리스를 `load`로 다시 돌리면 해시가 같을 때 아무 일도 하지 않고, 다르면 오류다(`--replace`로만 지우고 다시 적재).
5. **벡터는 릴리스마다 파티션이다.** 한 릴리스를 검색하려면 `WHERE e.release_id = '...'`를 반드시 건다(파티션 제거와 HNSW 사용에 필요).
6. **`rag.chunks.metadata`에 청크의 나머지 필드가 전부 있다**(`document_metadata`, `structure`, `quality`, `provenance`, `representation`…). 열로 꺼낸 것은 검색 필터에 쓰는 필드뿐이다. 행은 `storage.rag_loader.rebuild()`로 `chunks.jsonl` 행과 똑같이 복원된다.
7. **`provenance` 안의 경로는 원본 프로젝트의 절대 경로 그대로다**(`C:\Users\Admin\Documents\biosimilar_collection\...`). 검색에는 쓰이지 않는다. 쪽 이미지·원문 표시는 `(document_id, version_id)` → `corpus.document_versions.raw_path`(상대 경로)로 찾는다. 경로를 바꿔 싣는 일은 하지 않았다(통합 지도 3절의 남은 일).
8. **`corpus`·`registry`는 JSONL 카탈로그의 사본이다.** 새로 수집(`collection.collect`, `promote`)하면 `ingestion.load_catalog`를 다시 돌려야 한다.
9. **`corpus`와의 연결은 외래키가 아니다.** `rag.chunks`의 `(document_id, version_id)`는 `corpus.document_versions`를 가리키지만 제약은 걸지 않았다. `ingestion.load_catalog`가 `corpus`를 비우고 다시 채우는데(TRUNCATE), 외래키가 있으면 그것이 막힌다. 대신 `verify`가 존재를 검사한다.
10. **BM25는 DB로 옮기지 않았다.** 청크는 DB에서 읽고 BM25는 지금 코드(메모리 SQLite FTS5)로 계산한다. 계획 문서 4.2절 5번의 결정 그대로다.

## 5. 질의 예

```sql
-- H 최신 릴리스에서 주어진 청크와 가장 가까운 10개 (내적)
WITH q AS (SELECT embedding FROM rag.embeddings_bge_m3
           WHERE release_id = '03_pipeline_h_v2_table_recovery_2026_10_14' AND chunk_id = :chunk_id)
SELECT c.document_id, c.pdf_page, c.header, (e.embedding <#> q.embedding) * -1 AS score
FROM rag.embeddings_bge_m3 e JOIN rag.chunks c USING (release_id, chunk_id), q
WHERE e.release_id = '03_pipeline_h_v2_table_recovery_2026_10_14' AND c.product_id = 'SB17'
ORDER BY e.embedding <#> q.embedding LIMIT 10;
```

```python
from storage.postgres import connect          # 새 DB 코드는 ingestion.db가 아니라 여기서 import
```

질의 벡터는 BGE-M3로 만든 1024차원 벡터를 `'[0.1,0.2,...]'::vector`로 넘긴다. 이 PC에서는 `rag/embed_index.py`의 `Embedder`가 만든다.

## 6. 만든 것과 확인한 것

| 파일 | 역할 |
| :-- | :-- |
| `ingestion/db/migrations/005_rag.sql` | `rag.releases`, `chunks`, `release_documents`, `embeddings_bge_m3`(파티션, HNSW 내적), `active_release`, 불변 트리거 |
| `storage/rag_loader.py` | `load`(릴리스 한 개 = 한 트랜잭션), `verify`, `list` |
| `storage/handoff/biosim_20261014.dump` | 전달용 덤프 |

2026-10-14 15:22 KST `verify` 결과: 두 릴리스 모두 청크 행 전체와 벡터가 파일과 일치. 정확 검색은 numpy와 300/300 질의에서 집합·순서 모두 같다. HNSW 재현율@10(ef_search=100)은 파일럿 0.992, H 최신 0.992. 덤프를 임시 DB에 복원해 건수·인덱스·질의를 확인한 뒤 삭제했다.

**하지 않은 것**: 검색 경로(`rag/search.py`, `rag/pipeline.py`)와 운영 서비스(8875)를 DB로 바꾸는 일은 하지 않았다(계획상 사용자 승인 후). 질의 벡터를 새로 만드는 평가 질문으로 한 일치 검사도 아직 없다. 지금 검사는 저장된 청크 벡터를 질의로 쓴 것이다.
