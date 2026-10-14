# 바이오시밀러 근거 조사 PoC: 참고 선행연구

조사 기준: 2026-10-06 KST. 공개 논문·학회 원문 페이지·저자 공식 저장소를 확인했다. 현재 프로젝트의 `PROJECT_DIRECTION_REVIEW_2026-10-06.md`, `RAG_RULE_DEPENDENCE_REVIEW_2026-10-06.md`, README와 RAG 계획을 대조해 적용점을 정리했다.

이 문서는 선별 문헌 검토다. 체계적 문헌고찰이나 전체 논문 재현 실험은 아니다. 아래 **연구 내용**은 출처에서 확인한 내용이고, **프로젝트 적용·한계**는 해당 과제와 로컬 검토 기록을 대조한 판단이다. 기술보고서·프리프린트와 학회·저널 논문을 구분했으며, 성능 수치를 이 PoC의 예상 성능으로 옮기지 않았다. 확인 범위는 절별로 표시했다.

## 1. 우선 읽을 연구와 판단

참고할 연구는 충분하다. 현재 과제는 공개 규제문서를 검색하고, 여러 시험·분석의 수치를 구분해 비교표와 인용 메모를 만드는 것이다. 이를 위해 문서 변환, 임상 근거 구조화, 근거 중심 평가, 적응적 검색을 함께 참고하는 것이 좋다.

| 우선순위 | 연구 | 먼저 확인할 부분 |
|---|---|---|
| 1 | Evidence Inference 2.0 · Joint ICO Extraction | 시험약·비교약·평가변수·결과를 연결하는 문제 정의와 라벨 |
| 2 | QASPER · ALCE | 답을 보지 않고 질문을 만드는 절차, 답변 근거와 주장별 인용 평가 |
| 3 | Docling · PubTables-1M · OmniDocBench | 읽기 순서·표 구조·머리글·원문 위치를 보존하는 중간 표현과 평가 |
| 4 | PaperQA2 · OpenScholar | 근거 수집과 답변을 분리하고, 부족한 근거를 다시 검색하는 방식 |
| 5 | Med.ai ASK | 질문에 맞춰 여러 데이터 조회 도구를 선택하는 제약사 연구 사례 |
| 6 | EMA EPAR–guideline matching · QA-RAG | 이 프로젝트와 가까운 규제문서 활용 선행연구 |

위 순서는 논문의 중요도 순위가 아니라 현재 PoC의 실패 유형과 다음 구현에 대한 우선순위다. 임베딩 모델 비교는 기존 계획에도 있으므로, 이번에는 시험·분석의 의미 연결과 검색 이후 검증을 더 비중 있게 다뤘다.

## 2. 규제문서 활용에 가까운 연구

### 01. EPAR와 EMA 가이드라인의 문단 단위 의미 연결

Bergman et al. **A full-document analysis of the semantic relation between European Public Assessment Reports and EMA guidelines using a BERT language model.** PLOS ONE, 2023.

- 연구 내용: EPAR 1,024개와 EMA 가이드라인 669개를 겹치는 청크로 분할하고 Sentence-BERT 임베딩과 piecewise matching으로 문서 간 의미 관계를 분석했다. 본문의 전처리는 200단어 청크와 50단어 중첩을 기본으로 한다.
- 프로젝트 적용: 특정 제품의 심사 쟁점과 관련 지침 구간을 연결하는 조사 도구의 직접적인 선행사례다. 문서 전체 임베딩 하나 대신 세부 구간을 비교하는 설계를 참고할 수 있다.
- 한계: 논문도 의미 유사도를 지침 준수·제출자료 품질·심사 품질의 직접 지표로 해석하지 말라고 명시한다. 이 연구는 수치 답변이나 기관별 요구사항 판정의 검증이 아니다. 논문에서 비교한 현재 지침과 과거 승인자료의 시점 관계도 별도로 고려해야 한다.
- 확인: 저널 본문의 자료 수집·전처리·방법·논의.
- [논문](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0294560)

### 02. 제약 규제 질의응답용 QA-RAG

Kim and Min. **From RAG to QA-RAG: Integrating Generative AI for Pharmaceutical Regulatory Compliance Process.** arXiv, 2024.

