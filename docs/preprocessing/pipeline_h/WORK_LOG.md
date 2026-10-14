# 작업 상태

목표: H의 잔여 오류를 일반적인 출처 기반 규칙으로 수정하고, 과거 파이프라인을 보존한 새 폴더에서 승인 92문서를 재전처리한 뒤 기존 전략으로 청킹·BGE-M3 임베딩을 완료한다.

## 확정 사항

- 사용자 범위 응답: 기존92문서로 진행. `data/runs/corpus_update_2026-10-09/corpus_plan.json`이 승인 목록이며 평가 보류9문서 제외.
- 운영 프로젝트는 현재 `biosimilar_collection`이다. 별도 `Biosimilar_RA_Project`의 ra_prep은 비교에 사용한 알고리즘의 원본이며 그쪽 색인 전략으로 교체하지 않는다.
- 기존 운영 색인은 `data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2`, BGE-M3 1024차원, cosine, 10,088벡터. 원래 입력/서비스 파일은 변경하지 않는다.
- 청킹은 `rag/preprocessed_chunks.py`, `rag/chunking.py`, `rag/build_chunks.py`의 기존 768/1024/2048·overlap96·원문 범위/구조 전략을 재사용한다.
- 문서 metadata/절/표·그림/캡션·각주/본문 참조/연속 표의 출처 계약을 보존하도록 사용자 강조가 추가됐다.

## 현재 확인한 오류

- HC 후보 라벨 `therapy $^{a}$`, `therapies $^{a}$`를 비교 정규화가 therapya/therapiesa로 붙여 라벨 보존율을 떨어뜨림. 원문 표기는 바꾸지 않고 검증용 단어 정규화에서 일반 단어의 각주 superscript를 구분해야 함.
- 일반 OCR의 Fcγ 수용체 표기가 FcyR/로마숫자 혼동으로 읽힌 사례. 값 치환으로 정답을 넣지 않고 의심 셀을 원본 crop/VLM OCR 재판독해 근거를 저장해야 함.

## 다음 작업

1. 원래 intake·extraction·visual grouping·chunk contract의 정확한 adapter 입력을 구성한다.
2. HC 표기 검증과 의심 기호 셀 재판독을 새 `pipeline_h` 코드에 구현하고 기존15쪽 회귀 확인한다.
3. 신규 run에 92문서 scope·원문 해시·기존코드/운영 색인 보호 manifest를 생성한다.
4. Docling 기본 추출 → 독립PP영역 검출 → H선별VLM 및 canonical assets/units → 기존청킹 → 기존BGE-M3 임베딩 → 무결성/범위/원문 연결 확인.
5. 문서·단계별 checkpoint와 오류를 남기며 전체 완료까지 재개한다.

## 구현·검증 진행 (2026-10-14)

