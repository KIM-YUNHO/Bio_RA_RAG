# 임베딩·청킹 구성과 변경 이력 — 현재 서빙 04 기준

- 작성일: 2026-10-19 KST
- 현재 서빙 확인: **2026-10-19 02:05:05 KST**, 8885·8775의 읽기 전용 `GET /api/summary`와 버전 레지스트리. [확인 스냅샷](SERVING_SNAPSHOT_2026-10-19.json)
- 범위: 청크 구성, 임베딩 계약, 저장·조회 방식, 버전별 변경, 확인한 수치와 근거의 한계. 전처리·1차 검색·리랭커의 성능 실험은 [별도 성능 보고서](PERFORMANCE_REVIEW_2026-10-19.md)의 범위다.
- 이번 작업: 기존 문서·코드·산출물 조사와 저장 청크의 직접 집계. 새 임베딩·검색·생성 실험, 색인 변경, 서비스 재시작은 하지 않았다.

## 1. 현재 기준

현재 제품 색인은 **`04_pipeline_h_supplement_2026-10-14`**다. 두 서비스는 `ra-research-rag-4.10.3`, `BAAI/bge-m3`, `pgvector`, 제품 청크 15,682개와 동일 청크 해시를 보고한다. [버전 레지스트리](../../data/versions/versions.json)의 `latest`와 `served_by_ra_service`도 04를 가리킨다. 05는 비교용으로 생성했으나 채택하지 않은 후보다.

| 항목 | 현재 04 |
| :-- | :-- |
| 제품·문서 | 5제품·111문서 |
| 출처 | EMA 34·FDA 52·Health Canada 15·ClinicalTrials.gov 10문서 |
| 원본 형식 | PDF 101·HTML 10문서 |
| 제품 청크·벡터 | 15,682청크, dense 15,682 × 1,024 |
| 문서 메타데이터 기준 기본 검색 청크 | 11,095개. RA 검색은 승인서한 등 `correspondence`도 기본 추가하므로 이 수를 모든 요청의 검색 범위로 해석하지 않는다 |
| 청크 SHA-256 | `2b5684a27f444cfc08a4fe50c1a5047cbf53ee9c9577dd69807b220f25a7d905` |
| 상태의 의미 | 전처리·색인된 범위를 제공하는 `partial`. 전체 코퍼스 완전성이나 수치·의미 정확도 인증을 뜻하지 않음 |

근거: [라이브 확인 스냅샷](SERVING_SNAPSHOT_2026-10-19.json), [현재 색인 설명](../current/03_INDEX_CHUNK_EMBEDDING.md)(12–42행), [04 릴리스 매니페스트](../../data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release/release_manifest.json).

10월 14일 상세 가이드의 **10,088청크, 리랭커 미연결, H v1 검증 실패**는 당시 기록이다. 설계 설명을 참고할 수 있지만 현재 운영 규모·상태에는 이 문서와 위 스냅샷을 사용한다. [과거 기록과 현재 차이](../current/README.md)의 3절에 차이가 정리돼 있다.

## 2. 청킹 계약

