"""Small real-model RAG comparison; retain full responses for source review."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rag.pipeline import RAGPipeline

CASES = [
    {'id': 'population', 'agency': 'EU', 'pages': [38],
     'question': 'EMA 문서에서 SB17-1001 건강인 시험의 연령, 체중, BMI 선정 기준은?'},
    {'id': 'pk_table', 'agency': 'EU', 'pages': [43, 45],
     'question': 'EMA SB17-1001에서 SB17과 EU Stelara의 AUCinf 및 Cmax 비와 90% 신뢰구간은?'},
    {'id': 'figure_labels', 'agency': 'EU', 'pages': [44],
     'question': 'EMA 문서에서 Pyzchiva와 EU Stelara의 평균 혈청 농도 시간 곡선 그림의 축과 범례는 어떻게 표시되어 있나?'},
    {'id': 'paginated_table_title', 'agency': 'US', 'pages': [58],
     'question': 'FDA 문서에서 Table 7의 제목과 이 표가 다루는 분석집단은 무엇인가?'}]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--retrieval-pages', type=int, default=5, choices=range(1, 11))
    args = parser.parse_args()
    if args.out.exists(): parser.error('Choose a new output folder')
    args.out.mkdir(parents=True)
    pipeline = RAGPipeline(args.run, args.candidate, retrieval_pages=args.retrieval_pages)
    rows = []
    for case in CASES:
        print(f'Question {case["id"]}', flush=True)
        try:
            response = pipeline.ask(case['question'], products=['SB17'], jurisdictions=[case['agency']])
            (args.out/f'{case["id"]}.json').write_text(json.dumps(response, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            units = response['results']
            pages = [p for unit in units for p in unit.get('retrieved_pages', [])]
            rank = next((i for i, p in enumerate(pages, 1) if p['pdf_page'] in case['pages']), None)
            items = [item for unit in units for item in unit.get('items', [])]
            row = {**case, 'status': response['status'], 'unit_statuses': [u['status'] for u in units],
                'expected_page_rank': rank, 'accepted_items': len(items),
                'rejected_items': sum(len(u.get('validation', {}).get('rejected_items', [])) for u in units),
                'has_review_links': all(any(u['evidence'][eid].get('review_links') for eid in item['evidence'])
                    for u in units for item in u.get('items', [])),
                'seconds': response['seconds'], 'usage': response['usage'],
                'answer_ko': [u['answer_ko'] for u in units]}
        except Exception as exc:
            row = {**case, 'status': 'error', 'error': f'{type(exc).__name__}: {exc}'}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        result = {'created_at': datetime.now(timezone.utc).isoformat(), 'run': str(args.run.resolve()),
            'index_sha256': pipeline.index_meta['chunks_sha256'], 'local_only': True, 'retrieval_pages': args.retrieval_pages,
            'scope': 'Four purposive development questions using real understanding, retrieval and answer; quotation checks do not certify semantic correctness.',
            'rows': rows}
        (args.out/'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