- 연구 내용: FDA 공식 FAQ 1,681개로 미세조정한 모델의 가상 답변과 원 질문을 각각 검색에 사용하고, 후보를 재순위화해 최종 답변을 생성한다. 비교 실험에서는 후보 24개와 최종 문맥 6개를 맞췄다.
- 프로젝트 적용: 한국어 질문을 검색용 영어로 변환하는 현재 경로에, 원 질문 검색과 확장 검색을 함께 유지하는 실험 설계를 참고할 수 있다. 가상 답변은 검색 보조 표현으로 취급하고 최종 근거는 회수한 원문에 둔다.
- 한계: FDA 가이드라인 FAQ 중심이며 바이오시밀러 시험·PK 수치 비교 과제가 아니다. 검색 평가는 Ragas의 모델 판정, 답변 평가는 BERTScore를 사용한다. 이 결과가 수치·CI·비교군의 정확성을 검증했다고 해석하면 안 된다. 이번에는 프리프린트와 공개 본문을 확인했으며 후속 학회·저널 출판 여부는 확인하지 않았다.
- 확인: 공개 본문의 방법·데이터·평가·대조군.
- [논문](https://arxiv.org/abs/2402.01717) · [본문](https://arxiv.org/html/2402.01717v1)

## 3. 데이터 전처리: 텍스트·표·시험 근거 구조화

| 번호·연구 | 연구 내용 | 프로젝트 적용·한계 |
|---|---|---|
| 03. Auer et al., [Docling Technical Report](https://arxiv.org/abs/2408.09869), 기술보고서, 2024 | PDF 변환에 레이아웃 분석과 표 구조 인식 모델을 결합한다. | 일반 텍스트만 남기는 단계에서 블록·읽기 순서·표 구조를 보존하는 파서 후보로 참고한다. 현재 글꼴 복구나 원본 보존 위에 비교 실험을 얹을 수 있다. 규제문서 표의 수치 의미 정확도는 자체 확인이 필요하다. |
| 04. Smock et al., [PubTables-1M: Towards Comprehensive Table Extraction From Unstructured Documents](https://openaccess.thecvf.com/content/CVPR2022/html/Smock_PubTables-1M_Towards_Comprehensive_Table_Extraction_From_Unstructured_Documents_CVPR_2022_paper.html), CVPR, 2022 | 과학논문의 표에 머리글·위치·구조 정보를 부여하고, 탐지·구조 인식·기능 분석을 구분한다. | 숫자 문자열 일치와 숫자의 행·열 귀속을 별도 평가한다. PK 표의 평가변수×비교군×시점 연결에 적합한 출발점이다. 규제문서의 각주·연속 표·시험 조건은 별도 라벨이 필요하다. |
| 05. Wadhwa et al., [Jointly Extracting Interventions, Outcomes, and Findings from RCT Reports with LLMs](https://arxiv.org/abs/2305.03642), MLHC, 2023 | 임상시험 초록에서 시험약·평가변수·비교약(ICO)과 보고된 결과를 instruction-tuned LLM 기반 생성 과제로 함께 추출한다. | 현재 키워드·위치 중심 귀속을 보완할 의미 추출 과제의 선행연구다. 여기에 study/part/analysis, population, timepoint, unit, CI, 근거 구간을 추가하는 것은 이 프로젝트의 확장 제안이다. 논문은 초록 중심의 미세조정 과제이므로 긴 심사 PDF의 표에 바로 적용할 수 있다고 보장하지 않는다. |

확인 범위: 세 논문의 공식 초록·서지 페이지. Docling의 현재 설치·모델·지원 범위를 시험한 것은 아니다.

권장 전처리 실험은 실패 유형을 나눠 원문과 대조하는 방식이다. 스캔, 글꼴 손실, 다단 본문, 이미지 표, 병합 셀, 이어지는 표, 각주에 조건이 있는 표를 포함한다. 문자·숫자 복원 정확도와 함께 다음 묶음의 정확도를 잰다.

`study + analysis + endpoint + comparator + population + timepoint + value + unit + CI + evidence location`

위 묶음은 본 문서의 설계 제안이다. OCR 엔진 두 개의 숫자가 같아도 같은 비교군·분석군에 연결됐는지 따로 확인해야 한다.

## 4. 벤치마크: 검색 성공부터 인용 메모까지

| 번호·연구 | 연구 내용 | 프로젝트 적용·한계 |
|---|---|---|
| 06. [OmniDocBench: Benchmarking Diverse PDF Document Parsing with Comprehensive Annotations](https://arxiv.org/abs/2412.07626), CVPR, 2025; 프리프린트 2024 | 문서 요소·위치·읽기 순서 등을 주석화한 PDF 파싱 벤치마크다. [공식 평가 코드](https://github.com/opendatalab/OmniDocBench)는 텍스트·표·수식·읽기 순서를 나눠 평가한다. | 파서 비교의 평가 항목을 참고한다. 공개 점수와 버전별 데이터 수를 섞지 말고 버전을 고정한다. 제품·시험 귀속과 수치의 임상적 의미는 별도 평가다. |
| 07. Dasigi et al., [QASPER: A Dataset of Information-Seeking Questions and Answers Anchored in Research Papers](https://aclanthology.org/2021.naacl-main.365/), NAACL, 2021 | 1,585개 NLP 논문에 대한 5,049개 질문. 질문자는 제목·초록만 읽고 질문하며, 별도의 응답자가 전체 논문에서 답과 근거를 제공한다. | 현재 정답값에서 고정 문형 질문을 만드는 방식의 편향을 줄이는 절차로 참고한다. 사용자 질문과 근거 라벨을 독립적으로 작성한다. NLP 논문 데이터이므로 규제 도메인 시험셋은 따로 만든다. |
| 08. DeYoung et al., [Evidence Inference 2.0: More Data, Better Models](https://aclanthology.org/2020.bionlp-1.13/), BioNLP Workshop, 2020 | 임상시험 논문에서 특정 시험약·비교약·평가변수에 대한 비교 결과를 추론하고 지지 근거를 식별한다. | 문서에 시험 번호가 등장하는지보다 어떤 비교 결과의 근거인지를 평가하는 과제에 가깝다. 유효성 비교 중심이며 PK 동등성의 GMR·CI 검증과는 구분한다. |
| 09. [BioASQ-QA: A manually curated corpus for Biomedical Question Answering](https://pubmed.ncbi.nlm.nih.gov/36973320/), 2023 | 실제 전문가 정보 요구를 반영한 질문에 관련 문서·근거 구간·정답·요약 답변을 연결한다. | 근거 검색과 짧은 답·조사 메모를 함께 평가하는 라벨 형식을 참고한다. [공식 챌린지](https://bioasq.org/)의 과제 범위는 회차에 따라 달라지므로 데이터·가이드라인 버전을 고정한다. |
| 10. Xiong et al., [MIRAGE / Benchmarking Retrieval-Augmented Generation for Medicine](https://aclanthology.org/2024.findings-acl.372/), Findings of ACL, 2024 | 5개 의료 QA 데이터의 7,663개 질문으로 코퍼스·검색기·생성 모델 조합을 비교하며 MedRAG를 제공한다. | 요소를 하나씩 바꾸는 RAG 대조 실험 설계를 참고한다. 객관식·짧은 의료 QA 중심으로, 심사 PDF의 여러 표를 연결한 비교 메모 성능의 대리 지표로 쓰기에는 한계가 있다. |
| 11. Gao et al., [ALCE / Enabling Large Language Models to Generate Text with Citations](https://aclanthology.org/2023.emnlp-main.398/), EMNLP, 2023 | 검색과 인용 답변 생성을 연결하고 유창성·정확성·인용 품질을 평가한다. | 실제 근거가 주장을 뒷받침하는지, 필요한 주장에 인용이 빠지지 않았는지를 각각 평가하는 출발점이다. 인용 위치가 있어도 해당 수치·기관·분석을 지지하는지 확인해야 한다. |
| 12. Ru et al., [RAGChecker: A Fine-grained Framework for Diagnosing Retrieval-Augmented Generation](https://proceedings.neurips.cc/paper_files/paper/2024/hash/27245589131d17368cccdfa990cbf16e-Abstract.html), NeurIPS Datasets and Benchmarks, 2024 | 검색과 생성 모듈을 세부 지표로 진단하고 사람 평가와의 상관을 검증한다. | 검색 누락, 관련 없는 문맥, 근거를 답에 잘못 사용하는 오류를 분리하는 평가에 참고한다. 자동 판정 지표의 정확도와 이 프로젝트의 수치 허용오차는 별도 검증한다. |

확인 범위: 공식 논문 초록·서지와 BioASQ 공식 과제 설명. OmniDocBench는 공식 저장소의 주석·평가 설명도 확인했다.

이 프로젝트의 벤치마크는 아래와 같이 나눠 설계할 수 있다. 다음 항목은 위 연구를 참고한 자체 평가 제안이며, 특정 공개 벤치마크의 공식 지표를 그대로 옮긴 것은 아니다.

| 평가층 | 정답의 단위 | 권장 측정 |
|---|---|---|
| 전처리 | 블록·표·셀·읽기 순서 | 숫자 보존, 행·열·머리글·각주 연결, 원문 위치 연결 |
| 시험·분석 귀속 | 청크 또는 근거 구간 | 정확한 집합의 일치, 정밀도·재현율, 다른 시험을 잘못 붙이는 비율, unknown 처리 |
| 검색 | 필요한 근거 항목의 집합 | recall@k, 순위 품질, 필요한 비교군·평가변수의 전체 회수율, 다른 시험 근거 혼입 |
| 수치 답변 | 조건이 결합된 관측값 | 값·단위·CI 수준·분모·비교 방향·시험·분석의 일치 |
| 인용·메모 | 개별 주장과 근거 | 인용 지지 여부, 인용 완전성, 결론 과장·누락·충돌 |
| 업무·에이전트 | 완료된 조사 과제 | 사람 검수 포함 시간, 수정량, 비용, 도구 호출 수, 실패·중단 사유 |

정답 페이지가 상위 5개에 하나 들어오는 성공률만으로 여러 수치·조건을 모두 회수했다고 판단하지 않는다. 정답에 도달했더라도 같은 수치가 다른 시험에서 나온 경우는 실패로 잡아야 한다.

현재 AI 초안 정답셋은 독립 전문가 정답과 구분한다. 첫 평가셋을 만들 때 질문 작성자에게 정답값을 제공하지 않고, 별도 라벨러가 지정 버전 원문에서 근거·값·조건을 찾게 한다. 학습·개발·시험을 나눌 때 같은 시험의 EMA·FDA 문서 사본이 서로 다른 분할에 들어가는 누출도 확인한다. 세 제품만으로 제품 일반화를 판단하면 불확실성이 크므로 새 성분의 보류 표본을 추가한다.

## 5. RAG 방법론: 검색·재순위화·추가 검색·관계 연결

| 번호·연구 | 연구 내용 | 프로젝트 적용·한계 |
|---|---|---|
| 13. Jin et al., [MedCPT: Contrastive Pre-trained Transformers with large-scale PubMed search logs for zero-shot biomedical information retrieval](https://academic.oup.com/bioinformatics/article/39/11/btad651/7335842), Bioinformatics, 2023 | PubMed 검색 로그를 활용해 검색기와 재순위화기를 함께 학습한다. | 이미 비교한 MedCPT 임베딩 다음으로 cross-encoder 재순위화의 추가 효과를 시험할 근거다. PubMed 학습은 규제문서·한국어 질의 성능의 보장이 아니다. 논문도 근접한 유전자 명칭을 혼동할 수 있음을 논의한다. |
| 14. Trivedi et al., [IRCoT / Interleaving Retrieval with Chain-of-Thought Reasoning for Knowledge-Intensive Multi-Step Questions](https://aclanthology.org/2023.acl-long.557/), ACL, 2023 | 필요한 다음 검색이 이전에 찾은 정보에 의존하는 다단계 QA에서 검색과 추론 단계를 교차 수행한다. | 초기 심사 지적→추가 분석→최종 판단의 근거 연결, 원 보고서→프로토콜·SAP 보충 검색에 참고한다. 일반 다단계 QA 결과이며 수치 검증·규제 판단 과제는 아니다. |
| 15. Edge et al., [From Local to Global: A Graph RAG Approach to Query-Focused Summarization](https://arxiv.org/abs/2404.16130), 프리프린트 2024, 개정 2025 | 엔티티 그래프와 커뮤니티 요약을 만들고 코퍼스 전체에 대한 요약형 질문에 답한다. | 여러 제품의 심사 쟁점이나 기관별 논리의 공통 패턴을 탐색하는 실험 후보다. 특정 시험 표의 GMR·CI 조회에서 우수하다고 입증한 연구는 아니다. |
| 16. Guo et al., [LightRAG: Simple and Fast Retrieval-Augmented Generation](https://arxiv.org/abs/2410.05779), 프리프린트 2024, 개정 2025 | 그래프·벡터와 저수준·고수준 검색을 결합하고 증분 업데이트를 제안한다. | 제품·시험·분석·쟁점의 관계 탐색을 비교할 후보다. 원문 근거 위치를 유지하고 그래프 추출의 오류·비용을 함께 측정해야 한다. 현재 PoC에서의 개선 효과는 미검증이다. |
| 17. Fang et al., [A critical evaluation of generative query expansion on biomedical literature retrieval](https://academic.oup.com/jamia/article/33/6/1121/8571780), JAMIA, 2026 | 8개 질의 확장 방법·3개 LLM·5개 의료 검색 데이터셋을 비교하고 오류 유형을 분석한다. 확장 효과는 방법·증거 유형에 따라 달라진다. | 질의 번역·확장 후 주제, 분석 차원, 모집단·중재가 바뀌는 오류를 평가할 직접적인 참고다. 근거를 먼저 검색한 뒤 확장하는 방식도 비교한다. 원 논문의 평가 범위는 모든 dense·hybrid 검색 조합을 포괄하지 않는다. |

확인 범위: 공식 초록·서지. MedCPT는 본문의 논의, 질의 확장 연구는 방법·결론의 관련 부분도 확인했다.

권장 대조 실험은 같은 코퍼스·청크·번역·생성 모델을 고정하고 아래 순서로 구성하는 것이다.

1. 기존 BM25 + 벡터 검색 기준선.
2. 기준선 + 재순위화 + 주변 문단·표 제목·각주 확장.
3. 2번 + 구조화 값 조회와 시험·분석 근거 추출.
4. 3번 + 부족한 근거를 다시 찾는 에이전트.
5. 관계·기관별 쟁점 질문에 한해 GraphRAG 또는 LightRAG를 별도 비교.

전처리·귀속·재순위화·생성 모델을 한꺼번에 바꾸면 무엇이 개선을 만들었는지 알기 어렵다. 그래프는 개체·관계·시점·기관의 정확도와 원문 연결을 함께 평가한다.

## 6. AI 에이전트: 문헌 조사와 도구 선택

| 번호·연구 | 연구 내용 | 프로젝트 적용·한계 |
|---|---|---|
| 18. Yao et al., [ReAct: Synergizing Reasoning and Acting in Language Models](https://arxiv.org/abs/2210.03629), ICLR, 2023; 프리프린트 2022 | 정보 수집 행동과 추론을 교차 수행하는 구조를 QA·사실검증·상호작용 과제에서 평가한다. | 검색·원문 조회·구조화 값 조회를 모델이 선택하는 기본 설계로 참고한다. 이 논문 자체가 의료·규제 업무의 검증은 아니다. |
| 19. Skarlinski et al., [PaperQA2 / Language agents achieve superhuman synthesis of scientific knowledge](https://arxiv.org/abs/2409.13740), 프리프린트, 2024 | 문헌 검색·질의에 맞는 근거 수집·인용 답변 생성과 모순 탐색을 평가하며 LitQA2를 제시한다. [저자 공식 저장소](https://github.com/Future-House/paper-qa)는 검색→근거 수집→답변 도구와 반복 호출을 설명한다. | 공개 PDF를 조사해 인용 메모를 만드는 흐름과 가깝다. 요약을 다시 근거로 쓰는 과정에서 수치 조건이 빠지지 않도록 원문 근거 객체를 보존하는 것은 이 프로젝트의 추가 설계다. 제목의 인간 성능 비교를 규제 전문가 대체나 이 PoC 성능으로 일반화하지 않는다. |
| 20. Asai et al., [OpenScholar / Synthesizing scientific literature with retrieval-augmented language models](https://www.nature.com/articles/s41586-025-10072-4), Nature, 2026; 선행 프리프린트 2024 | 전문 검색기·재순위화·자기 피드백을 통한 추가 검색·수정을 결합해 인용 문헌 종합 답변을 만들고 ScholarQABench로 평가한다. | 여러 문서의 근거를 모아 긴 비교 메모를 만드는 과제의 참고다. 답변의 근거 범위·사실성·인용을 함께 평가한다. 바이오시밀러 심사 비교나 PK 표 추출의 검증은 아니다. DOI 경로에 2025가 있어도 최종 저널 발행일은 2026-02-04다. |
| 21. Nguyen et al., [Med.ai ASK: an agentic system for biomedical question answering](https://academic.oup.com/jamia/article/33/6/1134/8559661), JAMIA, 2026 | Johnson & Johnson 연구진이 ReAct 도구 호출 구조 등을 사용해 여러 생의학 지식원·외부 API·NER 도구를 질문에 따라 선택하는 시스템을 평가한다. | 에이전트의 역할을 데이터 조회 도구 선택과 조사에 두는 가까운 제약사 사례다. SQL·PDF 원문·기관별 문서 검색을 공통 근거 형식으로 반환하는 설계에 참고한다. 도구 기반 구조와 내부 사용 보고가 이 프로젝트의 업무 효과나 자유로운 다중 에이전트 구조의 우월성을 입증하지는 않는다. |
| 22. Jin et al., [TrialGPT / Matching patients to clinical trials with large language models](https://pubmed.ncbi.nlm.nih.gov/39557832/), Nature Communications, 2024 | 후보 시험 검색→선정기준별 적격성 판정→시험 순위화로 작업을 나누고 근거 문장·설명·사람 평가·선별 시간을 측정한다. | 프로토콜 점검을 확장할 때 조건별 판정과 근거를 남기는 평가 설계를 참고할 수 있다. 환자-시험 매칭 과제이며 바이오시밀러 시험 비교나 자율 에이전트의 직접 대조 실험은 아니다. |

확인 범위: 공식 초록·서지와 PaperQA 공식 저장소의 알고리즘 설명. OpenScholar는 저널 본문의 방법·평가 개요도 확인했다. Med.ai ASK는 저널의 초록·설계 설명을 확인했으며 내부 데이터·운영 환경을 재현하지 않았다.

에이전트의 첫 구현은 하나의 조사 에이전트와 명확한 도구들로도 가능하다. 여러 역할을 가진 에이전트의 수 자체를 목표로 삼을 필요는 없다. 다음은 이 프로젝트용 도구 계약 제안이다.

| 도구 | 역할 | 결과에 남길 정보 |
|---|---|---|
| search_evidence | 제품·기관·시험·쟁점의 후보 검색 | 원본·파생 버전, 근거 ID, 귀속 후보와 확신도 |
| fetch_context | 앞뒤 문단·표 머리글·각주·이어지는 표 조회 | 실제 원문 위치·발췌, 확장한 이유 |
| lookup_values | 검토된 구조화 값 조회 | 값과 조건, 검토 상태, 해당 원문 근거 |
| compare_evidence | 시험·기관별 비교표 구성 | 셀별 근거와 미충족 항목, 충돌 |
| verify_claims | 수치·비교 방향·인용 지원 확인 | 통과·실패·판정 불가와 확인 대상 |

에이전트는 필요한 평가변수·비교군·시험·기관의 근거 중 무엇이 빠졌는지를 보고 추가 검색한다. 종료 조건은 근거 충족 또는 정해진 비용·호출 예산 소진으로 둔다. 예산을 소진한 미발견과 코퍼스에서 답이 없다는 판정을 구분한다.

고정 순서 워크플로우와 에이전트를 같은 도구·모델로 비교한다. 후보·문맥 예산을 맞춘 실험과 전체 호출 비용을 포함한 성능-비용 비교를 함께 한다. 자동 자기 검증은 원문 대조·독립 검수 라벨과 비교해 정확도를 확인한다.

## 7. 이 프로젝트가 별도로 검증할 연구 과제

이번에 확인한 연구들은 각 구성요소의 설계 근거를 제공한다. FDA·EMA·Health Canada의 심사문서·프로토콜·SAP를 연결해 바이오시밀러 시험별 PK·면역원성 값을 정확히 비교하는 통합 과제를 그대로 검증한 것은 아니다. 이 조사만으로 동일 연구가 전혀 없거나 학술적 신규성이 확보됐다고 결론 내리지는 않는다.

현재 데이터와 검토 기록을 기준으로 다음 질문을 연구 과제로 삼을 수 있다.

- **의미 추출과 규칙 귀속의 차이:** 시험 번호가 본문 주체인지, 비교·제외 대상인지, 통합 분석의 재료인지 구분하면 잘못된 시험 귀속과 수치 혼동이 줄어드는가?
- **표 구조 보존의 효과:** 행·열·제목·각주를 보존한 표현이 평문 OCR보다 정확한 수치·조건 묶음을 더 잘 회수하는가?
- **적응적 검색의 효과:** 근거가 부족할 때만 다시 검색하는 에이전트가 고정 워크플로우보다 검수 포함 과제 완료율을 높이는가? 추가 비용은 얼마인가?
- **관계 검색의 효과:** 그래프가 초기 지적→신청자 설명→보완 근거→최종 판단의 회수에 기여하는가? 단순 수치 조회와 효과가 다른가?
- **일반화:** 새 문서·기관·성분, 동의어·부정·여러 시험 질문에서도 유지되는가? 특정 질문의 표현을 추가한 규칙 개선과 구분할 수 있는가?

이 질문들의 성능·효과는 아직 측정하지 않았다. 첫 실험은 전문가가 검수한 좁은 시험셋에서 수행하고, 질문·근거·코퍼스·모델·코드 버전과 비용을 실행별로 남기는 것이 적절하다.