| 항목 | 현재 설정·동작 |
| :-- | :-- |
| 청커·스키마 | `preprocessed-chunks-13-closed-row-blocks`, `rag-chunk-3` |
| 절 규칙 | 운영 04는 `sections-8`. 현재 코드의 기본 규칙 `sections-10`과 구분 |
| 토큰 계산 | `BAAI/bge-m3` 토크나이저. 생성 모델 토큰 수와 별도 |
| 본문 크기 | 목표 768·최대 1,024토큰. 768은 고정 길이·최소 길이가 아님 |
| 경계 | 페이지·절·시험·본문/표/그림 경계를 먼저 지킴. 긴 단일 unit은 줄바꿈→문장→공백→문자 위치 순으로 분할하며 마지막 방식은 `hard_split` 기록 |
| overlap | 같은 구조 구간의 온전한 fragment를 최대 96토큰까지 사용. 실제 0~96토큰이며 절 경계에서는 사용하지 않음 |
| 표 | 최대 2,048토큰. 열 헤더 반복, rowspan·닫힌 행 묶음 내부 보존, 원문 행 범위와 반복 문맥 구분 |
| 표의 캡션·각주 | 캡션·단위를 앞에, 각주 등 연결 문맥을 역할에 따라 뒤에 배치. 이 문맥도 2,048토큰 예산에 포함 |
| 큰 표 예외 | 헤더·행 묶음 하나가 이미 상한을 넘거나 안전한 분리가 불가능하면 제외 사유 기록 |
| 그림 | 검색용 텍스트 최대 2,048토큰. 캡션·인쇄 문자 등 허용 표현에서 벡터를 만듦. 원본 이미지·crop·패널 관계는 별도 보존 |
| 전체 임베딩 입력 | 헤더·줄바꿈·본문·특수 토큰을 포함해 8,192토큰 이하. 초과하면 오류를 내고 재청킹하며 근거를 임의로 자르지 않음 |
| 제외 기록 | 미완료 페이지·검색 제외 표현·중복 패널·빈 표현 등은 `chunk_exclusions.jsonl`에 사유 보존 |

표·그림은 본문 overlap을 사용하지 않는다. 필요한 헤더·캡션 반복은 표·그림 문맥 관리 규칙으로 처리한다. 그림 원본을 BGE-M3에 직접 입력하는 이미지 임베딩 경로도 아니다.

실제 04를 구성한 [03 청킹 설정](../../data/versions/03_pipeline_h_v2_table_recovery_2026-10-14/embedded/candidate_final/chunks_summary.json)과 [보충 run 설정](../../pipelines/pipeline_h/runs/supplement_20261014/candidate/chunks_summary.json)이 위 설정을 기록한다. 구현은 [전처리 청커](../../rag/preprocessed_chunks.py), [fragment 분할·packing](../../rag/chunking.py), 설계 설명은 [상세 가이드 4절](../rag/RAG_PIPELINE_CHUNK_EMBEDDING_GUIDE_2026-10-14.md)(108–157행)이다.

### 보존하는 메타데이터

| 분류 | 대표 필드·역할 |
| :-- | :-- |
| 문서·검색 범위 | `document_id`, `version_id`, `source_url`, `product_id`, `jurisdiction`, `doc_group`, `document_metadata` |
| 원문 위치 | `pdf_page`, `pdf_page_label`, `source_refs`, `bbox_pdf` |
| 절·시험 | `section_path`, `section_id`, `heading_anchors`, `study_candidates`. 후보 귀속과 검증 상태는 구분 |
| 표·그림 문맥 | `table_id`, `figure_id`, `member_unit_ids`, `context_unit_ids`, `context_roles` |
| 품질·분할 | `representation`, `quality`, `ocr_status`, `chunking`, 인접 청크 ID |
| 변경 추적 | `content_hash`, `page_hash`, `parsed_sha256` |

`representation_start/end`의 기준은 선택된 전처리 표현 문자열이다. PDF 바이트 위치·화면 좌표는 아니며 PDF 영역은 `source_refs`·bbox로 연결한다. `retrieval_eligible=true`는 검색 공급 허용 상태다. `numeric_evidence_eligible=false`·`semantic_status=unverified`가 별도로 남아 있으므로 문자 인용 일치와 수치·의미 검증을 같은 것으로 보지 않는다. [상세 가이드 5절](../rag/RAG_PIPELINE_CHUNK_EMBEDDING_GUIDE_2026-10-14.md)(174–193행), [전처리 청크 스키마](../../rag/preprocessing_chunk_schema.py).

## 3. 현재 04 청크의 직접 집계

