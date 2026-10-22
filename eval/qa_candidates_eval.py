#!/usr/bin/env python3
"""Golden-set evaluation of the live local RA service with qa_candidates.jsonl (WEZENLA FDA ChemR, v0.2-candidates).

Per question: send only the normal UI fields to /api/questions (never the gold), save the full report, then ask a
judge model whether each `required_facts` entry is supported by the service's answer. Verdicts are a FIRST PASS
(model judge on unreviewed candidate questions); read the answers of the failures before trusting a number.

    .venv\\Scripts\\python.exe -X utf8 eval/qa_candidates_eval.py --base-url http://127.0.0.1:9100 \\
        --qa handoff_q008/qa_candidates.jsonl --out data/runs/qa_eval/smoke --sample
Re-running with the same --out resumes: saved responses and judgments are reused.
"""
import argparse
import csv
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

JUDGE_SCHEMA = {'name': 'fact_judgment', 'schema': {
    'type': 'object', 'additionalProperties': False,
    'required': ['facts', 'contradiction', 'abstained'],
    'properties': {
        'facts': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['fact_id', 'met', 'reason'],
            'properties': {'fact_id': {'type': 'string'}, 'met': {'type': 'boolean'}, 'reason': {'type': 'string'}}}},
        'contradiction': {'type': 'boolean'},
        'abstained': {'type': 'boolean'}}}}

JUDGE_SYSTEM = (
    'You grade an answer from a regulatory-document QA system against reference facts. '
    'For each required fact decide whether the SYSTEM ANSWER states it, with the same numbers, units, scope and '
    'judgement; wording may differ and Korean/English mixing is fine. A fact is met only if the answer itself '
    'contains it (not merely a citation to the right place). Set contradiction=true if the answer states something '
    'that conflicts with the reference answer. Set abstained=true if the answer says the information was not found '
    'or gives no substantive answer. Keep each reason under 25 words. Return JSON only.')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def http(base, path, payload=None, timeout=30):
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = Request(base + path, data=body, headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=timeout) as response:
        return json.load(response)


