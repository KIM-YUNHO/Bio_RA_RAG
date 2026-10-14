"""Freeze and evaluate the live local RAG service without sending gold to it.

The service receives only normal UI request fields. Gold, source references and
later semantic adjudications remain evaluator-only. No tuning is performed.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rag.evidence import contains_quote, contains_value
from rag.table_evidence import contains_cell_value

VERSION = 'live-rag-evaluation-1'
CODE = ['rag/pipeline.py', 'rag/answer.py', 'rag/preprocessed_evidence.py', 'rag/search.py',
        'rag/study_search.py', 'rag/understand_query.py', 'rag/plan_query.py', 'rag/evidence.py',
        'rag/study_context.py', 'rag/study_registry.py', 'rag_service/__main__.py',
        'review_viewer/data.py', 'configs/preprocess.local.json', 'data/reviewed/studies.json',
        'rag/glossary.csv', 'rag/preprocessed_chunks.py', 'rag/translate_query.py', 'rag/llm.py',
        'rag_service/static/app.js',
        'rag/request_scope.py', 'rag/preprocessed_studies.py', 'rag/preprocessing_chunk_schema.py',
        'rag/local_tokenizer.py', 'rag/evidence_budget.py', 'rag/evidence_slices.py',
        'rag/budgeted_evidence.py', 'rag/preprocessed_prompt.py', 'rag/table_evidence.py',
        'rag/table_canonical.py', 'rag/answer_conditions.py', 'rag/local_chat.py',
        'rag/release.py', 'rag/source_selection.py', 'rag/source_context.py', 'rag/service_scope.py',
        'review_viewer/__main__.py', 'eval/current_server_eval.py','rag/document_structure.py',
        'rag/chunk_schema.py','rag/build_chunks.py','rag/context_units.py','rag/clinical_retrieval.py']


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def request(base, path, payload=None):
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = Request(base+path, data=body, headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=10) as response:
        return json.load(response)


def build_cases(summary):
    # Keep the same 22-question SB17 development comparison after composing
    # additional products; product coverage is measured separately.
    documents = {d['document_id']: d for d in summary['documents'] if d['product_id'] == 'SB17'}
    version = {key: d['version_id'] for key, d in documents.items()}
    with (ROOT/'gold/gold_values_v0.2.csv').open(encoding='utf-8-sig', newline='') as stream:
        gold = {r['gold_id']: r for r in csv.DictReader(stream)}
    cases = []
    with (ROOT/'eval/queries_v0.1.csv').open(encoding='utf-8-sig', newline='') as stream:
        queries = list(csv.DictReader(stream))
    for q in queries:
        if q['type'] != 'retrieval' or q['document_id'] not in documents:
            continue
        refs = [gold[g] for g in q['gold_ids'].split(';')]
        if any(r['source_sha256'] != version[q['document_id']] for r in refs):
            raise ValueError('Gold source version differs from live document')
        cases.append({'id': q['query_id'], 'suite': 'historical_dev', 'topic': q['topic'],
            'question': q['ko_a'], 'payload': {'question': q['ko_a'], 'products': ['SB17'], 'jurisdictions': ['EU']},
            'expected': 'answerable', 'gold_pages': json.loads(q['acceptable_pages']),
            'document_id': q['document_id'], 'version_id': version[q['document_id']],
            'gold': refs, 'analysis': q['analysis_id'], 'study': q['study_key'],
            'gold_status': 'AI_DRAFT; evaluator source review required'})
    def doc(prefix):
        preferred={'EMA:':'EMA:63563','FDA:':'FDA:761373Orig1s000,761425Orig1s000:MultidisciplineR',
            'HC:':'HC:PM:00082519','CTG:':'CTG:NCT04967508:Prot_000.pdf'}
        if preferred.get(prefix) in documents:return preferred[prefix]
        matches = [d for d in documents if d.startswith(prefix)]
        if len(matches) != 1: raise ValueError(f'Ambiguous document prefix: {prefix}')
        return matches[0]
    def extra(ident, topic, question, prefix, agency, pages, expected, rubric, include=False, only_doc=False):
        if not any(d.startswith(prefix) for d in documents):return
        document = doc(prefix)
        payload = {'question': question, 'products': ['SB17'], 'jurisdictions': [agency], 'include_nondefault': include}
        if only_doc: payload['documents'] = [documents[document]['key']]
        cases.append({'id': ident, 'suite': 'diagnostic', 'topic': topic, 'question': question,
            'payload': payload, 'expected': expected, 'gold_pages': pages, 'document_id': document,
            'version_id': version[document], 'rubric': rubric, 'gold_status': 'agent-authored diagnostic; not expert gold'})
    extra('D01', 'paginated_table', 'FDA 문서에서 Table 7의 제목과 이 표가 다루는 분석집단은 무엇인가?',
        'FDA:', 'US', [57,58], 'answerable',
        ['Incidences of ADAs and NAbs by Visits & Treatment Group', 'Safety Set 1'])
    extra('D02', 'figure_scope', 'EMA SB17-1001의 Figure 3에서 두 패널의 Y축 단위, 선형/반로그 스케일, 두 치료군 범례는 무엇인가?',
        'EMA:', 'EU', [44], 'answerable',
        ['Concentration (ng/mL)', 'Linear Scale', 'Semi-Logarithmic Scale', 'SB17 45 mg',
         'EU sourced Stelara 45 mg', 'Do not substitute labels from SB17-3001 Figure 4'])
    extra('D03', 'false_premise', 'EMA SB17-1001 건강인 시험의 90 mg 투여군에서 EU Stelara 대비 Cmax 기하평균비와 90% 신뢰구간은?',
        'EMA:', 'EU', [39,43,45], 'abstain_or_correct_premise',
        ['Study used 45 mg/0.5 mL, not a 90 mg arm', 'Do not present 45 mg primary or adjusted results as a 90 mg result'])
    protocol_question = 'SB17-3001 프로토콜의 Figure 1 각주에서 치료 전환 기간은 몇 주에 시작하며, 전환 진입에 필요한 PASI 반응 기준은 무엇인가?'
    extra('D04', 'default_exclusion', protocol_question, 'CTG:', 'sponsor', [], 'scope_excluded',
        ['Explicit protocol-only scope with include_nondefault=false has no eligible evidence'], only_doc=True)
    extra('D05', 'explicit_inclusion', protocol_question, 'CTG:', 'sponsor', [10], 'answerable',
        ['Week 28', 'PASI50', 'Eligibility qualification must be retained'], include=True, only_doc=True)
    extra('D06', 'japanese_table', 'PMDA 심사보고서 표 7에서 본제, EU 승인품, 미국 승인품의 Cmax 평균과 표준편차 및 단위는?',
        'PMDA:', 'JP', [12], 'answerable',
        ['本剤 5.095 ±1.498 μg/mL', 'EU 承認品 5.689 ±1.877 μg/mL', '米国 承認品 5.420 ±1.659 μg/mL'])
    extra('D07', 'label_table', 'Health Canada 제품설명서에서 건선 성인에게 우스테키누맙 45 mg과 90 mg을 단회 피하 투여했을 때 Cmax 중앙값과 평균은?',
        'HC:', 'CA', [27], 'answerable',
        ['Copy four Cmax values with dose, median/mean and units', 'These label data are not automatically an SB17-1001 result'], include=True, only_doc=True)
    q = next(q for q in queries if q['query_id'] == 'Q013')
    extra('D08', 'paraphrase', q['ko_b'], 'EMA:', 'EU', json.loads(q['acceptable_pages']), 'answerable',
        ['Primary Pyzchiva vs EU Stelara AUCinf: 0.99, 90% CI 0.90 to 1.08',
         'Primary Cmax: 0.90, 90% CI 0.82 to 0.98', 'Preserve comparison and endpoint'])
    return cases


def score(case, report):
    units = report.get('results', [])
    locations = [(p['document_id'], p.get('version_id'), p['pdf_page'])
                 for u in units for p in u.get('retrieved_pages', [])]
    rank = next((i for i, (d,v,p) in enumerate(locations,1) if d == case['document_id']
                 and v == case['version_id'] and p in case['gold_pages']), None)
    items = [(u, it) for u in units for it in u.get('items', [])]
    literal, links, identity = 0, 0, 0
    for unit, item in items:
        blocks = [unit.get('evidence', {}).get(e) for e in item['evidence']]
        valid = bool(blocks) and all(b and contains_quote(b['text'],item['quote']) for b in blocks)
        cell_checked = item.get('value_validation', {}).get('method') == 'within_single_html_cell'
        literal += bool(valid and (contains_value(item['quote'], item['value'])
            or (cell_checked and contains_cell_value(item['quote'], item['value']))))
        links += bool(blocks) and all(b and b.get('review_links') for b in blocks)
        identity += bool(blocks) and all(b and b['document_id'] == case['document_id']
            and b.get('version_id') == case['version_id'] for b in blocks)
    repair = [u.get('validation', {}).get('repair') for u in units]
    return {'id': case['id'], 'suite': case['suite'], 'topic': case['topic'], 'expected': case['expected'],
        'status': report.get('status'), 'unit_statuses': [u['status'] for u in units],
        'first_gold_page_rank': rank, 'gold_page_hit@5': rank is not None and rank <= 5 if case['gold_pages'] else None,
        'gold_in_answer_context': any(b['document_id'] == case['document_id'] and b.get('version_id') == case['version_id']
            and b['pdf_page'] in case['gold_pages'] for u in units for b in u.get('evidence', {}).values()) if case['gold_pages'] else None,
        'items': len(items), 'literal_valid_items': literal, 'items_with_review_links': links,
        'items_with_expected_source_version': identity,
        'rejected_items': sum(len(u.get('validation', {}).get('rejected_items', [])) for u in units),
        'repair_attempts': sum(r.get('attempts', 0) for r in repair if r),
        'repair_accepted': sum(bool(r.get('accepted')) for r in repair if r),
        'seconds': report.get('seconds'), 'usage': report.get('usage'),
        'semantic_verdict': 'pending_evaluator_review; literal validity is not semantic correctness'}


def summarize(rows):
    scored = [r for r in rows if r.get('expected') == 'answerable']
    ranks = [r.get('first_gold_page_rank') for r in scored]
    items = sum(r.get('items', 0) for r in rows)
    times = sorted(r['seconds'] for r in rows if r.get('seconds') is not None)
    def quantile(p):
        if not times: return None
        position = (len(times)-1)*p; low = int(position); high = min(low+1,len(times)-1)
        return round(times[low]+(times[high]-times[low])*(position-low),3)
    return {'n': len(rows), 'answerable_n': len(scored),
        'page_hit@5': sum(r is not None and r <= 5 for r in ranks)/len(ranks) if ranks else None,
        'mrr@5': sum(1/r for r in ranks if r is not None and r <= 5)/len(ranks) if ranks else None,
        'gold_in_answer_context_n': sum(bool(r.get('gold_in_answer_context')) for r in scored),
        'accepted_items': items, 'literal_valid_items': sum(r.get('literal_valid_items',0) for r in rows),
        'literal_precision': sum(r.get('literal_valid_items',0) for r in rows)/items if items else None,
        'items_with_review_links': sum(r.get('items_with_review_links',0) for r in rows),
        'status_counts': dict(Counter(r['status'] for r in rows)),
        'latency_seconds': {'p50':quantile(.5),'p95':quantile(.95),'mean':sum(times)/len(times) if times else None},
        'input_tokens': sum((r.get('usage') or {}).get('input_tokens',0) for r in rows),
        'output_tokens': sum((r.get('usage') or {}).get('output_tokens',0) for r in rows),
        'repair_attempts': sum(r.get('repair_attempts',0) for r in rows),
        'semantic_scoring': 'Separate source-based agent adjudication required; no LLM judge or expert certification'}


def run(base, out):
    parsed = urlsplit(base)
    if parsed.scheme != 'http' or parsed.hostname not in ('localhost','127.0.0.1','::1') or parsed.path:
        raise ValueError('Only a local HTTP service is allowed')
    summary = request(base, '/api/summary')
    out.mkdir(parents=True, exist_ok=True)
    freeze_path = out/'frozen.json'
    code = {name:digest(ROOT/name) for name in CODE if (ROOT/name).is_file()}
    if freeze_path.exists():
        freeze = read(freeze_path)
        if freeze['code_sha256'] != code or freeze['server_summary'] != summary:
            raise ValueError('Frozen pipeline or live index changed')
        cases = read(out/'cases.json')
        if digest(out/'cases.json') != freeze['cases_sha256']: raise ValueError('Frozen cases changed')
    else:
        if any(out.iterdir()): raise ValueError('Output must be empty')
        cases = build_cases(summary)
        write(out/'cases.json',cases)
        freeze = {'version':VERSION,'created_at':datetime.now(timezone.utc).isoformat(),
            'base_url':base,'server_summary':summary,'code_sha256':code,
            'cases_sha256':digest(out/'cases.json'), 'case_count':len(cases),
            'source_label_sha256':{n:digest(ROOT/n) for n in ['eval/queries_v0.1.csv','gold/gold_values_v0.2.csv']},
            'scope':'Current SB17 5-document development and agent-authored diagnostic suite, not independent holdout or expert gold.',
            'policy':'No pipeline tuning during run; no gold sent in requests; unchanged service and index required'}
        write(freeze_path,freeze)
        for name in code:
            dest = out/'code'/name; dest.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(ROOT/name,dest)
        try:
            tags = request('http://127.0.0.1:11434','/api/tags')
            write(out/'model_inventory.json',{'models':[m for m in tags.get('models',[]) if m['name']==summary['answer_model']]})
        except (OSError,URLError) as exc: write(out/'model_inventory.json',{'error':str(exc)})
    rows = []
    for number, case in enumerate(cases,1):
        response_path = out/f"responses/{case['id']}.json"
        response_path.parent.mkdir(exist_ok=True)
        if response_path.exists():
            report = read(response_path)
            if report['question'] != case['question'] or report['index']['chunks_sha256'] != summary['chunks_sha256']:
                raise ValueError('Saved response does not match frozen input')
        else:
            print(f"[{number}/{len(cases)}] {case['id']} {case['topic']}",flush=True)
            write(out/f"responses/{case['id']}.request.json",case['payload'])
            started = time.monotonic()
            while True:
                try:
                    job = request(base,'/api/questions',case['payload']); break
                except HTTPError as exc:
                    if exc.code != 409 or time.monotonic()-started > 600: raise
                    print('Server busy; waiting for existing job',flush=True); time.sleep(5)
            write(out/f"responses/{case['id']}.job.json",job)
            while True:
                job = request(base,f"/api/jobs/{job['id']}")
                if job['state'] in ('completed','failed'): break
                if time.monotonic()-started > 1200: raise TimeoutError('Live question exceeded 20 minutes')
                time.sleep(2)
            write(out/f"responses/{case['id']}.job.json",job)
            if job['state'] == 'failed': raise RuntimeError(job.get('error','Server job failed'))
            report = job['report']
            if report['index']['chunks_sha256'] != summary['chunks_sha256']: raise ValueError('Live index changed during request')
            write(response_path, report)
        row = score(case,report); rows.append(row)
        write(out/'automatic.json',{'freeze':freeze,'summary':summarize(rows),'rows':rows})
        print(json.dumps(row,ensure_ascii=False),flush=True)
        if request(base,'/api/summary') != summary: raise ValueError('Live server changed during evaluation')
    if code != {name:digest(ROOT/name) for name in code}: raise ValueError('Pipeline code changed during evaluation')
    write(out/'completed.json',{'completed_at':datetime.now(timezone.utc).isoformat(),
        'n':len(rows),'live_server_unchanged':True,'code_unchanged':True,
        'all_requests_exclude_gold':True,'summary':summarize(rows)})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8765')
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    run(args.base_url.rstrip('/'),args.out)