2026-10-19에 [04 `chunks.jsonl`](../../data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release/candidate/chunks.jsonl)을 읽고 저장 필드 `n_tokens`·`n_tokens_with_header`를 집계했다. 토크나이저를 새로 실행한 값은 아니다. 파일 SHA-256은 라이브 스냅샷과 일치했다.

P50은 중앙값이다. P90은 오름차순 배열의 `floor((n-1) × 0.9)` 위치이며, 종류별 본문 통계에는 헤더를 제외했다.

| 종류 (`structure.kind`) | 개수 | 최소 | P50 | P90 | 최대 |
| :-- | --: | --: | --: | --: | --: |
| `text` | 11,387 | 1 | 108 | 594 | 1,023 |
| `table_candidate` | 3,317 | 5 | 282 | 975 | 2,037 |
| `figure_candidate` | 978 | 1 | 8 | 92 | 631 |
| 합계 | **15,682** | — | — | — | — |
| 헤더 포함 전체 입력 (`n_tokens_with_header`) | 15,682 | 32 | 182 | 716 | 2,264 |

본문은 구조 경계 때문에 목표 768토큰보다 짧은 경우가 많다. 특히 그림의 짧은 텍스트 표현이 원본 이미지 전체의 정보량을 뜻하지는 않는다. 최대 입력 2,264토큰은 모델 상한 8,192토큰보다 작다.

`structure.section_path`가 비어 있는 청크는 **4,227/15,682개(26.95%)**다. 잘못 상속된 시험 제목도 저장 헤더·벡터에 남아 있다. 4.10.2 이후 문맥 공급 단계에서 충돌 제목을 차단하더라도 저장 색인의 검색 순위 편향까지 제거한 것은 아니다. [현재 전처리 한계](../current/02_PREPROCESSING.md)(56–62행), [문맥 연결 수정의 적용 범위](../rag/CONTEXT_LINK_FIX_2026-10-16.md)(70–76행).

## 4. 임베딩 계약

| 항목 | 현재 구성 |
| :-- | :-- |
| 모델·로더 | 로컬 `BAAI/bge-m3`, SentenceTransformers |
| pooling·출력 | CLS pooling, dense 1,024차원 |
| 정규화·정밀도 | L2 정규화, CUDA float16 추론, NumPy float32 저장 |
| 문서 입력 | `header + "\n" + text` |
| 헤더 | 제품·기관·문서 제목·절 경로·필요한 캡션·페이지. 이미 표 본문 문맥에 들어간 캡션은 중복을 줄이는 처리 포함 |
| 입력하지 않는 정보 | 문서 ID·검토 정책 등 메타데이터 전체를 직렬화해 넣지는 않음. 이들은 별도 필터·검증에 사용 |
| 추가 prefix | BGE-M3 문서·질의에 별도의 지시문 prefix를 붙이지 않음. 다른 모델용 prefix 코드와 구분 |
| 길이·배치 | 문서 최대 8,192토큰, 질의 최대 512토큰, 기본 배치 8. 긴 입력부터 유사 길이로 배치한 뒤 원래 청크 순서 복원 |
| 외부 임베딩 API | 없음 |
| 사용하지 않는 BGE-M3 출력 | sparse·multi-vector. 키워드 검색은 별도 BM25 |

근거: [현재 색인 설명](../current/03_INDEX_CHUNK_EMBEDDING.md)(64–71행), [04 임베딩 메타데이터](../../data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release/candidate/emb/bge-m3/meta.json)(2–24행), [임베더](../../rag/embed_index.py)(40–47·122–160행), [상세 가이드 6절](../rag/RAG_PIPELINE_CHUNK_EMBEDDING_GUIDE_2026-10-14.md).

### 점수와 동일성