- `prepare.py`: 기존 승인92문서/4160쪽/5제품을 고정했다. 기존 문서 객체·원문/파생본 SHA·catalog 메타데이터를 보존하며 전체 catalog/product 스냅샷도 저장한다. 보호한 이전 코드·DB·서비스 포인터·벡터 파일189개 변경 없음 확인.
- `docling_worker.py`: 지속 GPU 작업과 문서별 체크포인트. 현재 전체 데이터 처리 중이며 중간 대화로 작업이 끊기지 않도록 별도 숨김 프로세스로 실행한다.
- `docling_runtime.py`: 변환/일반 OCR/재판독 단계·페이지 진행을 기록한다. 문서10 완료 체크포인트에서 재개해, 변환과 독립 OCR 사이에 사용하지 않는 PyTorch CUDA 캐시를 해제하도록 했다. 모델·crop 배율·판정 규칙은 유지한다. `checkpoint_restart.json` 참조.
- `policy.py`/`selection.py`: H의 표·그림 보존 규칙, 각주 단어 검증 수정, 복수 의심 기호는 전체 표 문맥 재판독. 기호만 수정하는 경우 행·열과 주변 문구·R131/V158 등을 검사하며 그 셀만 반영하고 다른 수치 셀은 보존한다.
- 셀 OCR 4회는 γ/y·I/l 혼동을 해소하지 못해 거절했다. 실제 원문 전체 표의 별도 CPU 1회에서는 네 기호 셀을 읽어냈고 보존 검사를 통과했다. 원문 crop·원응답·각 gate는 `runs/glyph_*_regression`에 있다. 이 CPU 시간은 운영 GPU 속도 비교에 포함하지 않는다.
- `metadata_tools.py`: 원래 기관명/문서 유형은 보존하며 보정 규칙 입력만 정규화한다. 대소문자/분류 어휘 차이로 발화자가 unknown이 되는 문제를 수정했다. 발화자는 heuristic_unverified로 기록한다. PDF 물리 쪽, PDF 내부 라벨, 인쇄 footer 후보를 구분한다.
- `caption_recovery.py`/`figure_literals.py`: Docling이 picture에만 붙여 별도 블록으로 내보내지 않은 캡션을 실제 원문 라인 bbox와 연결한다. 그림 설명은 생성하지 않고 캡션/인쇄 문자/본문 참조를 보존한다.
- `export.py`: 기존 extraction/source-unit/visual-context 계약으로 내보낸다. 원시 구조·원시 셀·변경 전 primary·후보 응답/거절/선택 이력·DOM locator를 함께 저장한다. 회전 PDF 한 쪽의 좌표 변환 검증 완료.
- `chunks.py`: 현재 운영의 768/1024/2048·overlap96·구조 경계 전략을 그대로 사용하고 parser 출처 이름을 실제 H 도구로 수정한다. 잘못된 과거 Qwen parser 이름을 이어받지 않는다.
- `run.py`/`finish.py`: 남은 PP 검출 → H 선별 VLM → extraction 검증 → 기존 청킹 → 동일 입력 벡터 재사용/새 로컬 BGE → 92문서·메타데이터·벡터·보호 파일 검사 → 별도 release/완료 보고서를 자동으로 이어간다.
- 회귀8개 통과. HC 짧은 문서의 전처리→메타데이터→청킹 연결 통과. 원문4쪽(표6개·그림2개·논리 그룹2개·회전 페이지)의 source/bbox/group/chunk 검증 통과. 최신 fixture는 `runs/latest_smoke_visual.json` 참조.
- 기존 H 15쪽 + 저장 HC 후보 재검증 + 새 CPU 전체표1회에서 선택 셀만 반영한 부분 재검증은 셀90/90, 표18/18, 그림4/4. 전체 GPU 재실행·독립 표본 정확도·4,160쪽 정확도 측정이 아니다. `runs/ustekinumab_20261014_v1/fix_regression.json` 참조.

전체 작업은 아직 완료 전이다. 현재 단계/완료 문서/실패/문서 내 페이지는 run의 `pipeline_status.json`, `docling_progress.json`, `docling_live_progress.json`이 실제 최신 상태다. 외부 API 호출, 기존서비스 재시작, 기존색인 교체는 하지 않았다.

## 2026-10-14 10:32 처리 속도 수정

