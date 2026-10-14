# storage — DB 접속·적재 코드 자리

- 작성: 2026-10-14 13:46 KST
- 최종 수정: 2026-10-14 15:30 KST (RAG 릴리스 적재기·인수 안내 추가)

PostgreSQL + pgvector로 통합할 코드가 들어갈 곳이다.

- `postgres.py` — `connect()`, `url()`, `migrate()`, `MIGRATIONS`. 원본 그대로인 `ingestion/db.py`를 다시 내보낸다. **새 DB 코드는 `ingestion.db`가 아니라 여기서 import 한다.** 나중에 접속 코드를 `ingestion/`에서 이 폴더로 옮겨도 호출하는 쪽은 바뀌지 않는다.
- `rag_loader.py` — 릴리스 폴더(`chunks.jsonl` + `emb/bge-m3`)를 `rag.chunks`·`rag.embeddings_bge_m3`에 적재하고(`load`), 파일과 DB를 비교한다(`verify`). 파일럿(01)과 H 최신(03)이 적재돼 있다.
- `handoff/` — 전달용 `pg_dump` 파일(git 제외). 복원 방법과 알아야 할 것은 **[HANDOFF.md](HANDOFF.md)**.
- 스키마 마이그레이션은 계속 `ingestion/db/migrations/`에 번호 순으로 추가한다(`migrate()`가 그 폴더를 읽는다). RAG 테이블은 `005_rag.sql`.
- 다음에 들어올 것(제안): 검색 경로의 DB 구현(`rag/search.py`의 `load_index`·`dense_hits` 대응), 대화·검토·사용량 장부의 DB 구현.

무엇을 어디서 바꾸는지는 [docs/project/PGVECTOR_INTEGRATION_MAP_2026-10-14.md](../docs/project/PGVECTOR_INTEGRATION_MAP_2026-10-14.md)에 정리했다.