현재 순위 점수는 `vec @ query` 내적이다. 메타데이터의 `similarity`에는 `cosine`이 남아 있지만, fp16 정규화의 반올림 때문에 실제 노름은 약 0.9998~1.0005로 퍼진다. 현재 DB 경로는 파일 내적 순위를 맞추기 위해 `<#>`(음의 내적)·`vector_ip_ops`를 사용한다. 문서 기록상 `<=>` 코사인으로 바꾸면 top-10이 6~8% 질의에서 달라진다. 이를 임베딩 모델 변경 효과와 혼동하지 않는다. [현재 점수 계약](../current/03_INDEX_CHUNK_EMBEDDING.md)(68행).

벡터 재사용은 **전체 `header + 줄바꿈 + text`가 같을 때만** 허용한다. 헤더만 바뀌어도 새 계산 대상이다. `revision`은 `null`이므로 revision 필드만으로 모델 동일성을 판단하지 않고 아래 해시를 함께 보존해야 한다.

| 04 모델 동일성 기록 | 값 |
| :-- | :-- |
| `loaded_state_sha256` | `5b967a9c96c282deab4ed597747d48ce21673ff6bdfa29fe6c49713aa4eb0a82` |
| `tokenizer_sha256` | `a36343fdcf888cc8d4e5d2c7fc72ef9b37cc7ca9679338c7dd18fadd4982eae0` |

근거: [04 메타데이터](../../data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release/candidate/emb/bge-m3/meta.json)(11–24행), [증분 임베딩 구현](../../rag/incremental_embeddings.py), [상세 가이드 재사용 계약](../rag/RAG_PIPELINE_CHUNK_EMBEDDING_GUIDE_2026-10-14.md)(253–263행).

04 메타데이터의 `embed_seconds=6.476`을 전체 15,682청크의 임베딩 시간으로 사용하면 안 된다. `fresh_full_corpus_embedding=false`인 합성 릴리스이며 구성요소의 부분 갱신·재사용 기록을 포함한다. `created_at`도 구성요소에서 이어진 과거 값이다. 전체 실행 시간은 각 run의 로그·메타데이터 범위를 확인해 별도로 계산해야 한다. [04 메타데이터](../../data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release/candidate/emb/bge-m3/meta.json)(26–28·53–70행).

## 5. 버전별 이력과 미채택 후보

| 버전·작업 | 청크 | 변경·상태 |
| :-- | --: | :-- |
| 01 파일럿 | 10,088 | 이전 서비스 색인, 비교·백업용 |
| H 최초 v1 후보 | 11,445 | 릴리스 검증 실패 당시 기록. 이후 완료한 H v2와 구분 |
| 02 H v2 | 11,536 | 92문서 전체 H 실행. 이후 03·04의 기반 |
| 03 연속 표 회수 | 11,536 | 02에서 닫힌 행 2단위 회수. OCR·VLM 호출 없이 보강 |
| **04 보충 병합** | **15,682** | 03 + 보충 19문서·4,146청크. **현재 서비스** |
| **05 절 제목 후보** | **16,725** | 같은 추출본을 `sections-9`로 재청킹. **비교 후 미채택, 미서빙·DB 미적재, 레지스트리 미등록** |
| `sections-10` | 새 색인 없음 | 독립 시험 제목 인식을 보강한 코드. 04를 이 규칙으로 다시 만든 상태는 아님 |

근거: [현재 전처리 계보·05 상태](../current/02_PREPROCESSING.md)(26–54행), [현재 문서가 정리한 과거 기준](../current/README.md) 3절, [과거 H v1 기록](../rag/RAG_PIPELINE_CHUNK_EMBEDDING_GUIDE_2026-10-14.md) 13절, [새 절 규칙과 재현 계약](../rag/CONTEXT_LINK_FIX_2026-10-16.md)(27–31행).

### 04와 05의 직접 비교

05도 같은 방법으로 저장 청크를 직접 집계했다. SHA-256은 `95f2c84be4befc9e9858734176d221aa8ca51de502707e2aed5b1ea032c1e00d`이다.