def load_questions(path):
    rows = [json.loads(line) for line in Path(path).read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    if len({r['qa_id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate qa_id')
    return rows


def pick_sample(rows):
    """One question per question_type, plus the first complex-table question."""
    chosen, seen = [], set()
    for r in rows:
        if r['question_type'] not in seen:
            seen.add(r['question_type']); chosen.append(r)
    extra = next((r for r in rows if r.get('qa_pattern') == 'complex_table_relations' and r not in chosen), None)
    return chosen + ([extra] if extra else [])


def ask(base, payload, wait=1200):
    started = time.monotonic()
    while True:
        try:
            job = http(base, '/api/questions', payload); break
        except HTTPError as exc:
            if exc.code != 409 or time.monotonic() - started > 600: raise
            time.sleep(5)
    while True:
        job = http(base, f"/api/jobs/{job['id']}")
        if job['state'] in ('completed', 'failed'): return job
        if time.monotonic() - started > wait: raise TimeoutError('Question exceeded the time limit')
        time.sleep(2)


def answer_view(report):
    """Answer text and the cited sources of a service report."""
    units = report.get('results', [])
    text = '\n\n'.join(u.get('answer_ko', '') for u in units if u.get('answer_ko'))
    refs, quotes = [], []
    for u in units:
        evidence = u.get('evidence', {})
        for item in u.get('items', []):
            quotes.append(item.get('quote', ''))
            for e in item.get('evidence', []):
                block = evidence.get(e) or {}
                ref = {'document_id': block.get('document_id'), 'pdf_page': block.get('pdf_page')}
                if ref not in refs: refs.append(ref)
    return text, refs, quotes


def judge(question, text, quotes, model):
    from rag.llm.llm import chat
    facts = [{'fact_id': f['fact_id'], 'text': f['text']} for f in question['required_facts']]
    user = json.dumps({'question': question['question'], 'reference_answer': question['gold_answer'],
                       'required_facts': facts, 'system_answer': text,
                       'quoted_source_snippets': quotes[:12]}, ensure_ascii=False)
    reply = chat([{'role': 'system', 'content': JUDGE_SYSTEM}, {'role': 'user', 'content': user}],
                 json_schema=JUDGE_SCHEMA, model=model)
    verdict = json.loads(reply['text'])
    by_id = {f['fact_id']: f for f in verdict['facts']}
    met = [bool(by_id.get(f['fact_id'], {}).get('met')) for f in facts]
    return {'facts': [by_id.get(f['fact_id'], {'fact_id': f['fact_id'], 'met': False, 'reason': 'not judged'})
                      for f in facts],
            'contradiction': verdict['contradiction'], 'abstained': verdict['abstained'],
            'judge_model': reply['model_reported'], 'judge_tokens': [reply['prompt_tokens'], reply['completion_tokens']],
            'verdict': 'pass' if all(met) and not verdict['contradiction'] else ('partial' if any(met) else 'fail'),
            'facts_met': sum(met), 'facts_total': len(met)}


def summarize(rows):
    def rate(sub):
        return {'n': len(sub), 'pass': sum(r['verdict'] == 'pass' for r in sub),
                'partial': sum(r['verdict'] == 'partial' for r in sub), 'fail': sum(r['verdict'] == 'fail' for r in sub),
                'facts_met': sum(r['facts_met'] for r in sub), 'facts_total': sum(r['facts_total'] for r in sub)}
    groups = {}
    for key in ('question_type', 'split', 'qa_pattern', 'difficulty'):
        for value in sorted({r[key] for r in rows}):
            groups[f'{key}={value}'] = rate([r for r in rows if r[key] == value])
    times = sorted(r['seconds'] for r in rows if r.get('seconds') is not None)
    return {'overall': rate(rows), 'by_group': groups,
            'service_status': {s: sum(r['service_status'] == s for r in rows) for s in {r['service_status'] for r in rows}},
            'latency_seconds': {'median': times[len(times) // 2] if times else None, 'max': times[-1] if times else None},
            'service_tokens': {'input': sum((r.get('usage') or {}).get('input_tokens', 0) for r in rows),
                               'output': sum((r.get('usage') or {}).get('output_tokens', 0) for r in rows)},
            'note': 'Model-judged first pass on unreviewed candidate questions; not certified accuracy.'}


def run(args):
    parsed = urlsplit(args.base_url)
    if parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', '::1') or parsed.path:
        raise ValueError('Only a local HTTP service is allowed')
    base = args.base_url.rstrip('/')
    summary = http(base, '/api/summary')
    questions = load_questions(args.qa)
    if args.ids:
        wanted = set(args.ids.split(','))
        questions = [q for q in questions if q['qa_id'] in wanted]
    elif args.sample:
        questions = pick_sample(questions)
    if args.split:
        questions = [q for q in questions if q['split'] == args.split]
    out = Path(args.out); (out / 'responses').mkdir(parents=True, exist_ok=True)
    try:
        commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True,
                                text=True).stdout.strip()
    except OSError:
        commit = None
    write_json(out / 'run.json', {
        'created_at': datetime.now(timezone.utc).isoformat(), 'base_url': base, 'git_commit': commit,
        'pipeline_version': summary.get('pipeline_version'), 'answer_model': summary.get('answer_model'),
        'run_name': summary.get('run_name'), 'product_index_chunks': summary.get('product_index_chunks'),
        'qa_file': str(args.qa), 'qa_sha256': digest(args.qa), 'dataset_version': questions[0].get('dataset_version'),
        'question_ids': [q['qa_id'] for q in questions], 'scope': ('all products and agencies (screen default)' if args.all_scope else
                  {'products': [args.product], 'jurisdictions': [args.jurisdiction]}), 'judge_model': args.judge_model or 'project default (.env)',
        'no_judge': args.no_judge})
    rows = []
    spent_input = 0
    for number, q in enumerate(questions, 1):
        qid = q['qa_id']
        if args.all_scope:
            # What the screen sends with nothing narrowed: every product and agency of /api/summary.
            payload = {'question': q['question'], 'products': summary['products'],
                       'jurisdictions': summary['jurisdictions'], 'include_nondefault': False}
        else:
            payload = {'question': q['question'], 'products': [args.product], 'jurisdictions': [args.jurisdiction]}
        if args.document_key:
            payload['documents'] = args.document_key.split(',')
        response_path = out / 'responses' / f'{qid}.json'
        if args.max_input_tokens and spent_input >= args.max_input_tokens and not response_path.exists():
            print(f'Stopped before {qid}: {spent_input} input tokens reach --max-input-tokens', flush=True)
            break
        if response_path.exists():
            job = read_json(response_path)
        else:
            print(f"[{number}/{len(questions)}] {qid} ({q['question_type']})", flush=True)
            job = ask(base, payload); write_json(response_path, job)
        report = job.get('report') or {}
        spent_input += (report.get('usage') or {}).get('input_tokens', 0)
        text, refs, quotes = answer_view(report)
        row = {'qa_id': qid, 'question': q['question'], 'question_type': q['question_type'], 'split': q['split'],
               'qa_pattern': q.get('qa_pattern'), 'difficulty': q['difficulty'], 'job_state': job['state'],
               'service_status': report.get('status', job['state']), 'seconds': report.get('seconds'),
               'usage': report.get('usage'), 'gold_answer': q['gold_answer'], 'answer': text, 'cited': refs}
        judgment_path = out / 'responses' / f'{qid}.judgment.json'
        if args.no_judge or job['state'] != 'completed' or not text:
            row.update({'verdict': 'fail' if not args.no_judge else 'unjudged', 'facts_met': 0,
                        'facts_total': len(q['required_facts']), 'facts': [], 'contradiction': False,
                        'abstained': not text})
        else:
            if judgment_path.exists():
                judgment = read_json(judgment_path)
            else:
                judgment = judge(q, text, quotes, args.judge_model); write_json(judgment_path, judgment)
            row.update(judgment)
        rows.append(row)
        print(f"   {row['service_status']:<9} {row['verdict']:<8} facts {row['facts_met']}/{row['facts_total']}",
              flush=True)
        with (out / 'results.jsonl').open('w', encoding='utf-8') as stream:
            for r in rows: stream.write(json.dumps(r, ensure_ascii=False) + '\n')
    if summarize and rows:
        write_json(out / 'summary.json', summarize(rows))
        with (out / 'results.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['qa_id', 'type', 'split', 'pattern', 'service_status', 'verdict', 'facts_met',
                             'facts_total', 'seconds', 'question', 'gold_answer', 'answer'])
            for r in rows:
                writer.writerow([r['qa_id'], r['question_type'], r['split'], r['qa_pattern'], r['service_status'],
                                 r['verdict'], r['facts_met'], r['facts_total'], r['seconds'], r['question'],
                                 r['gold_answer'], r['answer']])
    print(json.dumps(summarize(rows)['overall'], ensure_ascii=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--base-url', default='http://127.0.0.1:9100')
    p.add_argument('--qa', type=Path, default=ROOT / 'handoff_q008' / 'qa_candidates.jsonl')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--product', default='ABP654')
    p.add_argument('--jurisdiction', default='US')
    p.add_argument('--ids', help='comma-separated qa_id list')
    p.add_argument('--all-scope', action='store_true',
                   help='send every product and agency like the screen does when nothing is narrowed')
    p.add_argument('--max-input-tokens', type=int, default=0,
                   help='stop before the next new question once service input tokens reach this total')
    p.add_argument('--document-key', help='pin the request to these document keys of /api/summary (e.g. d34), '
                   'like selecting the document on the screen; comma-separated')
    p.add_argument('--sample', action='store_true', help='one question per type plus one complex table')
    p.add_argument('--split', choices=['dev', 'test'])
    p.add_argument('--judge-model', help='override the judge model (default: OPENAI_MODEL from .env)')
    p.add_argument('--no-judge', action='store_true', help='only collect answers')
    run(p.parse_args())
