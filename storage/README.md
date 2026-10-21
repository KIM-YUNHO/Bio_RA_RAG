# storage — DB 접속·적재 코드 자리

- 작성: 2026-10-14 13:46 KST
- 최종 수정: 2026-10-14 16:50 KST (DB 검색 연결, 경로 이식, 데이터 묶음 도구 추가. 15:30 RAG 릴리스 적재기·인수 안내)

PostgreSQL + pgvector로 통합할 코드가 들어갈 곳이다.

- `postgres.py` — `connect()`, `url()`, `migrate()`, `MIGRATIONS`. 원본 그대로인 `ingestion/db.py`를 다시 내보낸다. **새 DB 코드는 `ingestion.db`가 아니라 여기서 import 한다.** 나중에 접속 코드를 `ingestion/`에서 이 폴더로 옮겨도 호출하는 쪽은 바뀌지 않는다.
- `rag_loader.py` — 릴리스 폴더(`chunks.jsonl` + `emb/bge-m3`)를 `rag.chunks`·`rag.embeddings_bge_m3`에 적재하고(`load`), 파일과 DB를 비교한다(`verify`). 파일럿(01)과 H 최신(03)이 적재돼 있다.
- `rag_index.py` — `rag.retrieval.search.load_index`와 같은 `(chunks, vectors, meta)`를 DB에서 읽는다(`BIOSIM_SEARCH_BACKEND=postgres`).
- `serving_data.py` — 웹 화면에 필요한 데이터 파일의 목록·크기·zip(`--zip`). `copy_serving_files.py` — 첫 이관에서 빠진 파일을 원본에서 읽기만 해서 복사.
- `handoff/` — 전달용 `pg_dump` 파일을 두던 곳(git 제외). 10-14 덤프·데이터 묶음은 10-21에 지웠다(내용은 DB `biosim_ra`에 모두 있다). 새로 넘길 때는 `biosim_ra`에서 다시 덤프하고 `storage.serving_data --zip`으로 묶는다. 복원 방법은 **[HANDOFF.md](HANDOFF.md)**.
- 스키마 마이그레이션은 계속 `ingestion/db/migrations/`에 번호 순으로 추가한다(`migrate()`가 그 폴더를 읽는다). RAG 테이블은 `005`~`007`. **적용된 마이그레이션 파일은 고치지 않는다**(`migrate()`가 해시 변경을 오류로 본다). 바꿀 일이 생기면 새 번호를 붙인다.
- 다음에 들어올 것(제안): 질의를 pgvector `<#>`로 직접 보내는 검색(지금은 DB에서 읽어 메모리에서 계산), 대화·검토·사용량 장부의 DB 구현.

무엇을 어디서 바꾸는지는 [docs/project/PGVECTOR_INTEGRATION_MAP_2026-10-14.md](../docs/project/PGVECTOR_INTEGRATION_MAP_2026-10-14.md)에 정리했다.