| 항목 | 현재 04 | 미채택 05 |
| :-- | --: | --: |
| 전체 청크 | 15,682 | 16,725 |
| 본문 청크 | 11,387 | 12,430 |
| 표 청크 | 3,317 | 3,317 |
| 그림 청크 | 978 | 978 |
| 절 경로 없는 청크 | 4,227 (26.95%) | 2,243 (13.41%) |
| 본문 토큰 P50 / P90 / 최대 | 108 / 594 / 1,023 | 93 / 550 / 1,023 |
| 헤더 포함 입력 P50 / P90 / 최대 | 182 / 716 / 2,264 | 171 / 688 / 2,268 |

05에서는 본문 청크가 1,043개 늘었다. 표·그림은 개수와 본문 토큰 분포가 같았다. 절 정보가 더 많이 채워졌다는 구조 지표이며, 제목 귀속의 정확도·검색률·최종 답변 품질 향상을 증명하는 수치는 아니다. [04 저장 청크](../../data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release/candidate/chunks.jsonl), [05 저장 청크](../../data/versions/05_section_titles_2026-10-15/embedded/release/candidate/chunks.jsonl).

| 05 임베딩 구성요소 | 재사용 | 신규 계산 |
| :-- | --: | --: |
| base 03 재청킹 | 5,103 | 7,242 |
| supplement 재청킹 | 1,984 | 2,396 |
| 합계 | **7,087** | **9,638** |

재사용·신규 합계는 05 전체 16,725개와 일치한다. [05 재청킹 보고서](../../data/versions/05_section_titles_2026-10-15/embedded/rechunk_report.json)(24–26·53–55행). 보고서는 청크 구조·입력 변화와 임베딩 재사용량을 기록하며, 05의 검색 품질 비열등성이나 미채택 이유에 대한 정량 결론을 제공하지는 않는다.

## 6. 모델·크기 비교의 근거 한계

현재 확인한 자료에서 BGE-M3 유지 배경은 **로컬 처리, 외부 임베딩 API 없이 운영, 기존 1,024차원 색인·검색 경로 유지**다. 여러 임베딩 모델을 현재 H/04 코퍼스에서 공정 비교해 BGE-M3가 우승했다는 정량 근거는 확인하지 못했다. [발표 내용 근거 검토](../project/PRESENTATION_CONTENT_PLAN_2026-10-16.md)(185–203행).

다만 “임베딩 모델 비교가 전혀 없었다”고도 단정하면 안 된다. [임베더 코드](../../rag/embed_index.py)(23–49행)에는 MedCPT·MedTE·BMRetriever·Qwen3-Embedding이 역사 재현용으로 남아 있고, [관련 연구 검토](../research/RELATED_RESEARCH_REVIEW_2026-10-06.md)(93행)는 이미 비교한 MedCPT 임베딩을 언급한다. 이 자료만으로 과거 비교군·데이터·결과를 복원하거나 현재 서비스의 모델 선정 실험으로 취급할 수는 없다.

| 항목 | 확인한 것 | 아직 뒷받침하지 못하는 결론 |
| :-- | :-- | :-- |
| 임베딩 모델 | 현재 모델·pooling·차원·정규화·입력·해시 | 현 04에서 BGE-M3가 대안 모델보다 우수함 |
| 본문 크기 | 768 목표·1,024 상한, 현재 분포, 역사 평가 도구 | 이 크기와 overlap 96이 현 04에서 최적임 |
| `sections-9` | 빈 절 경로 감소, 본문 분할·입력 변화, 05 생성 | 빈 절 감소가 곧 검색·답변 향상임 |
| H v2 개발 평가 | 전처리·검색·답변 경로의 비교 결과 | 임베딩 모델·차원·정규화 변경의 인과 효과 |
| 출처·벡터 무결성 | 범위·해시·ID·차원·정규화 등의 계약 검사 | 모든 OCR 문자·표 귀속·수치 의미가 정확함 |