- 전체 Docling 92문서/4,160쪽, PP-DocLayoutV3 4,160쪽 완료. 선별 결과는 기존 표 수정142개, 추가 후보804개, 개별 기호3개로 총949개다. 추가 후보는 실제 누락 표가 확정된 수가 아니다.
- 직렬 VLM 평균 약23초에 단순 외삽했던 전체 완료16~18시는 GPU 미사용 때문이 아니었다. nvidia-smi에서 이 작업 프로세스를 확인했고 모델 내부도 CUDA:0/BF16/SDPA였다. 8GB RTX4070 Laptop 사용.
- 응답74개 저장 후 이 작업 worker만 종료코드72로 재개했다. `acceleration_supervisor.py`가 기존 controller 종료를 확인하고 배치 시험 후 자동 재개했다. 이전의 error72는 이 의도된 재시작 기록이다.
- 표4개 시험: 저장 단건 합126.6777초, batch2 48.7341초, batch4 36.2112초. 실제 모델 process 배치2/4 확인. 네 표의 파싱 셀·숫자 multiset·gate 판정 동일. batch4 peak torch reserved2.72GiB. 전체 표본/반복평균은 아니다.
- 신규 `vlm_batch.py`의 batch padding/출처 대응 검사를 적용. 입력 crop·가중치·해상도·4096출력 상한·선별 정책 유지. 이미지 면적 순으로 묶고 오류 시 배치를 줄여 재시도한다.
- 동일 task/crop SHA 조합777개, 중복172개. 동일 이미지 원응답 재사용을 추가하고 각 문서의 job/source와 원응답 재사용 path/SHA를 보존하며 gate는 각각 적용한다.
- 기존 정책8개 및 배치 출처 정합성3개 검사 총11개 통과. 본 작업은 batch4로 재개되어 10:32 현재112/949 영역, 호출 실패0. 이후 상태는 live progress를 기준으로 한다.

## DOM·좌표 보완 및 임베딩 갱신 완료

- v1 청킹11,445개·벡터가 완료됐으나19쪽의 invalid bbox로 최종 검증 중단. 특히 parent page partial 상태 때문에 해당 페이지의 정상 본문도 chunker가 제외하고 있었다.
- `html_source.py`는 원본 DOM 단위를 직접 읽고 제목/목록/표·병합/과학 표기/원문 범위를 보존한다. `html_preview.py`는 DOM 단락 단위로 실제 배치를 측정해 올바른 미리보기 PDF와 좌표를 공급한다. 모델 OCR에서 내용을 다시 생성하지 않는다. 원본 HTML10문서의1,022단위 모두 보존했고, 미리보기의 본문·표 셀 literal 대조에서 누락0개였다.
- `source_router.py`, worker/selection/export에 형식별 경로를 연결했다. 문서명·제품·페이지 ID로 분기하지 않는다. 기존 체크포인트에 다른 원문 프레임을 조용히 섞지 않는다.
- `repair_extraction.py`는 v1을 보존하며 v2의 새 전처리를 만들었다. PDF82문서는 기존 결과를 재사용, HTML10문서는 DOM 경로로 교체. 문자1개 native bbox 복구, 나머지16개 작은 좌표 경고를 원문/원시 block/미리보기와 보존해 page 전체 제외를 방지했다.
- `source_contract.py`는 원본 DOM range/내용·원래 문서 메타데이터·representation 처리 내역을 확인한다. 별도 `html_coverage.py`가 원본 text node1,835개를 독립 선택해 추출에서 미포함0개를 확인했다.
- 새 DOM 출처 필드는 새 폴더의 runtime adapter가 청크 schema에 추가한다. 초기 schema의 extra-field 거절을 보완했으며 기존 disk 원본은 그대로다.
- chunker의 반복 전체 unit 검색만 페이지별 인덱스로 대체했다. 기존4쪽/22청크 출력 전체 동일 확인. 정책/출처/DOM18개 검사 통과.
- v2 청크11,536개. 임베딩은 v1 완료 candidate 기준으로11,277벡터 재사용/259개 새 계산. 외부 API0. 실제 새 임베딩 단계 약25초.
- HTML 청크의 도구 출처를 실제 HTMLParser/Story로 정정했다. 정정 전후 모든 header+text·chunk ID·벡터 파일이 동일함을 확인하고 새 release를 재구성했다. 이전 재구성본은 v2 안의 history 폴더에 보존했다.
- 최종 ReleaseBundle92문서, 벡터11536x1024, source accounting indexed68092/명시 제외2172. 기존 운영 보호189개와 v1 완료 임베딩 모두 변경 없음. `final_postcheck.json` 통과. 전체 의미 정확도/모든 그림 정답율은 별도 측정하지 않았다.
- 최종 위치: `runs/ustekinumab_20261014_v2/release`, `FINAL_REPORT.md`. 기존 운영 서비스 포인터는 바꾸지 않았다.