[H v2 평가의 해석 범위](../rag/H_V2_RAG_DEVELOPMENT_EVAL_2026-10-14.md)(37·221행)는 임베딩 모델·차원·정규화를 바꾼 실험이 아니며, 당시 실패 분석만으로 청크 크기·정규화·차원을 먼저 바꿀 근거가 부족하다고 명시한다.

[역사 청킹 평가 도구](../../eval/eval_chunking.py)는 512·768·1,024 상한 후보를 만들고 기존 PoC의 캐시 질의·assessment 문서·qrels로 BM25/dense/hybrid를 비교하도록 작성돼 있다. S@1/5/10·nDCG@10·다른 시험 혼입률@5와 출처·벡터 무결성을 검사한다. 승격 가드는 각 kind/form/기관 셀의 S@5 유지, 다른 시험 비율 증가 2%p 이하이며 **통계적 유의성 검정은 아니다**(35–61·93–103·174–204행).

도구의 기본 입력·출력 위치인 `data/rag`는 이번 공유 작업 폴더에 없다. 따라서 도구 존재를 완료된 실험 결과로 간주하지 않았고, 이 도구를 그대로 실행해 현재 04 평가라고 부를 수도 없다. 현재 릴리스·질의·라벨에 맞춘 별도 입력 연결이 필요하다.

## 7. 저장·검색 경로와 재현 자료

서비스는 10월 18일부터 pgvector를 사용한다. 시작할 때 청크를 DB에서 읽어 필터·BM25를 만들고, dense 벡터는 DB에 둔다. 그룹별 dense 상위 40개는 그 그룹에 해당하는 행을 내적으로 전수 계산하는 정확 검색이다. HNSW는 필터 없는 근사 `nearest` 경로용이며 현재 그룹 검색을 HNSW 근사 검색이라고 설명하면 안 된다. BM25는 서비스 프로세스의 SQLite FTS5다. [현재 저장·검색 경로](../current/03_INDEX_CHUNK_EMBEDDING.md)(85–93행), [DB 구현](../../storage/rag_index.py).

| 자료 | 재현·감사 용도 |
| :-- | :-- |
| [04 매니페스트](../../data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release/release_manifest.json) | 구성요소·릴리스·해시 |
| [04 청크 요약](../../data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release/candidate/chunks_summary.json) | 03 + supplement 구성과 산출물 연결 |
| [04 임베딩 메타데이터](../../data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release/candidate/emb/bge-m3/meta.json) | 모델 동일성·차원·부분 재사용 |
| [04 청크 ID](../../data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release/candidate/emb/bge-m3/chunk_ids.json)·[벡터 파일](../../data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release/candidate/emb/bge-m3/doc.npy) | `chunks.jsonl` i번째 줄 = ID i번째 = 벡터 i번째 행 계약 |
| [05 재청킹 보고서](../../data/versions/05_section_titles_2026-10-15/embedded/rechunk_report.json) | 규칙·청크 변화·재사용량 |
| [문맥 수정 검증](../../data/runs/context_link_fix_2026-10-16/verification.json)·[재현 패킷](../../data/runs/context_link_fix_2026-10-16/controlled_context_replays.json)·[출처/코드 해시](../../data/runs/context_link_fix_2026-10-16/provenance.json) | 저장 색인을 바꾸지 않은 문맥 공급 단계 수정 확인 |
| [재청킹 도구](../../pipelines/pipeline_h/rechunk_release.py) | 추출본을 고정한 별도 후보 생성. `--section-rules`로 버전 고정, `--no-publish`로 DB 자동 적재 차단 |

파일 원문 패널·문맥 자료는 계속 필요하다. DB를 사용해도 PDF·추출본·`contexts`·`visual_*` 전체를 없앨 수 있는 구조는 아니다.

### 공통 지침은 별도 계약

공통 지침은 제품 청킹을 그대로 사용하지 않는다. [지침 스냅샷](../../data/guidelines/ra_guidelines_native_2026-10-12_v2/chunks.jsonl)은 native 텍스트 페이지 1개를 청크 1개로 저장하며 임베딩·리랭커 없이 BM25만 사용한다. 저장 21문서·236청크 중 FDA 2015 지침 27청크를 제외해 검색 허용은 20문서·209청크다. 현재 지침 유효성 확인 상태는 `false`다. [현재 지침 계약](../current/03_INDEX_CHUNK_EMBEDDING.md)(95–104행), [지침 상태 레지스트리](../rag/GUIDELINE_STATUS_2026-10-12.json).

## 8. 후속 비교 제안 — 아직 실행하지 않은 항목

임베딩·청킹 개선을 비교하려면 현재 04의 모델·추출본·질의·검색 필터·후보 수·리랭커·입력 예산을 고정하고 아래 변수만 바꾸는 것이 우선이다.

| 우선순위 | 비교 | 당일에 좁혀 볼 범위 | 판단 지표 |
| :-- | :-- | :-- | :-- |
| 1 | `sections-8` 대 `sections-10` | 시험 제목 충돌이 확인된 ABP654 문서 + 기관별 정상 문서 | 제목/시험 귀속 오류, 정답 포함률@40, 상위 8 근거 recall, 잘못된 시험 혼입 |
| 2 | 같은 본문에서 현재 헤더 대 충돌 제목 제거 헤더 | 위 문서의 동일 청크 집합. 경계·본문은 고정 | 검색 순위 변화·정답 순위·혼입률. 헤더 효과를 재청킹 효과와 분리 |
| 3 | 헤더 유무 또는 검증된 절 경로 추가 | 절 정보가 비어 있는 문서 일부와 정상 대조군 | 검색 recall·MRR/nDCG·임베딩 계산량·입력 길이 |
| 이후 | 청크 크기·overlap·모델 교체 | 현재 정답 원문 위치가 확정된 별도 평가셋 확보 후 | 출처 범위 recall, 리랭크 후 recall, 답변 충족률, 지연·GPU 메모리·재색인 시간 |

각 후보는 독립 디렉터리에서 만들고 미서빙 상태로 비교한다. 다른 청크 분할을 비교할 때는 청크 ID 일치율보다 **같은 정답 원문 범위를 회수했는지**를 봐야 한다. 임베딩 모델 교체·절 규칙·본문 크기를 동시에 바꾸면 차이의 원인을 구분할 수 없다. 위 항목은 이번 조사에서 실행한 실험 결과가 아니다.

## 부록. 저장 토큰 통계 재현

프로젝트 루트에서 아래 PowerShell 명령은 04·05 파일을 읽기만 한다. 새 모델 호출·파일 쓰기는 없다. 이번 직접 집계와 같은 중앙값·P90·빈 절 경로 판정을 사용한다.

```powershell
@'
import collections, hashlib, json, math, statistics
from pathlib import Path

def stats(values):
    v = sorted(values)
    return dict(n=len(v), minimum=v[0], p50=statistics.median(v),
                p90=v[math.floor((len(v)-1)*0.9)], maximum=v[-1])

for version in ("04_pipeline_h_supplement_2026-10-14",
                "05_section_titles_2026-10-15"):
    path = Path("data/versions") / version / "embedded/release/candidate/chunks.jsonl"
    kinds, inputs, empty = collections.defaultdict(list), [], 0
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            chunk = json.loads(line)
            kinds[chunk["structure"]["kind"]].append(chunk["n_tokens"])
            inputs.append(chunk["n_tokens_with_header"])
            empty += not bool(chunk["structure"].get("section_path"))
    with path.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    print(json.dumps(dict(version=version, chunks_sha256=sha,
        by_kind={kind: stats(values) for kind, values in kinds.items()},
        with_header=stats(inputs), empty_sections=empty,
        empty_pct=round(100*empty/len(inputs), 2)), ensure_ascii=False))
'@ | .\.venv\Scripts\python.exe -X utf8 -
```
